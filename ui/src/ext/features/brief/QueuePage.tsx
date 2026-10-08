import {useEffect, useMemo, useState} from 'react'
import {useTranslation} from 'react-i18next'
import {Link, useSearchParams} from 'react-router-dom'
import {useMutation, useQuery, useQueryClient} from '@tanstack/react-query'
import {ArrowLeft, LoaderCircle, Sparkles, Square} from 'lucide-react'
import {Button} from '../../../components/ui/button'
import {Checkbox} from '../../../components/ui/checkbox'
import {ListenLoading, ListenState} from '../../../components/ListenState'
import {type Episode, type History, minutes, plainText, type Show} from '../../../utils/listening'
import {podfetch} from '../../shared/podfetch'
import {cachedBriefs, type Estimate, estimateQueue, fetchQueue, type Queue, QUEUE_KEY, QUEUE_MAX, startQueue, stopQueue} from './api'
import {VerdictChip} from './Verdict'

type Item = {podcastEpisode: Episode; podcastHistoryItem?: History | null}
const DEFAULT_PICK = 5
const POLL_MS = 2000
const raw = {interpolation: {escapeValue: false}}

async function getJson<T>(path: string): Promise<T> {
    const response = await podfetch(path)
    if (!response.ok) throw new Error(`PodFetch answered ${response.status}`)
    return response.json()
}

/** Played to 90% or more, in the web player or a phone app (gpodder), as the brief counts "heard". */
const finished = ({podcastEpisode: episode, podcastHistoryItem: history}: Item) => {
    const position = history?.position ?? 0, total = history?.total || episode.total_time
    return position > 0 && total > 0 && position >= 0.9 * total
}

/** /briefs?show=<podcast id>: brief up to 20 episodes of a show, one at a time, after seeing the cost. */
export function QueuePage() {
    const {t} = useTranslation('brief')
    const [params] = useSearchParams()
    const show = params.get('show')
    return <>
        {show && <Link to="/briefs" className="back-link"><ArrowLeft size={16}/>{t('all-shows')}</Link>}
        <div className="page-title"><h1>{t('queue-title')}</h1><p>{t('queue-intro')}</p></div>
        {show ? <ShowEpisodes key={show} podcastId={show}/> : <Shows/>}
    </>
}

function Shows() {
    const {t} = useTranslation('brief')
    const shows = useQuery({queryKey: ['brief-shows'], queryFn: () => getJson<Show[]>('/api/v1/podcasts')})
    if (shows.isLoading) return <ListenLoading/>
    if (shows.isError) return <ListenState title={t('shows-error')} retry={() => void shows.refetch()}/>
    if (!shows.data?.length) return <ListenState title={t('no-shows')}/>
    return <><p className="brief-muted brief-lead">{t('choose-show')}</p>
        <div className="brief-shows">{shows.data.map(show => <Link key={show.id} className="show-tile" to={`/briefs?show=${encodeURIComponent(show.id)}`}>
            <img src={show.image_url} alt="" loading="lazy"/><span>{plainText(show.name)}</span></Link>)}</div></>
}

function ShowEpisodes({podcastId}: {podcastId: string}) {
    const {t} = useTranslation('brief')
    const cache = useQueryClient()
    const base = `/api/v1/podcasts/${encodeURIComponent(podcastId)}`
    const show = useQuery({queryKey: ['brief-show', podcastId], queryFn: () => getJson<Show>(base)})
    const episodes = useQuery({queryKey: ['brief-show-episodes', podcastId], queryFn: () => getJson<Item[]>(`${base}/episodes`)})
    const items = episodes.data ?? []
    const ids = items.map(item => item.podcastEpisode.episode_id)
    const briefs = useQuery({queryKey: ['brief-show-briefs', podcastId, ids.join(',')], queryFn: () => cachedBriefs(ids), enabled: ids.length > 0})
    const queue = useQuery({queryKey: QUEUE_KEY, queryFn: fetchQueue,
        refetchInterval: query => query.state.data?.status === 'running' ? POLL_MS : false})
    const [picked, setPicked] = useState<string[] | null>(null)
    const [estimate, setEstimate] = useState<Estimate | null>(null)
    const byId = useMemo(() => new Map((briefs.data ?? []).map(brief => [brief.episode_id.toLowerCase(), brief])), [briefs.data])
    const check = useMutation({mutationFn: estimateQueue, onSuccess: setEstimate})
    const start = useMutation({mutationFn: startQueue, onSuccess: state => {setEstimate(null); cache.setQueryData(QUEUE_KEY, state)}})
    const stop = useMutation({mutationFn: stopQueue, onSuccess: state => cache.setQueryData(QUEUE_KEY, (old?: Queue) => ({...state, ai_ready: old?.ai_ready ?? true}))})
    const state = queue.data
    const running = state?.status === 'running'
    const aiReady = state?.ai_ready === true
    useEffect(() => {  // new verdicts appear as each episode finishes
        if (state?.done) {
            void cache.invalidateQueries({queryKey: ['brief-show-briefs', podcastId]})
            void cache.invalidateQueries({queryKey: ['brief-badge']})
        }
    }, [cache, podcastId, state?.done, state?.status])

    if (episodes.isLoading) return <ListenLoading/>
    if (episodes.isError) return <ListenState title={t('episodes-error')} retry={() => void episodes.refetch()}/>
    if (!items.length) return <ListenState title={t('no-episodes')}/>

    // Until the user picks, suggest the newest episodes without a brief that they haven't finished.
    const chosen = picked ?? (aiReady ? items.filter(item => !byId.has(item.podcastEpisode.episode_id.toLowerCase()) && !finished(item))
        .slice(0, DEFAULT_PICK).map(item => item.podcastEpisode.episode_id) : [])
    const toggle = (id: string, on: boolean) => {
        setEstimate(null)
        setPicked(on ? [...chosen, id].slice(0, QUEUE_MAX) : chosen.filter(other => other !== id))
    }
    const counts = {ready: state?.items?.filter(item => item.status === 'ready').length ?? 0,
        failed: state?.items?.filter(item => item.status === 'failed').length ?? 0}
    const skipped = estimate?.items.filter(item => item.status !== 'will_brief') ?? []
    const error = check.error ?? start.error ?? stop.error

    return <>
        {show.data && <h2 className="brief-show-name">{plainText(show.data.name)}</h2>}
        {queue.isSuccess && !aiReady && <p className="brief-notice">{t('queue-no-ai')} <Link to="/settings/ai">{t('connect-ai')}</Link></p>}
        <div className="brief-queue-bar">
            {running ? <>
                <div className="brief-progress-wrap"><p role="status">{t('progress', {done: state.done ?? 0, total: state.total ?? 0})}</p>
                    <div className="brief-progress" aria-hidden="true"><span style={{width: `${100 * (state.done ?? 0) / Math.max(1, state.total ?? 1)}%`}}/></div></div>
                <Button variant="outline" size="sm" disabled={stop.isPending || state.cancelled} onClick={() => stop.mutate()}>
                    <Square/>{t(state.cancelled ? 'stopping' : 'stop')}</Button>
            </> : <>
                <span className="brief-muted">{t('selected', {count: chosen.length})}</span>
                <Button disabled={!chosen.length || !aiReady || check.isPending} onClick={() => check.mutate(chosen)}>
                    {check.isPending ? <LoaderCircle className="animate-spin"/> : <Sparkles/>}{t(check.isPending ? 'checking' : 'check-cost')}</Button>
                {state && (state.status === 'done' || state.status === 'cancelled') && <span role="status" className="brief-muted">
                    {t(state.status === 'done' ? 'finished-queue' : 'stopped-queue', counts)}</span>}
            </>}
        </div>
        {estimate && !running && <div className="brief-estimate">
            <p>{estimate.count ? t('estimate', {count: estimate.count, model: estimate.model ?? '', tokens: estimate.input_tokens.toLocaleString(), ...raw})
                : t('estimate-none')}</p>
            {skipped.length > 0 && <p className="brief-muted">{t('estimate-skipped', {...raw,
                list: skipped.map(item => `${plainText(item.title ?? '')} (${t(`item-${item.status}`)})`).join('; ')})}</p>}
            <div className="brief-actions">
                {estimate.count > 0 && <Button disabled={start.isPending} onClick={() => start.mutate(
                    estimate.items.filter(item => item.status === 'will_brief').map(item => item.episode_id))}><Sparkles/>{t('start', {count: estimate.count})}</Button>}
                <Button variant="ghost" onClick={() => setEstimate(null)}>{t('cancel')}</Button>
            </div>
        </div>}
        {error && <p role="alert" className="brief-error">{error.message}</p>}
        <ul className="brief-queue-list">{items.map(item => {
            const episode = item.podcastEpisode, id = episode.episode_id, title = plainText(episode.name)
            const brief = byId.get(id.toLowerCase()), queued = state?.items?.find(entry => entry.episode_id === id.toLowerCase())
            const on = chosen.includes(id)
            let status = null
            if (queued && (running || queued.status === 'failed') && queued.status !== 'ready') status = queued.status === 'running'
                ? <span className="brief-busy"><LoaderCircle size={13} className="animate-spin"/>{t('item-running')}</span>
                : <span className="brief-muted" title={queued.error ?? undefined}>{t(`item-${queued.status}`)}</span>
            else if (brief?.verdict) status = <Link to={`/learn?episode=${encodeURIComponent(id)}`} aria-label={title}><VerdictChip verdict={brief.verdict} reason={brief.verdict_reason}/></Link>
            return <li key={id} className="brief-queue-row">
                <label className="brief-queue-pick">
                    <Checkbox checked={on} disabled={running || !aiReady || (!on && chosen.length >= QUEUE_MAX)}
                        onCheckedChange={checked => toggle(id, checked === true)} aria-label={t('select-episode', {title, ...raw})}/>
                    <span className="brief-queue-copy"><span className="brief-queue-title">{title}</span>
                        <span className="brief-muted">{new Date(episode.date_of_recording).toLocaleDateString()} · {minutes(episode.total_time)}
                            {finished(item) && ` · ${t('finished')}`}</span></span>
                </label>
                {status && <span className="brief-queue-state">{status}</span>}
            </li>
        })}</ul>
    </>
}
