// "Only the parts about X, I have 15 minutes, skip Y": the form, the preview, Smart Play and the
// MP3 export. The episode's Cut tab and the queue page (/queue/cut) both use it.
import {type FormEvent, useEffect, useRef, useState} from 'react'
import {Trans, useTranslation} from 'react-i18next'
import {Link} from 'react-router-dom'
import {useMutation, useQueries, useQuery, useQueryClient} from '@tanstack/react-query'
import {Download, FileAudio, List, LoaderCircle, Play, Scissors, Square, Undo2, X} from 'lucide-react'
import {Button} from '../../../components/ui/button'
import {Checkbox} from '../../../components/ui/checkbox'
import {Input} from '../../../components/ui/input'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../../../components/ui/select'
import useAudioPlayer from '../../../store/AudioPlayerSlice'
import {clock, fetchEpisode} from '../../../utils/listening'
import {aiReady, cancelJob, createPlan, cutFile, getAiSettings, getCut, getJob, getPlan, getScript, listPlans, startRender, transcriptQuery, updatePlan,
    type Mode, type LearningMode, type Plan, type PlanRequest} from './api'
import {keptSeconds, shortMinutes, skipPlans, startSmartPlay} from './smartPlay'
import {useDownload} from './useDownload'
import {MakeFromFile} from './MakeFromFile'
import {ScriptView} from './ScriptView'
import {CheckResult} from './CheckResult'
import {LearningModePicker, learningModes} from './LearningModePicker'

export type CutTarget = {kind: 'episode'; episodeId: string} | {kind: 'queue'; episodeIds: string[]}
type Form = {want: string; skip: string; minutes: string; skipAds: boolean; mode: Mode; learningMode: LearningMode}
export type CutWorkspaceProps = {target: CutTarget; titles?: Record<string, string>; onPlay: (episodeId: string, seconds: number) => void
    initialPlanId?: string; onPlanChange?: (id: string) => void}

export const JOB_POLL_MS = 1000
const DEFAULT_FORM: Form = {want: '', skip: '', minutes: '15', skipAds: true, mode: 'keyword', learningMode: 'balanced'}
/** Each workspace's form, plan and export, so switching tabs or pages doesn't lose them. */
const saved = new Map<string, {form: Form; planId?: string; jobId?: string; request?: PlanRequest}>()
/** Forget every workspace's form and plan (tests start clean with this). */
export const forgetWorkspaces = () => saved.clear()
const BUDGET = /budget|minute|fit|limit/i
/** React escapes text itself; i18next must not escape it again ("BGP's" would show as BGP&#39;s). */
const raw = {interpolation: {escapeValue: false}}

export function CutWorkspace({target, titles = {}, onPlay, initialPlanId, onPlanChange}: CutWorkspaceProps) {
    const {t} = useTranslation('cuts')
    const key = target.kind === 'episode' ? `episode:${target.episodeId}` : 'queue'
    const [form, setForm] = useState<Form>(() => saved.get(key)?.form ?? DEFAULT_FORM)
    const [planId, setPlanId] = useState(() => initialPlanId || saved.get(key)?.planId)
    const [jobId, setJobId] = useState(() => saved.get(key)?.jobId)
    const [request, setRequest] = useState(() => saved.get(key)?.request)
    const appliedInitialPlan = useRef(initialPlanId)
    const collapsedPlan = useRef<string | undefined>(initialPlanId)
    const [planningOpen, setPlanningOpen] = useState(() => !planId)
    useEffect(() => {saved.set(key, {form, planId, jobId, request})}, [key, form, planId, jobId, request])
    // URL/query state may arrive after the workspace mounts. Treat it as an input, so echoing it
    // through onPlanChange cannot create a router update loop.
    useEffect(() => {
        if (initialPlanId && initialPlanId !== appliedInitialPlan.current) {
            appliedInitialPlan.current = initialPlanId
            setPlanId(initialPlanId); setJobId(undefined)
        }
    }, [initialPlanId])
    const client = useQueryClient()
    const ai = useQuery({queryKey: ['cuts', 'ai-settings'], queryFn: getAiSettings, retry: false, staleTime: 60_000})
    const canUseAi = aiReady(ai.data)
    const mode: Mode = canUseAi && (form.mode === 'ai' || form.learningMode !== 'balanced') ? 'ai' : 'keyword'
    const plan = useQuery({queryKey: ['cuts', 'plan', planId], queryFn: () => getPlan(planId!), enabled: !!planId, staleTime: Infinity, retry: false})
    const appliedEffortPlan = useRef<string | undefined>(undefined)
    useEffect(() => {
        if (plan.data && appliedEffortPlan.current !== plan.data.id) {
            appliedEffortPlan.current = plan.data.id
            const learningMode = plan.data.learning_mode ?? 'balanced'
            setForm(previous => ({...previous, learningMode}))
        }
    }, [plan.data])
    const openPlan = (id: string, notify = true) => {
        if (id === planId) return
        setPlanId(id); setJobId(undefined)
        if (notify) onPlanChange?.(id)
    }
    const keepPlan = (next: Plan) => {
        client.setQueryData(['cuts', 'plan', next.id], next)
        setJobId(undefined)
        if (next.id !== planId) openPlan(next.id)
    }
    // AI planning can take minutes; it can be cancelled, and leaving the page cancels it.
    const planning = useRef<AbortController | null>(null)
    useEffect(() => () => planning.current?.abort(), [])
    const create = useMutation({onSuccess: keepPlan, mutationFn: (next: PlanRequest) => {
        planning.current?.abort()
        planning.current = new AbortController()
        return createPlan(next, planning.current.signal)
    }})
    const cancelled = create.error?.name === 'AbortError'
    const toggle = useMutation({mutationFn: ({id, enabled}: {id: string; enabled: boolean}) => updatePlan(planId!, {spans: [{id, enabled}]}), onSuccess: keepPlan})
    const find = (next: PlanRequest) => {setRequest(next); create.mutate(next)}
    const submit = (event: FormEvent) => {
        event.preventDefault()
        const want = form.want.trim(), minutes = Number(form.minutes)
        if (!want || create.isPending || (form.learningMode !== 'balanced' && !canUseAi)) return
        find({...(target.kind === 'queue' ? {source: 'queue' as const} : {episode_ids: [target.episodeId]}), want,
            ...(form.skip.trim() ? {skip: form.skip.trim()} : {}), ...(minutes > 0 ? {minutes} : {}), skip_ads: form.skipAds, mode,
            ...(form.learningMode !== 'balanced' ? {learning_mode: form.learningMode} : {})})
    }
    const edit = (change: Partial<Form>) => setForm(value => ({...value, ...change}))
    const id = (name: string) => `cut-${name}-${key}`
    const problem = plan.data && target.kind === 'episode' ? planProblem(plan.data, target.episodeId) : null
    const validPlan = Boolean(plan.data && !problem)
    useEffect(() => {
        if (validPlan && plan.data && collapsedPlan.current !== plan.data.id) {
            collapsedPlan.current = plan.data.id
            setPlanningOpen(false)
        } else if (!validPlan && (!planId || plan.isError || problem)) setPlanningOpen(true)
    }, [validPlan, plan.data, planId, plan.isError, problem])

    return <div className="cut-workspace">
        <details className="cut-planning-disclosure" open={planningOpen} onToggle={event => setPlanningOpen(event.currentTarget.open)}>
            <summary><span>{t('saved-plans')} / {t('find')}</span><span className="cut-hint">{t('saved-plans-hint')}</span></summary>
            <div className="cut-planning-controls">
                {target.kind === 'episode' ? <SavedPlans episodeId={target.episodeId} planId={planId} openPlan={openPlan} form={form}/>
                    : <AgentInstructions form={form}/>}
                <form className="cut-form" onSubmit={submit}>
                    <div className="col-span-full"><LearningModePicker value={form.learningMode} onChange={learningMode => edit({learningMode})} disabled={create.isPending}/>
                        {form.learningMode !== 'balanced' && !canUseAi && !ai.isPending && <p className="text-sm text-muted-foreground mt-2">Copy a request for your connected agent above, or <Link to="/settings/ai" className="underline">connect AI</Link> to choose passages by effort.</p>}
                    </div>
                    <label className="cut-field cut-want" htmlFor={id('want')}><span>{t('want')}</span>
                        <Input id={id('want')} value={form.want} placeholder={form.learningMode === 'focus' ? 'For example: BGP, especially the tie-breakers I find confusing' : t('want-placeholder')} maxLength={300} onChange={e => edit({want: e.target.value})}/></label>
                    <label className="cut-field" htmlFor={id('skip')}><span>{t('skip')}</span>
                        <Input id={id('skip')} value={form.skip} placeholder={t('skip-placeholder')} maxLength={300} onChange={e => edit({skip: e.target.value})}/></label>
                    <label className="cut-field cut-minutes" htmlFor={id('minutes')}><span>{t('minutes')}</span>
                        <Input id={id('minutes')} type="number" inputMode="numeric" min={1} max={600} value={form.minutes} onChange={e => edit({minutes: e.target.value})}/></label>
                    <label className="cut-check" htmlFor={id('ads')}>
                        <Checkbox id={id('ads')} checked={form.skipAds} onCheckedChange={checked => edit({skipAds: checked === true})}/>{t('skip-ads')}</label>
                    {form.learningMode === 'balanced' && <fieldset className="cut-mode"><legend>{t('mode')}</legend>
                        <label><input type="radio" name={id('mode')} value="keyword" checked={mode === 'keyword'} onChange={() => edit({mode: 'keyword'})}/>
                            <span>{t('mode-keyword')}<small>{t('mode-keyword-hint')}</small></span></label>
                        <label data-disabled={!canUseAi || undefined}><input type="radio" name={id('mode')} value="ai" checked={mode === 'ai'} disabled={!canUseAi} onChange={() => edit({mode: 'ai'})}/>
                            <span>{t('mode-ai')}<small>{t('mode-ai-hint')}</small></span></label>
                        {!canUseAi && !ai.isPending && <p className="cut-hint"><Trans t={t} i18nKey="ai-off" components={{settings: <Link to="/settings/ai"/>}}/></p>}
                    </fieldset>}
                    <Button type="submit" className="cut-submit" disabled={!form.want.trim() || create.isPending || (form.learningMode !== 'balanced' && !canUseAi)}>
                        {create.isPending ? <LoaderCircle className="animate-spin"/> : <Scissors/>}{t(create.isPending ? 'finding' : 'find')}</Button>
                </form>
                {create.isPending && create.variables?.mode === 'ai' && <div role="status" className="cut-planning">
                    <p><LoaderCircle size={14} className="animate-spin inline"/> {t('planning-ai')}</p>
                    <Button variant="outline" size="sm" onClick={() => planning.current?.abort()}>{t('cancel')}</Button></div>}
                {create.isError && !cancelled && <p role="alert" className="cut-error">{t('plan-error', {message: create.error.message, ...raw})}</p>}
                {cancelled && <p role="status" className="cut-hint">{t('planning-cancelled')}</p>}
            </div>
        </details>
        {planId && plan.isPending && <p role="status" className="cut-hint"><LoaderCircle size={14} className="animate-spin inline"/> {t('loading')}</p>}
        {plan.isError && <p role="alert" className="cut-error">{plan.error.message} <Button variant="link" onClick={() => void plan.refetch()}>{t('try-again')}</Button></p>}
        {problem && <p role="alert" className="cut-error">{t(problem)}</p>}
        {plan.data && !problem &&
            <PlanView plan={plan.data} target={target} titles={titles} onPlay={onPlay} jobId={jobId} setJobId={setJobId}
            toggle={(span, enabled) => toggle.mutate({id: span, enabled})} toggleError={toggle.isError} busy={toggle.isPending}
            findAgain={() => request && find(request)}/>}
    </div>
}

const planOrigin = (plan: Plan) => plan.mode === 'agent' ? 'Selected passages' : plan.mode === 'ai' ? 'AI' : 'Keywords'
const planLabel = (plan: Plan) => `${plan.want} · ${plan.learning_mode && plan.learning_mode !== 'balanced' ? `${learningModes[plan.learning_mode].label} · ` : ''}${planOrigin(plan)}`

function SavedPlans({episodeId, planId, openPlan, form}: {episodeId: string; planId?: string; openPlan: (id: string) => void; form: Form}) {
    const {t} = useTranslation('cuts')
    const plans = useQuery({queryKey: ['cuts', 'plans', episodeId], queryFn: () => listPlans(episodeId), retry: false})
    const [selected, setSelected] = useState('')
    useEffect(() => {if (planId) setSelected(planId)}, [planId])
    const items = Object.fromEntries((plans.data ?? []).map(plan => [plan.id, planLabel(plan)]))
    return <section className="cut-saved" aria-label={t('saved-plans')}>
        <div><h3>{t('saved-plans')}</h3><p className="cut-hint">{t('saved-plans-hint')}</p></div>
        {plans.isPending ? <p role="status" className="cut-hint"><LoaderCircle size={14} className="animate-spin inline"/> {t('plans-loading')}</p>
        : plans.isError ? <p role="alert" className="cut-error">{t('plans-error')} <Button variant="link" onClick={() => void plans.refetch()}>{t('try-again')}</Button></p>
        : plans.data.length ? <div className="cut-saved-controls">
            <Select value={selected || null} items={items} onValueChange={value => setSelected(value ?? '')}>
                <SelectTrigger aria-label={t('saved-plan-select')}><SelectValue placeholder={t('saved-plan-placeholder')}/></SelectTrigger>
                <SelectContent>{plans.data.map(savedPlan => <SelectItem key={savedPlan.id} value={savedPlan.id}>{planLabel(savedPlan)}</SelectItem>)}</SelectContent>
            </Select>
            <Button type="button" variant="outline" disabled={!selected || selected === planId} onClick={() => openPlan(selected)}>{t('open-plan')}</Button>
            <Button type="button" variant="ghost" onClick={() => void plans.refetch()}>{t('reload-plans')}</Button>
        </div> : <p className="cut-hint">{t('plans-empty')}</p>}
        <AgentInstructions episodeId={episodeId} form={form}/>
    </section>
}

function AgentInstructions({episodeId, form}: {episodeId?: string; form: Form}) {
    const {t} = useTranslation('cuts')
    const [copied, setCopied] = useState(false)
    const [copyError, setCopyError] = useState(false)
    useEffect(() => {setCopied(false); setCopyError(false)}, [form])
    const copy = async () => {
        setCopyError(false)
        try {
            if (!navigator.clipboard?.writeText) throw new Error('clipboard-unavailable')
            const source = episodeId ? t('agent-instructions-copy', {episodeId}) : 'Read my Listen next queue and its transcripts. Create a version-bound listen from your chosen passages and return the saved plan ID.'
            await navigator.clipboard.writeText(`${source}\nUse learning_mode=${form.learningMode}. ${learningModes[form.learningMode].instruction} Effort is separate from importance. Use my stated learning context; do not infer mastery from playback.\n${form.want.trim() ? `My goal: ${form.want.trim()}\n` : ''}${form.skip.trim() ? `Skip: ${form.skip.trim()}\n` : ''}${Number(form.minutes) > 0 ? `Time budget: ${form.minutes} minutes.\n` : ''}Skip sponsors: ${form.skipAds ? 'yes' : 'no'}.\nUse plan_from_segments with your own choices; explain the effort fit in each reason.`)
            setCopied(true)
        } catch {
            setCopied(false)
            setCopyError(true)
        }
    }
    return <><div className="cut-agent-help"><p className="cut-hint">{t('agent-plan-help')}</p>
            <Button type="button" size="sm" variant="outline" onClick={() => void copy()}>{t(copied ? 'instructions-copied' : 'copy-agent-instructions')}</Button></div>
        {copyError && <p role="alert" className="cut-error">{t('copy-instructions-error')}</p>}
    </>
}

function planProblem(plan: Plan, episodeId: string): 'plan-wrong-source' | 'plan-many-sources' | null {
    const sources = new Set([
        ...(plan.episodes ?? []).map(item => item.episode_id), ...plan.spans.map(item => item.episode_id),
        ...plan.omitted.map(item => item.episode_id), ...plan.needs_timing.map(item => item.episode_id),
    ])
    if (sources.size > 1) return 'plan-many-sources'
    // Old keyword plans did not always persist episode metadata. A genuinely empty result has
    // nothing that can play or export, so it is safe to show its useful "nothing matched" state.
    if (sources.size === 0 && plan.status === 'empty' && plan.spans.length === 0) return null
    if (sources.size !== 1 || !sources.has(episodeId)) return 'plan-wrong-source'
    return null
}

function PlanView({plan, target, titles, onPlay, jobId, setJobId, toggle, toggleError, busy, findAgain}: {
    plan: Plan; target: CutTarget; titles: Record<string, string>; onPlay: CutWorkspaceProps['onPlay']; jobId?: string; setJobId: (id?: string) => void
    toggle: (span: string, enabled: boolean) => void; toggleError: boolean; busy: boolean; findAgain: () => void
}) {
    const {t} = useTranslation('cuts')
    const title = (episodeId: string) => titles[episodeId] || plan.episodes?.find(e => e.episode_id === episodeId)?.title
        || plan.spans.find(span => span.episode_id === episodeId && span.title)?.title || t('episode')
    const plans = skipPlans(plan, plan.want)
    // Timing is checked when the plan says so; without that, a publisher transcript counts as unchecked.
    const transcripts = useQueries({queries: plan.episodes ? [] : plans.map(p => transcriptQuery(p.episodeId))})
    const unchecked = plan.episodes ? plans.some(p => plan.episodes!.find(e => e.episode_id === p.episodeId)?.timing !== 'ok')
        : transcripts.some(query => query.data?.origin === 'feed')
    // The script (every word kept and cut) replaces the passage list once it loads; a server or
    // plan without one keeps the list.
    const version = plan.spans.map(s => `${s.id}:${s.enabled ? 1 : 0}:${s.start}:${s.end}`).join(',')
    const script = useQuery({queryKey: ['cuts', 'script', plan.id, version], queryFn: () => getScript(plan.id), retry: false,
        staleTime: Infinity, enabled: plan.spans.length > 0})
    const words = script.data?.words ? script.data : null
    const overBudget = plan.omitted.filter(item => BUDGET.test(item.reason))
    const otherOmitted = plan.omitted.filter(item => !BUDGET.test(item.reason))
    const enabled = plan.spans.filter(span => span.enabled).length
    const manyEpisodes = new Set(plan.spans.map(span => span.episode_id)).size > 1
    const needsTiming = plan.needs_timing.length > 0 && <div className="cut-state" role="status">
        <h3>{t('needs-timing-title')}</h3><p>{t('needs-timing')}</p>
        <ul>{plan.needs_timing.map(item => <li key={item.episode_id}>{target.kind === 'queue' && <strong>{title(item.episode_id)}: </strong>}{item.reason}</li>)}</ul>
        {plan.needs_timing.map(item => <MakeFromFile key={item.episode_id} episodeId={item.episode_id} onReady={findAgain}
            label={target.kind === 'queue' ? title(item.episode_id) : undefined}/>)}
    </div>
    if (!plan.spans.length && overBudget.length) return <>{needsTiming}<div className="cut-state" role="status">
        <h3>{t('over-budget-title', {minutes: plan.minutes ?? ''})}</h3><p>{t('over-budget-none', {count: overBudget.length})}</p></div></>
    if (plan.status === 'empty' && !plan.spans.length) return <>{needsTiming}<div className="cut-state" role="status">
        <h3>{plan.learning_mode && plan.learning_mode !== 'balanced' ? `No passages fit ${learningModes[plan.learning_mode].label}` : t('empty-title')}</h3><p>{plan.learning_mode && plan.learning_mode !== 'balanced' ? 'Try another source or listening effort. Your original episode is still available.' : t(target.kind === 'queue' ? 'empty-queue' : 'empty', {want: plan.want, ...raw})}</p></div></>
    if (!plan.spans.length) return needsTiming || null
    return <section className="cut-plan" aria-label={t('passage-list')}>
        {needsTiming}
        <div className="cut-plan-head">
            <p className="cut-summary"><strong>{t('summary', {kept: shortMinutes(plan.kept_seconds), total: shortMinutes(plan.source_seconds)})}</strong>
                <span> · {t('passages', {count: enabled})}</span>
                <span className="cut-badge">{planOrigin(plan)}</span>
                {plan.learning_mode && plan.learning_mode !== 'balanced' && <span className="cut-badge">{learningModes[plan.learning_mode].label}</span>}
                {unchecked && <span className="cut-badge" title={t('timing-unchecked-hint')}>{t('timing-unchecked')}</span>}</p>
            {overBudget.length > 0 && <p className="cut-hint">{t('over-budget', {count: overBudget.length, minutes: plan.minutes ?? ''})}</p>}
            {otherOmitted.length > 0 && <p className="cut-hint">{t('omitted', {count: otherOmitted.length,
                reasons: [...new Set(otherOmitted.map(item => item.reason))].join('; '), ...raw})}</p>}
            {unchecked && <p className="cut-hint">{t('timing-unchecked-hint')}</p>}
        </div>
        <SmartPlay plans={plans} titles={titles} queue={target.kind === 'queue'}/>
        <Export plan={plan} jobId={jobId} setJobId={setJobId} onPlay={onPlay} title={title}/>
        {toggleError && <p role="alert" className="cut-error">{t('update-error')}</p>}
        {words ? <ScriptView plan={plan} script={words} title={title} onPlay={onPlay} toggle={toggle} busy={busy}/> :
        <ol className="cut-passages">{plan.spans.map(span => {
            const time = clock(span.start)
            return <li key={span.id} className="cut-passage" data-removed={!span.enabled || undefined}>
                <Button variant="ghost" className="passage-time" aria-label={t('play-passage', {time})} onClick={() => onPlay(span.episode_id, span.start)}>
                    <Play/>{time}–{clock(span.end)}</Button>
                <div className="cut-passage-text">
                    {manyEpisodes && <p className="cut-episode">{title(span.episode_id)}</p>}
                    <p>{span.text}</p>{span.why && <p className="cut-why">{span.why}</p>}
                    {!span.enabled && <p className="cut-why">{t('removed')}</p>}
                </div>
                <Button variant="ghost" size="sm" disabled={busy} aria-label={t(span.enabled ? 'remove-passage' : 'restore-passage', {time})}
                    onClick={() => toggle(span.id, !span.enabled)}>{span.enabled ? <X/> : <Undo2/>}<span className="cut-action-label">{t(span.enabled ? 'remove' : 'restore')}</span></Button>
            </li>
        })}</ol>}
    </section>
}

/** The Smart Play button: only for downloaded episodes, so the skips land in the right place. */
function SmartPlay({plans, titles, queue}: {plans: ReturnType<typeof skipPlans>; titles: Record<string, string>; queue: boolean}) {
    const {t} = useTranslation('cuts')
    const [error, setError] = useState(false)
    const episodes = useQueries({queries: plans.map(p => ({queryKey: ['listen-episode', p.episodeId], queryFn: () => fetchEpisode(p.episodeId), staleTime: 30_000}))})
    const missing = plans.filter((_, index) => episodes[index]?.data && !episodes[index]!.data!.podcastEpisode.status)
    const checking = episodes.some(query => query.isPending)
    const download = useDownload()
    const active = useAudioPlayer(state => !!state.skipPlan && [state.skipPlan, ...state.skipNext].every(p => plans.some(mine =>
        mine.episodeId === p.episodeId && mine.label === p.label && JSON.stringify(mine.keep) === JSON.stringify(p.keep))))
    if (!plans.length) return null
    const start = async () => {
        setError(false)
        try {await startSmartPlay(plans)} catch {setError(true)}
    }
    return <div className="cut-smart">
        {/* One button that turns into "Stop", so keyboard focus stays on it. */}
        <Button variant={active ? 'secondary' : 'default'} disabled={!active && (checking || missing.length > 0)}
            onClick={() => active ? useAudioPlayer.getState().stopSmartPlay() : void start()}>
            {active ? <Square/> : <Play fill="currentColor"/>}{t(active ? 'stop-smart-play' : 'smart-play')}</Button>
        <p className="cut-hint">{active ? t('smart-play-on') : t(queue && plans.length > 1 ? 'smart-play-queue' : 'smart-play-hint')}</p>
        {error && <p role="alert" className="cut-error">{t('smart-play-error')}</p>}
        {missing.length > 0 && <div className="cut-download" role="status">
            <p>{t(queue ? 'download-first-list' : 'download-first')}</p>
            {queue && <ul>{missing.map(p => <li key={p.episodeId}>{titles[p.episodeId] || t('episode')}</li>)}</ul>}
            {download.state === 'not-allowed' ? <p>{t('download-not-allowed')}</p> : <>
                <Button variant="outline" disabled={download.state === 'downloading'} onClick={() => void download.start(missing.map(p => p.episodeId))}>
                    {download.state === 'downloading' ? <LoaderCircle className="animate-spin"/> : <Download/>}
                    {t(download.state === 'downloading' ? 'downloading' : missing.length > 1 ? 'download-all' : 'download')}</Button>
                {download.state === 'error' && <p role="alert" className="cut-error">{t('download-error')}</p>}</>}
        </div>}
    </div>
}

/** Approve and export MP3: a render job with progress (it listens to its own MP3), then the
 *  listen-back check, Download and Show index. */
function Export({plan, jobId, setJobId, onPlay, title}: {plan: Plan; jobId?: string; setJobId: (id?: string) => void
    onPlay: CutWorkspaceProps['onPlay']; title: (episodeId: string) => string}) {
    const {t} = useTranslation('cuts')
    const [index, setIndex] = useState(false)
    const [saveError, setSaveError] = useState(false)
    const render = useMutation({mutationFn: () => startRender(plan.id), onSuccess: ({job_id}) => setJobId(job_id)})
    const job = useQuery({queryKey: ['cuts', 'job', jobId], queryFn: () => getJob(jobId!), enabled: !!jobId, retry: false,
        refetchInterval: query => ['done', 'failed'].includes(query.state.data?.status ?? '') || query.state.error ? false : JOB_POLL_MS})
    const cutId = job.data?.status === 'done' ? job.data.cut_id : null
    const cut = useQuery({queryKey: ['cuts', 'cut', cutId], queryFn: () => getCut(cutId!), enabled: !!cutId, retry: false})
    const again = () => {setJobId(undefined); render.mutate()}
    const cancel = useMutation({mutationFn: () => cancelJob(jobId!), onSettled: () => setJobId(undefined)})
    const save = async () => {
        setSaveError(false)
        try {
            const url = URL.createObjectURL(await cutFile(cutId!))
            const link = Object.assign(document.createElement('a'), {href: url, download: `cut-${plan.want.replace(/[^\p{L}\p{N}]+/gu, '-').slice(0, 40)}.mp3`})
            link.click()
            setTimeout(() => URL.revokeObjectURL(url), 60_000)
        } catch {setSaveError(true)}
    }
    const percent = Math.round((job.data?.progress ?? 0) * 100)
    let body
    if (render.isError) body = <p role="alert" className="cut-error">{t('export-error', {message: render.error.message, ...raw})}
        <Button variant="link" onClick={again}>{t('try-again')}</Button></p>
    else if (job.isError || cut.isError) body = <p role="alert" className="cut-error">{(job.error ?? cut.error)?.message}
        <Button variant="link" onClick={() => void (job.isError ? job.refetch() : cut.refetch())}>{t('try-again')}</Button></p>
    else if (job.data?.status === 'failed') body = <div role="alert" className="cut-error">
        <p>{t('export-failed', {error: job.data.error ?? '', ...raw})} <Button variant="link" onClick={again}>{t('try-again')}</Button></p>
        {job.data.detail && job.data.detail !== job.data.error && <p className="cut-detail">{job.data.detail}</p>}</div>
    else if (render.isPending || (jobId && (!job.data || job.data.status === 'queued' || job.data.status === 'running'))) body = <div role="status" className="cut-progress">
        <p>{t(`stage-${job.data?.stage ?? (job.data?.status === 'queued' ? 'waiting' : 'render')}`, {percent})}</p>
        <div className="cut-progress-row">
            <div role="progressbar" aria-label={t('export-progress')} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><span style={{width: `${percent}%`}}/></div>
            {jobId && <Button variant="ghost" size="sm" disabled={cancel.isPending} onClick={() => cancel.mutate()}>{t('cancel')}</Button>}
        </div>
    </div>
    else if (cut.data) body = <div className="cut-ready" role="status">
        <p>{t('export-ready', {duration: clock(cut.data.duration), size: (cut.data.size_bytes / 1e6).toFixed(1)})}
            {cut.data.label && <> {t('export-timing-unchecked')}</>}</p>
        {cut.data.check && <CheckResult check={cut.data.check} onPlay={onPlay}/>}
        <div className="cut-actions">
            <Button onClick={() => void save()}><Download/>{t('download-mp3')}</Button>
            <Button variant="outline" aria-expanded={index} onClick={() => setIndex(!index)}><List/>{t(index ? 'hide-index' : 'show-index')}</Button>
        </div>
        {saveError && <p role="alert" className="cut-error">{t('download-failed')}</p>}
        {index && <ol className="cut-index">{cut.data.index.map(entry => <li key={`${entry.cut_start}`}>
            {new Set(cut.data!.index.map(item => item.episode_id)).size > 1 && <p><strong>{entry.title || title(entry.episode_id)}</strong></p>}
            <p className="cut-index-where">{t('index-entry', {cutStart: clock(entry.cut_start), cutEnd: clock(entry.cut_end), sourceStart: clock(entry.source_start), sourceEnd: clock(entry.source_end)})}</p>
            <Button variant="ghost" size="sm" aria-label={t('play-source', {time: clock(entry.source_start)})} onClick={() => onPlay(entry.episode_id, entry.source_start)}><Play/>{clock(entry.source_start)}</Button>
        </li>)}</ol>}
    </div>
    return <div className="cut-export">
        {!jobId && !render.isPending && !render.isError && <>
            <Button variant="outline" disabled={!keptSeconds(skipPlans(plan, plan.want))} onClick={() => render.mutate()}>
                <FileAudio/>{t('export')}</Button>
            <p className="cut-hint cut-export-hint">{t('export-hint')}</p></>}
        {body}
    </div>
}
