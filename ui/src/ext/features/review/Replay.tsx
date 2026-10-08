// "Replay what I forget": a cut plan of exactly the spans behind cards answered Again 2+ times,
// then Smart Play and export (this feature reuses cuts/api.ts and cuts/smartPlay.ts
// unchanged: this plan is a normal Plan, just built from citations instead of a search).
import {useState} from 'react'
import {useTranslation} from 'react-i18next'
import {useMutation, useQuery} from '@tanstack/react-query'
import {Download, LoaderCircle, Play, RotateCcw} from 'lucide-react'
import {Button} from '../../../components/ui/button'
import {cutFile, getCut, getJob, startRender, type Plan} from '../cuts/api'
import {keptSeconds, shortMinutes, skipPlans, startSmartPlay} from '../cuts/smartPlay'
import {replayPlan} from './api'

const JOB_POLL_MS = 1500

export function Replay() {
    const {t} = useTranslation('review')
    const [plan, setPlan] = useState<Plan | null>(null)
    const [jobId, setJobId] = useState<string>()
    const find = useMutation({mutationFn: replayPlan, onSuccess: found => {setPlan(found); setJobId(undefined)}})
    const plans = plan ? skipPlans(plan, t('replay-label')) : []
    const seconds = keptSeconds(plans)

    return <section className="review-replay">
        <h2>{t('replay-title')}</h2>
        <p className="review-muted">{t('replay-hint')}</p>
        {!plan && <Button variant="outline" disabled={find.isPending} onClick={() => find.mutate()}>
            {find.isPending ? <LoaderCircle className="animate-spin"/> : <RotateCcw/>}{t('replay-find')}</Button>}
        {find.isError && <p role="alert" className="review-error">{find.error.message}</p>}
        {plan && <div className="review-replay-plan">
            <p className="review-muted">{t('replay-summary', {minutes: shortMinutes(plan.kept_seconds), count: plans.length})}</p>
            {plan.needs_timing.length > 0 && <p className="review-muted">{t('replay-needs-timing', {count: plan.needs_timing.length})}</p>}
            {seconds > 0 && <div className="review-replay-actions">
                <SmartPlay plans={plans}/>
                <Export plan={plan} jobId={jobId} setJobId={setJobId}/>
            </div>}
            <Button variant="link" size="sm" onClick={() => {setPlan(null); find.reset()}}>{t('replay-again')}</Button>
        </div>}
    </section>
}

function SmartPlay({plans}: {plans: ReturnType<typeof skipPlans>}) {
    const {t} = useTranslation('review')
    const [error, setError] = useState(false)
    const start = async () => {
        setError(false)
        try {await startSmartPlay(plans)} catch {setError(true)}
    }
    return <div className="review-smart">
        <Button onClick={() => void start()}><Play fill="currentColor"/>{t('smart-play')}</Button>
        {error && <p role="alert" className="review-error">{t('smart-play-error')}</p>}
    </div>
}

function Export({plan, jobId, setJobId}: {plan: Plan; jobId?: string; setJobId: (id?: string) => void}) {
    const {t} = useTranslation('review')
    const [saveError, setSaveError] = useState(false)
    const render = useMutation({mutationFn: () => startRender(plan.id), onSuccess: ({job_id}) => setJobId(job_id)})
    const job = useQuery({queryKey: ['review', 'job', jobId], queryFn: () => getJob(jobId!), enabled: !!jobId, retry: false,
        refetchInterval: query => ['done', 'failed'].includes(query.state.data?.status ?? '') ? false : JOB_POLL_MS})
    const cutId = job.data?.status === 'done' ? job.data.cut_id : null
    const cut = useQuery({queryKey: ['review', 'cut', cutId], queryFn: () => getCut(cutId!), enabled: !!cutId, retry: false})
    const again = () => {setJobId(undefined); render.mutate()}
    const save = async () => {
        setSaveError(false)
        try {
            const url = URL.createObjectURL(await cutFile(cutId!))
            const link = Object.assign(document.createElement('a'), {href: url, download: 'replay-what-i-forget.mp3'})
            link.click()
            setTimeout(() => URL.revokeObjectURL(url), 60_000)
        } catch {setSaveError(true)}
    }
    if (job.data?.status === 'failed') return <p role="alert" className="review-error">{t('export-failed')} {job.data.error}{' '}
        <Button variant="link" onClick={again}>{t('try-again')}</Button></p>
    if (cut.data) return <div className="review-export-ready">
        <Button onClick={() => void save()}><Download/>{t('download-mp3')}</Button>
        {saveError && <p role="alert" className="review-error">{t('export-error')}</p>}
    </div>
    if (jobId) return <p role="status" className="review-muted">
        <LoaderCircle size={14} className="animate-spin inline"/> {t('exporting', {percent: Math.round((job.data?.progress ?? 0) * 100)})}</p>
    return <>
        <Button variant="outline" disabled={render.isPending} onClick={() => render.mutate()}>
            {render.isPending ? <LoaderCircle className="animate-spin"/> : <Download/>}{t('export')}</Button>
        {render.isError && <p role="alert" className="review-error">{t('export-error')}</p>}
    </>
}
