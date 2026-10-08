// Settings → AI: pick a provider, paste a key, pick a model, Test, Save. The key is write-only.
import {useEffect, useId, useState} from 'react'
import {Trans, useTranslation} from 'react-i18next'
import {useMutation, useQuery, useQueryClient} from '@tanstack/react-query'
import {CircleAlert, CircleCheck, ExternalLink, Info} from 'lucide-react'
import {Button} from '../../../components/ui/button'
import {Input} from '../../../components/ui/input'
import {getConfigFromHtmlFile} from '../../../utils/config'
import {
    type AiDraft, type AiSettings as Saved, getAiSettings, listModels, type ProviderId, saveAiSettings,
    SETTINGS_KEY, testAiSettings,
} from './api'

type Form = {provider: ProviderId; baseUrl: string; model: string; limit: string; key: string; clearKey: boolean}

const MIN_LIMIT = 1_000, MAX_LIMIT = 2_000_000
// React escapes text itself; i18next's own escaping would show a model id like a/b as a&#x2F;b.
const raw = {interpolation: {escapeValue: false}}
const fromSaved = (saved: Saved): Form => ({provider: saved.provider, baseUrl: saved.base_url, model: saved.model,
    limit: String(saved.max_input_chars), key: '', clearKey: false})
const originOf = (url: string) => {try {return new URL(url.trim()).origin} catch {return ''}}

export function AiSettings() {
    const {t} = useTranslation('ai-settings')
    const settings = useQuery({queryKey: SETTINGS_KEY, queryFn: getAiSettings, retry: false})
    if (settings.isPending) return <p role="status" className="ai-muted">{t('loading')}</p>
    if (settings.isError) return <div role="alert" className="ai-state">
        <p>{t('load-error')} {settings.error.message}</p>
        <Button variant="outline" onClick={() => void settings.refetch()}>{t('try-again')}</Button>
    </div>
    return <AiForm saved={settings.data}/>
}

function AiForm({saved}: {saved: Saved}) {
    const {t} = useTranslation('ai-settings')
    const client = useQueryClient()
    const id = useId()
    const [form, setForm] = useState<Form>(() => fromSaved(saved))
    const [listed, setListed] = useState({baseUrl: saved.base_url, stamp: 0})   // what the model list was asked for
    const locked = new Set(saved.from_env)
    const preset = saved.presets.find(p => p.id === form.provider) ?? saved.presets[saved.presets.length - 1]!
    const keyTyped = form.key.trim() !== ''
    const savedKeyFits = saved.key_set && !form.clearKey && originOf(form.baseUrl) === originOf(saved.base_url)
    const limit = Number(form.limit)
    const limitOk = Number.isInteger(limit) && limit >= MIN_LIMIT && limit <= MAX_LIMIT
    const dirty = keyTyped || form.clearKey || form.provider !== saved.provider || form.baseUrl.trim() !== saved.base_url
        || form.model.trim() !== saved.model || form.limit !== String(saved.max_input_chars)

    const draft = (baseUrl = form.baseUrl): AiDraft => ({
        ...(locked.has('base_url') ? {} : {provider: form.provider, base_url: baseUrl.trim()}),
        ...(locked.has('model') ? {} : {model: form.model.trim()}),
        ...(limitOk ? {max_input_chars: limit} : {}),
        ...(keyTyped && !locked.has('api_key') ? {api_key: form.key.trim()} : {}),
        ...(form.clearKey ? {clear_key: true} : {}),
    })
    const canList = listed.baseUrl.trim() !== '' && (keyTyped || savedKeyFits || !preset.needs_key || locked.has('api_key'))
    const models = useQuery({
        // The key itself never goes into a query key; `stamp` changes when a typed key is committed.
        queryKey: ['ai-settings', 'models', form.provider, listed.baseUrl, listed.stamp, form.clearKey],
        queryFn: () => listModels(draft(listed.baseUrl)), enabled: canList, retry: false, staleTime: 5 * 60_000,
    })
    const suggested = models.data?.suggested
    const modelLocked = locked.has('model')
    useEffect(() => {   // pre-fill the provider's suggested model once the list shows it exists
        if (suggested && !modelLocked) setForm(f => f.model ? f : {...f, model: suggested})
    }, [suggested, modelLocked])

    const test = useMutation({mutationFn: () => testAiSettings(draft())})
    const save = useMutation({
        mutationFn: () => saveAiSettings(draft()),
        onSuccess: result => {
            client.setQueryData(SETTINGS_KEY, result)
            // Everything that asked "is AI ready?" asks again (Ask, cuts, briefs).
            void client.invalidateQueries({predicate: q => q.queryKey[0] !== 'ai-settings'
                && q.queryKey.some(part => part === 'ai-settings' || part === 'answer-service')})
            setForm(fromSaved(result))
            setListed(l => ({...l, baseUrl: result.base_url}))
        },
    })
    const edit = (change: Partial<Form>) => {setForm(f => ({...f, ...change})); save.reset(); test.reset()}
    const commitKey = () => {if (keyTyped) setListed(l => ({...l, stamp: l.stamp + 1}))}
    const choose = (provider: ProviderId) => {
        if (provider === form.provider) return
        const next = saved.presets.find(p => p.id === provider)!
        const reset = provider === saved.provider ? fromSaved(saved)
            : {provider, baseUrl: next.base_url, model: '', limit: String(next.max_input_chars), key: '', clearKey: false}
        edit(reset)
        setListed(l => ({baseUrl: reset.baseUrl, stamp: l.stamp + 1}))
    }

    const status = saved.configured
        ? <p className="ai-status" data-on=""><CircleCheck size={16} aria-hidden/> {t('connected', {...raw, provider: saved.presets.find(p => p.id === saved.provider)?.label ?? saved.provider, model: saved.model})}</p>
        : <p className="ai-status"><Info size={16} aria-hidden/> {t('not-connected')}</p>
    const modelHint = locked.has('model') ? t('from-env', {name: 'LLM_MODEL'})
        : !canList ? (preset.needs_key ? t('models-need-key') : t('models-need-address'))
        : models.isLoading ? t('models-loading')
        : models.isError ? t('models-error', {...raw, message: models.error.message})
        : models.data ? (models.data.models.length ? t('models-count', {count: models.data.models.length}) : t('models-none'))
        : ''
    const testOutcome = test.isError ? {tone: 'bad', text: test.error.message}
        : test.data ? {tone: test.data.ok ? 'good' : test.data.needs === 'model' ? 'info' : 'bad', text: test.data.message}
        : null

    return <section className="ai-settings" aria-labelledby={`${id}-title`}>
        <h2 id={`${id}-title`}>{t('title')}</h2>
        <p className="ai-muted">{t('intro')}</p>
        {status}
        {saved.problem && <p role="alert" className="ai-problem"><CircleAlert size={16} aria-hidden/> {saved.problem}</p>}

        <fieldset className="ai-providers" disabled={locked.has('base_url')}>
            <legend>{t('provider')}</legend>
            <div className="ai-chips">{saved.presets.map(option => <label key={option.id} data-checked={option.id === form.provider || undefined}>
                <input type="radio" name={`${id}-provider`} value={option.id} checked={option.id === form.provider}
                    onChange={() => choose(option.id)}/>
                {option.label}
            </label>)}</div>
            <p className="ai-hint">{locked.has('base_url') ? t('from-env', {name: 'LLM_BASE_URL'}) : t(`about-${form.provider}`)}</p>
        </fieldset>

        <div className="ai-field">
            <label htmlFor={`${id}-key`}>{preset.needs_key ? t('key') : t('key-optional')}</label>
            {locked.has('api_key')
                ? <p className="ai-hint">{t('from-env', {name: 'LLM_API_KEY'})}{saved.key_hint ? ' ' + t('key-ends', {...raw, hint: saved.key_hint}) : ''}</p>
                : <Input id={`${id}-key`} type="password" autoComplete="off" spellCheck={false} value={form.key}
                    placeholder={savedKeyFits ? t('key-saved', {...raw, hint: saved.key_hint ?? '····'}) : t('key-placeholder', {...raw, provider: preset.label})}
                    aria-describedby={`${id}-key-hint`}
                    onChange={event => edit({key: event.target.value, clearKey: false})} onBlur={commitKey}
                    onKeyDown={event => {if (event.key === 'Enter') commitKey()}}/>}
            <div id={`${id}-key-hint`} className="ai-hint ai-row">
                {!locked.has('api_key') && savedKeyFits && !keyTyped && <span>{t('key-replace')}</span>}
                {!locked.has('api_key') && form.clearKey && <span>{t('key-will-forget')}</span>}
                {!locked.has('api_key') && saved.key_set && !keyTyped && <Button variant="link" size="sm" type="button"
                    onClick={() => edit({clearKey: !form.clearKey})}>{form.clearKey ? t('key-keep') : t('key-forget')}</Button>}
                {preset.key_url && <a href={preset.key_url} target="_blank" rel="noreferrer noopener">{t('key-get', {...raw, provider: preset.label})} <ExternalLink size={12} aria-hidden/></a>}
            </div>
        </div>

        <div className="ai-field">
            <label htmlFor={`${id}-base`}>{t('base-url')}</label>
            <Input id={`${id}-base`} type="url" inputMode="url" spellCheck={false} value={form.baseUrl}
                disabled={locked.has('base_url')} placeholder="https://…/v1"
                onChange={event => edit({baseUrl: event.target.value})}
                onBlur={() => setListed(l => l.baseUrl === form.baseUrl ? l : {baseUrl: form.baseUrl, stamp: l.stamp + 1})}/>
            <p className="ai-hint">{locked.has('base_url') ? t('from-env', {name: 'LLM_BASE_URL'})
                : form.provider === 'ollama' ? t('base-url-ollama') : form.provider === 'custom' ? t('base-url-custom') : t('base-url-preset')}</p>
        </div>

        <div className="ai-field">
            <label htmlFor={`${id}-model`}>{t('model')}</label>
            <Input id={`${id}-model`} list={`${id}-models`} spellCheck={false} autoComplete="off" value={form.model}
                disabled={locked.has('model')} placeholder={t('model-placeholder')}
                onChange={event => edit({model: event.target.value})}/>
            <datalist id={`${id}-models`}>{models.data?.models.map(model => <option key={model.id} value={model.id}/>)}</datalist>
            <p className="ai-hint" role="status">{modelHint}</p>
        </div>

        <div className="ai-field">
            <label htmlFor={`${id}-limit`}>{t('limit')}</label>
            <Input id={`${id}-limit`} className="ai-limit" type="number" inputMode="numeric" min={MIN_LIMIT} max={MAX_LIMIT}
                step={1000} value={form.limit} aria-invalid={!limitOk || undefined} aria-describedby={`${id}-limit-hint`}
                onChange={event => edit({limit: event.target.value})}/>
            <p id={`${id}-limit-hint`} className={limitOk ? 'ai-hint' : 'ai-hint ai-bad'}>
                {limitOk ? t('limit-hint') : t('limit-invalid')}</p>
        </div>

        <div className="ai-actions">
            <Button type="button" variant="outline" className="ai-button" focusableWhenDisabled disabled={test.isPending || !limitOk}
                onClick={() => test.mutate()}>
                {test.isPending ? t('testing') : t('test')}</Button>
            <Button type="button" className="ai-button" focusableWhenDisabled disabled={!dirty || save.isPending || !limitOk}
                onClick={() => save.mutate()}>
                {save.isPending ? t('saving') : t('save')}</Button>
        </div>
        <div aria-live="polite">
            {testOutcome && <p className="ai-outcome" data-tone={testOutcome.tone}>
                {testOutcome.tone === 'good' ? <CircleCheck size={16} aria-hidden/> : testOutcome.tone === 'bad' ? <CircleAlert size={16} aria-hidden/> : <Info size={16} aria-hidden/>}
                {testOutcome.text}</p>}
            {save.isSuccess && !dirty && <p className="ai-outcome" data-tone="good"><CircleCheck size={16} aria-hidden/> {t('saved')}</p>}
            {save.isError && <p className="ai-outcome" data-tone="bad"><CircleAlert size={16} aria-hidden/> {save.error.message}</p>}
        </div>

        <Transcription/>
    </section>
}

function Transcription() {
    const {t} = useTranslation('ai-settings')
    const on = getConfigFromHtmlFile()?.transcriptionEnabled ?? false
    return <section className="ai-transcription" aria-label={t('transcription')}>
        <h3>{t('transcription')}</h3>
        <p className="ai-status" data-on={on || undefined}>{on ? <CircleCheck size={16} aria-hidden/> : <Info size={16} aria-hidden/>} {on ? t('transcription-on') : t('transcription-off')}</p>
        <p className="ai-hint"><Trans t={t} i18nKey={on ? 'transcription-how-on' : 'transcription-how-off'} components={{code: <code/>}}/></p>
    </section>
}
