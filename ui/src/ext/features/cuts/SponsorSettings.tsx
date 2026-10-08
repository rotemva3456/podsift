// Settings → Sponsors: existing PodFetch per-user preferences control our native transcript skips.
// The storage endpoint's historical name does not involve SponsorBlock's API or data.
import {useTranslation} from 'react-i18next'
import {useMutation, useQuery, useQueryClient} from '@tanstack/react-query'
import {LoaderCircle} from 'lucide-react'
import {Button} from '../../../components/ui/button'
import {Checkbox} from '../../../components/ui/checkbox'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../../../components/ui/select'
import {getSkipPreferences, getSponsorSettings, putSkipPreferences, putSponsorSettings, SKIP_PREFERENCES_KEY,
    SPONSOR_SETTINGS_KEY, type SponsorSettings as Settings} from './api'
import {CATEGORIES} from './sponsors'

export function SponsorSettings() {
    const {t} = useTranslation('cuts')
    const client = useQueryClient()
    const settings = useQuery({queryKey: SPONSOR_SETTINGS_KEY, queryFn: getSponsorSettings, retry: false})
    const preferences = useQuery({queryKey: SKIP_PREFERENCES_KEY, queryFn: getSkipPreferences, retry: false})
    const save = useMutation({mutationFn: putSponsorSettings, onSuccess: saved => client.setQueryData(SPONSOR_SETTINGS_KEY, saved)})
    const savePreferences = useMutation({mutationFn: putSkipPreferences, onSuccess: saved => client.setQueryData(SKIP_PREFERENCES_KEY, saved)})
    if (settings.isPending || preferences.isPending) return <p role="status" className="cut-hint"><LoaderCircle size={14} className="animate-spin inline"/> {t('loading')}</p>
    if (settings.isError || preferences.isError) return <div className="cut-state" role="alert"><h3>{t('sponsors-load-error')}</h3>
        <Button variant="outline" onClick={() => {void settings.refetch(); void preferences.refetch()}}>{t('try-again')}</Button></div>
    const current = save.isPending && save.variables ? save.variables : settings.data
    const choices = savePreferences.isPending && savePreferences.variables ? savePreferences.variables : preferences.data
    const saving = save.isPending || savePreferences.isPending
    const modeItems = [{value: 'auto', label: t('skip-mode-auto')}, {value: 'manual', label: t('skip-mode-manual')}]
    const minimumItems = [...new Set([0, 2, 5, 10, 30, 60, choices.minimum_seconds])].sort((a, b) => a - b)
        .map(seconds => ({value: String(seconds), label: seconds ? t('minimum-skip-seconds', {count: seconds}) : t('minimum-skip-none')}))
    const change = (patch: Partial<Settings>) => save.mutate({...current, ...patch})
    const manualChoice = (category: string, manual: boolean) => savePreferences.mutate({...choices,
        manual_categories: manual ? [...choices.manual_categories.filter(c => c !== category), category]
            : choices.manual_categories.filter(c => c !== category)})
    return <section className="cut-settings" aria-labelledby="cut-sponsors-title">
        <h2 id="cut-sponsors-title">{t('sponsors-title')}</h2>
        <p className="cut-intro">{t('sponsors-intro')}</p>
        <label className="cut-check cut-master" htmlFor="cut-sponsors-enabled">
            <Checkbox id="cut-sponsors-enabled" checked={current.enabled} disabled={saving} onCheckedChange={checked => change({enabled: checked === true})}/>
            {t('sponsors-switch')}</label>
        <fieldset className="cut-categories" disabled={!current.enabled || saving}>
            <legend>{t('sponsors-categories')}</legend>
            {CATEGORIES.map(([category, setting]) => <div key={category} className="cut-category-row">
                <label className="cut-check" htmlFor={`cut-category-${category}`}>
                <Checkbox id={`cut-category-${category}`} checked={current[setting]} disabled={!current.enabled || saving}
                    onCheckedChange={checked => change({[setting]: checked === true})}/>
                <span>{t(`category-${category}`)}<small>{t(`category-${category}-hint`)}</small></span></label>
                <Select value={choices.manual_categories.includes(category) ? 'manual' : 'auto'} items={modeItems}
                    disabled={!current.enabled || !current[setting] || saving}
                    onValueChange={value => {if (value) manualChoice(category, value === 'manual')}}>
                    <SelectTrigger size="sm" aria-label={t('category-skip-mode', {category: t(`category-${category}`)})}><SelectValue/></SelectTrigger>
                    <SelectContent>{modeItems.map(item => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}</SelectContent>
                </Select>
            </div>)}
        </fieldset>
        <div className="cut-minimum">
            <label id="cut-minimum-label">{t('minimum-skip-length')}</label>
            <Select value={String(choices.minimum_seconds)} items={minimumItems} disabled={!current.enabled || saving}
                onValueChange={value => {if (value !== null) savePreferences.mutate({...choices, minimum_seconds: Number(value)})}}>
                <SelectTrigger aria-labelledby="cut-minimum-label"><SelectValue/></SelectTrigger>
                <SelectContent>{minimumItems.map(item => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}</SelectContent>
            </Select>
        </div>
        <p className="cut-hint">{t('sponsors-timing-help')}</p>
        <p role="status" className="cut-hint">{saving ? t('sponsors-saving') : save.isError || savePreferences.isError ? ''
            : save.isSuccess || savePreferences.isSuccess ? t('sponsors-saved') : ''}</p>
        {(save.isError || savePreferences.isError) && <p role="alert" className="cut-error">{t('sponsors-save-error')}</p>}
    </section>
}
