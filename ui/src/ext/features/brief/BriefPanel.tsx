import {type ReactNode, useEffect, useId, useState} from 'react'
import {useTranslation} from 'react-i18next'
import {Link} from 'react-router-dom'
import {useMutation, useQuery, useQueryClient} from '@tanstack/react-query'
import {LoaderCircle, Play, Sparkles} from 'lucide-react'
import {Button} from '../../../components/ui/button'
import {clock, minutes} from '../../../utils/listening'
import {MakeTranscript} from '../../shared/MakeTranscript'
import type {EpisodeCtx} from '../../types'
import {badgeKey, type Brief, briefKey, fetchBrief, makeBrief} from './api'
import {VerdictChip} from './Verdict'

export const POLL_MS = 2000
const SHOWN = 5
const CONCEPTS = 8
const raw = {interpolation: {escapeValue: false}}

/** "Is it worth my time?" above the episode workspace: verdict, summary, "% new", sponsors, chapters, key ideas. */
export function BriefPanel(ctx: EpisodeCtx) {
    return <Panel key={ctx.episodeId} {...ctx}/>
}

function Panel({episodeId, episode, seek}: EpisodeCtx) {
    const {t} = useTranslation('brief')
    const cache = useQueryClient()
    const heading = useId()
    const [all, setAll] = useState(false)
    const brief = useQuery({queryKey: briefKey(episodeId), queryFn: () => fetchBrief(episodeId), retry: false,
        refetchInterval: query => query.state.data?.status === 'generating' ? POLL_MS : false})
    const make = useMutation({mutationFn: (regenerate: boolean) => makeBrief(episodeId, regenerate),
        onSuccess: made => cache.setQueryData(briefKey(episodeId), made)})
    const data = brief.data
    useEffect(() => {
        if (data?.status === 'ready') void cache.invalidateQueries({queryKey: badgeKey(episodeId)})
    }, [cache, episodeId, data?.status, data?.created_at])

    const frame = (body: ReactNode) => <section className="brief-panel" aria-labelledby={heading}>
        <h2 id={heading} className="brief-label"><Sparkles size={13}/>{t('title')}</h2>{body}</section>
    if (brief.isLoading) return frame(<p role="status" className="brief-muted">{t('loading')}</p>)
    // A failed poll while a brief is being made keeps the last answer on screen; polling goes on.
    if (!data) return frame(<div className="brief-actions"><p role="alert">{t('load-error')}</p>
        <Button variant="outline" size="sm" onClick={() => void brief.refetch()}>{t('try-again')}</Button></div>)

    const run = (regenerate: boolean) => make.mutate(regenerate)
    const showMore = `/briefs?show=${encodeURIComponent(episode.podcast_id)}`
    const chapters = all ? data.chapters : data.chapters.slice(0, SHOWN)
    const ideas = all ? data.key_ideas : data.key_ideas.slice(0, SHOWN)
    const hidden = data.chapters.length > SHOWN || data.key_ideas.length > SHOWN
    const concepts = data.new_concepts.slice(0, CONCEPTS)
    const tokens = data.estimated_tokens ? data.estimated_tokens.toLocaleString() : ''

    let actions: ReactNode
    if (data.status === 'no_transcript') actions = <div className="brief-transcript"><p>{t('no-transcript')}</p>
        <MakeTranscript key={episode.id} episode={episode} onReady={() => {
            void brief.refetch()
            void cache.invalidateQueries({queryKey: ['transcript', episodeId]})
        }}/></div>
    else if (data.status === 'generating') actions = <p role="status" className="brief-busy"><LoaderCircle size={15} className="animate-spin"/>{t('generating')}</p>
    else if (!data.ai_ready && data.status !== 'ready') actions = <p className="brief-muted">{t('no-ai')} <Link to="/settings/ai">{t('connect-ai')}</Link></p>
    else if (data.status === 'failed') actions = <div className="brief-actions">
        <p role="alert" className="brief-error">{t('failed', {reason: data.error ?? '', ...raw})}</p>
        <Button variant="outline" size="sm" disabled={make.isPending} onClick={() => run(false)}>{t('try-again')}</Button></div>
    else if (data.status === 'not_generated') actions = <div className="brief-actions">
        <Button disabled={make.isPending} onClick={() => run(false)}>{make.isPending ? <LoaderCircle className="animate-spin"/> : <Sparkles/>}{t('generate')}</Button>
        {tokens && <span className="brief-muted">{t('generate-hint', {tokens})}</span>}
        <Link to={showMore} className="brief-link">{t('brief-more')}</Link></div>
    else actions = <>
        <div className="brief-meta">{data.model && <span>{t('made-with', {model: data.model, ...raw})}</span>}
            {data.ai_ready && <Button variant="link" size="sm" disabled={make.isPending} onClick={() => run(true)}>{t('regenerate')}</Button>}
            {data.ai_ready && <Link to={showMore} className="brief-link">{t('brief-more')}</Link>}</div>
        {data.error && <p role="alert" className="brief-muted">{t('regenerate-failed', {reason: data.error, ...raw})}</p>}</>

    return frame(<>
        {data.verdict && <p className="brief-verdict"><VerdictChip verdict={data.verdict}/><span>{data.verdict_reason}</span></p>}
        {data.summary && <p className="brief-summary">{data.summary}</p>}
        <Facts brief={data}/>
        {data.who_for && <p className="brief-who">{t('who-for', {who: data.who_for, ...raw})}</p>}
        {concepts.length > 0 && <p className="brief-concepts"><span>{t(data.heard_count ? 'new-concepts' : 'key-concepts')}</span> {concepts.join(', ')}</p>}
        {(chapters.length > 0 || ideas.length > 0) && <div className="brief-columns">
            {chapters.length > 0 && <div><h3>{t(data.chapters_source === 'ai' ? 'chapters' : 'publisher-chapters')}</h3>
                <ol className="brief-list">{chapters.map((chapter, index) => <li key={index}>
                    <PlayFrom at={chapter.start} title={chapter.title} seek={seek}/><span>{chapter.title}</span></li>)}</ol></div>}
            {ideas.length > 0 && <div><h3>{t('key-ideas')}</h3>
                <ul className="brief-list">{ideas.map((idea, index) => <li key={index}>
                    {idea.citations[0] && <PlayFrom at={idea.citations[0].start} title={idea.text} seek={seek}/>}<span>{idea.text}</span></li>)}</ul></div>}
        </div>}
        {hidden && <Button variant="link" size="sm" className="brief-toggle" aria-expanded={all} onClick={() => setAll(!all)}>{t(all ? 'show-less' : 'show-all')}</Button>}
        {actions}
        {make.isError && <p role="alert" className="brief-error">{make.error.message}</p>}
    </>)
}

function Facts({brief}: {brief: Brief}) {
    const {t} = useTranslation('brief')
    const items: ReactNode[] = []
    if (brief.percent_new !== null && brief.heard_count) items.push(<span key="new">
        <strong>{t('percent-new', {percent: brief.percent_new})}</strong> {t('percent-new-detail', {count: brief.heard_count})}</span>)
    else if (brief.heard_count === 0) items.push(<span key="new">{t('nothing-heard')}</span>)
    if (brief.ads_seconds) items.push(<span key="ads">{t('sponsor-time', {time: clock(brief.ads_seconds)})}</span>)
    else if (brief.ads_seconds === 0) items.push(<span key="ads">{t('no-sponsors')}</span>)
    if (brief.duration) items.push(<span key="length">{minutes(brief.duration)}</span>)
    return items.length ? <p className="brief-facts">{items}</p> : null
}

function PlayFrom({at, title, seek}: {at: number; title: string; seek: (seconds: number) => void}) {
    const {t} = useTranslation('brief')
    return <Button variant="ghost" size="sm" className="brief-time" aria-label={t('play-from', {time: clock(at), title, ...raw})}
        onClick={() => seek(at)}><Play size={11} fill="currentColor"/>{clock(at)}</Button>
}
