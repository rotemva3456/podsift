// Settings → Worth hearing: which shows to watch and the daily cap. The daily token cost
// recomputes on its own, debounced, whenever the switch is on and the shows or the cap change
// (shown before Save, not behind a click).
import {useEffect, useState} from 'react'
import {useTranslation} from 'react-i18next'
import {useMutation, useQuery, useQueryClient} from '@tanstack/react-query'
import {LoaderCircle} from 'lucide-react'
import {Button} from '../../../components/ui/button'
import {Checkbox} from '../../../components/ui/checkbox'
import {Input} from '../../../components/ui/input'
import {plainText, type Show} from '../../../utils/listening'
import {podfetch} from '../../shared/podfetch'
import {
    estimateWorthSettings, getWorthSettings, saveWorthSettings, type WorthDraft,
    type WorthSettings as Saved, WORTH_SETTINGS_KEY,
} from './api'

const MIN_CAP = 1, MAX_CAP = 50
const ESTIMATE_DEBOUNCE_MS = 500

async function getJson<T>(path: string): Promise<T> {
    const response = await podfetch(path)
    if (!response.ok) throw new Error(`PodFetch answered ${response.status}`)
    return response.json()
}

/** The value, ``delay`` ms after it last changed. utils/useDebounce.ts drives its callback from a
 * useMemo (a render-phase computation), which does not reliably re-fire on a later dependency
 * change (confirmed by reproduction; the fix belongs in useDebounce.ts, not here) -- a plain effect does. */
function useDebouncedValue<T>(value: T, delay: number): T {
    const [debounced, setDebounced] = useState(value)
    useEffect(() => {
        const timer = setTimeout(() => setDebounced(value), delay)
        return () => clearTimeout(timer)
    }, [value, delay])
    return debounced
}

export function WorthSettings() {
    const {t} = useTranslation('worth')
    const settings = useQuery({queryKey: WORTH_SETTINGS_KEY, queryFn: getWorthSettings, retry: false})
    const shows = useQuery({queryKey: ['worth-shows'], queryFn: () => getJson<Show[]>('/api/v1/podcasts')})
    if (settings.isLoading) return <p role="status" className="worth-muted">{t('loading')}</p>
    if (settings.isError) return <div role="alert" className="worth-state">
        <p>{t('load-error')}</p>
        <Button variant="outline" onClick={() => void settings.refetch()}>{t('try-again')}</Button>
    </div>
    return <Form saved={settings.data!} shows={shows.data ?? []} showsError={shows.isError}/>
}

function isValidCap(value: number): boolean {
    return Number.isInteger(value) && value >= MIN_CAP && value <= MAX_CAP
}

function Form({saved, shows, showsError}: {saved: Saved; shows: Show[]; showsError: boolean}) {
    const {t} = useTranslation('worth')
    const client = useQueryClient()
    const [enabled, setEnabled] = useState(saved.enabled)
    const [picked, setPicked] = useState<string[]>(saved.show_ids)
    const [cap, setCap] = useState(String(saved.daily_cap))
    // What the cost estimate reacts to, debounced so every keystroke/click doesn't fire a request.
    const debouncedPicked = useDebouncedValue(picked, ESTIMATE_DEBOUNCE_MS)
    const debouncedCap = useDebouncedValue(cap, ESTIMATE_DEBOUNCE_MS)

    const capNumber = Number(cap)
    const capOk = isValidCap(capNumber)
    const dirty = enabled !== saved.enabled || picked.join(',') !== saved.show_ids.join(',') || cap !== String(saved.daily_cap)
    const draft = (): WorthDraft => ({enabled, show_ids: picked, daily_cap: capOk ? capNumber : saved.daily_cap})

    const debouncedCapNumber = Number(debouncedCap)
    const canEstimate = enabled && debouncedPicked.length > 0 && isValidCap(debouncedCapNumber)
    const estimate = useQuery({
        queryKey: ['worth-estimate', debouncedPicked, debouncedCapNumber],
        queryFn: () => estimateWorthSettings({enabled: true, show_ids: debouncedPicked, daily_cap: debouncedCapNumber}),
        enabled: canEstimate, staleTime: 30_000,
    })

    const save = useMutation({
        mutationFn: saveWorthSettings,
        onSuccess: result => client.setQueryData(WORTH_SETTINGS_KEY, result),
    })
    const error = save.error ?? (canEstimate ? estimate.error : null)

    const toggleShow = (id: string, on: boolean) => {
        setPicked(on ? [...picked, id] : picked.filter(other => other !== id))
    }

    return <div className="worth-settings">
        <label className="worth-toggle">
            <Checkbox checked={enabled} disabled={saved.login_blocks_auto}
                onCheckedChange={value => setEnabled(value === true)}/>
            <span>{t('enable-label')}</span>
        </label>
        {saved.login_blocks_auto ? <p className="worth-muted" role="status">{t('login-required')}</p> : <>
            <p className="worth-muted">{t('enable-hint')}</p>
            {enabled && <>
                <h3>{t('shows-heading')}</h3>
                {showsError ? <p role="alert" className="worth-error">{t('shows-error')}</p>
                    : !shows.length ? <p className="worth-muted">{t('no-shows')}</p>
                    : <ul className="worth-show-list">{shows.map(show => <li key={show.id}>
                        <label>
                            <Checkbox checked={picked.includes(show.id)} onCheckedChange={value => toggleShow(show.id, value === true)}/>
                            <span>{plainText(show.name)}</span>
                        </label>
                    </li>)}</ul>}
                <h3>{t('cap-heading')}</h3>
                <p className="worth-muted">{t('cap-hint')}</p>
                <Input type="number" min={MIN_CAP} max={MAX_CAP} value={cap} className="worth-cap-input"
                    onChange={event => setCap(event.target.value)} aria-label={t('cap-heading')}/>
                {!capOk && <p role="alert" className="worth-error">{t('cap-invalid', {min: MIN_CAP, max: MAX_CAP})}</p>}
            </>}
            {error && <p role="alert" className="worth-error">{error.message}</p>}
            <div className="worth-actions">
                <Button disabled={!capOk || !dirty || save.isPending} onClick={() => save.mutate(draft())}>
                    {save.isPending && <LoaderCircle className="animate-spin"/>}{t('save')}</Button>
                {canEstimate && (estimate.data
                    ? <span className="worth-muted" role="status">{estimate.data.count
                        ? t('daily-estimate', {tokens: estimate.data.input_tokens.toLocaleString()})
                        : t('estimate-none')}</span>
                    : estimate.isFetching && <span className="worth-muted" role="status">{t('estimating')}</span>)}
                {save.isSuccess && !dirty && <span role="status" className="worth-muted">{t('saved')}</span>}
            </div>
        </>}
    </div>
}
