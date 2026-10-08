// The episode workspace's "Recap" tab: what you heard, the brief's key ideas,
// your highlights and notes, and, with AI, a short "what you learned" paragraph.
import {type ReactNode, useId} from 'react'
import {useTranslation} from 'react-i18next'
import {Link} from 'react-router-dom'
import {useMutation, useQuery, useQueryClient} from '@tanstack/react-query'
import {LoaderCircle, Play, Sparkles} from 'lucide-react'
import {Button} from '../../../components/ui/button'
import {clock} from '../../../utils/listening'
import {MakeTranscript} from '../../shared/MakeTranscript'
import type {EpisodeCtx} from '../../types'
import {type Citation, fetchRecap, makeLearned, type NoteRow, recapKey} from './api'

const raw = {interpolation: {escapeValue: false}}

export function RecapTab(ctx: EpisodeCtx) {
    return <Panel key={ctx.episodeId} {...ctx}/>
}

function Panel({episodeId, episode, seek}: EpisodeCtx) {
    const {t} = useTranslation('recap')
    const cache = useQueryClient()
    const heading = useId()
    const recap = useQuery({queryKey: recapKey(episodeId), queryFn: () => fetchRecap(episodeId), retry: false})
    const learn = useMutation({
        mutationFn: (regenerate: boolean) => makeLearned(episodeId, regenerate),
        onSuccess: learned => cache.setQueryData(recapKey(episodeId), (was: typeof recap.data) => was && {...was, learned}),
    })

    const frame = (body: ReactNode) => <section className="recap-panel" aria-labelledby={heading}>
        <h2 id={heading} className="recap-label"><Sparkles size={13}/>{t('title')}</h2>{body}</section>
    if (recap.isLoading) return frame(<p role="status" className="recap-muted">{t('loading')}</p>)
    if (!recap.data) return frame(<div className="recap-actions"><p role="alert">{t('load-error')}</p>
        <Button variant="outline" size="sm" onClick={() => void recap.refetch()}>{t('try-again')}</Button></div>)
    const data = recap.data

    const nothingAtAll = !data.heard.total && data.key_ideas.length === 0 && data.highlights.length === 0
        && data.notes.length === 0 && data.learned.status === 'no_transcript'
    if (nothingAtAll) return frame(<p className="recap-muted">{t('empty')}</p>)

    return frame(<>
        <Heard heard={data.heard}/>
        {data.key_ideas.length > 0 && <div className="recap-section">
            <h3>{t('key-ideas')}</h3>
            <ul className="recap-list">{data.key_ideas.map((idea, index) => <li key={index}>
                {idea.citations[0] && <PlayFrom at={idea.citations[0].start} title={idea.text} seek={seek}/>}
                <span>{idea.text}</span></li>)}</ul></div>}
        <Learned data={data.learned} episode={episode} seek={seek} pending={learn.isPending}
            onGenerate={() => learn.mutate(false)} onRegenerate={() => learn.mutate(true)}
            onTranscriptReady={() => {void recap.refetch(); void cache.invalidateQueries({queryKey: ['transcript', episodeId]})}}/>
        <Highlights rows={data.highlights} seek={seek}/>
        <Notes rows={data.notes} seek={seek}/>
        {learn.isError && <p role="alert" className="recap-error">{(learn.error as Error).message}</p>}
    </>)
}

function Heard({heard}: {heard: {heard: boolean; position: number | null; total: number | null; percent: number | null}}) {
    const {t} = useTranslation('recap')
    if (!heard.total) return null
    if (heard.heard) return <p className="recap-heard">{t('heard-all', {percent: heard.percent ?? 100, ...raw})}</p>
    if (heard.position) return <p className="recap-heard">{t('heard-some',
        {position: clock(heard.position), total: clock(heard.total), ...raw})}</p>
    return <p className="recap-heard">{t('not-heard')}</p>
}

function Learned({data, episode, seek, pending, onGenerate, onRegenerate, onTranscriptReady}: {
    data: {status: string; text: string | null; citations: Citation[]; error: string | null}
    episode: EpisodeCtx['episode']; seek: (s: number) => void; pending: boolean
    onGenerate: () => void; onRegenerate: () => void; onTranscriptReady: () => void
}) {
    const {t} = useTranslation('recap')
    if (data.status === 'no_transcript') return <div className="recap-section recap-transcript">
        <p>{t('learned-no-transcript')}</p>
        <MakeTranscript key={episode.id} episode={episode} onReady={onTranscriptReady}/></div>
    if (data.status === 'no_ai') return <p className="recap-muted">{t('learned-no-ai')} <Link to="/settings/ai">{t('connect-ai')}</Link></p>
    if (pending) return <p role="status" className="recap-busy"><LoaderCircle size={15} className="animate-spin"/>{t('learned-generating')}</p>
    if (data.status === 'failed') return <div className="recap-actions">
        <p role="alert" className="recap-error">{t('learned-failed', {reason: data.error ?? '', ...raw})}</p>
        <Button variant="outline" size="sm" onClick={onGenerate}>{t('try-again')}</Button></div>
    if (data.status === 'ready') return <div className="recap-section">
        <h3><Sparkles size={13}/>{t('learned-title')}</h3>
        <p className="recap-learned-text">{data.text}</p>
        {data.citations.length > 0 && <p className="recap-citations">{data.citations.map((c, i) =>
            <PlayFrom key={i} at={c.start} title={t('learned-title')} seek={seek}/>)}</p>}
        <Button variant="link" size="sm" className="recap-toggle" onClick={onRegenerate}>{t('regenerate')}</Button></div>
    return <div className="recap-actions">
        <Button variant="outline" size="sm" onClick={onGenerate}><Sparkles size={13}/>{t('learned-generate')}</Button></div>
}

function Highlights({rows, seek}: {rows: NoteRow[]; seek: (s: number) => void}) {
    const {t} = useTranslation('recap')
    if (rows.length === 0) return null
    return <div className="recap-section">
        <h3>{t('highlights')}</h3>
        <ul className="recap-list recap-highlights">{rows.map(row => <li key={row.id}>
            {row.start !== null && <PlayFrom at={row.start} title={row.quote ?? row.text} seek={seek}/>}
            <blockquote>{row.quote ?? row.text}</blockquote></li>)}</ul></div>
}

function Notes({rows, seek}: {rows: NoteRow[]; seek: (s: number) => void}) {
    const {t} = useTranslation('recap')
    if (rows.length === 0) return null
    return <div className="recap-section">
        <h3>{t('notes')}</h3>
        <ul className="recap-list">{rows.map(row => <li key={row.id}>
            <PlayFrom at={row.position} title={row.text} seek={seek}/><span>{row.text}</span></li>)}</ul></div>
}

function PlayFrom({at, title, seek}: {at: number; title: string; seek: (seconds: number) => void}) {
    const {t} = useTranslation('recap')
    return <Button variant="ghost" size="sm" className="recap-time" aria-label={t('play-from', {time: clock(at), title, ...raw})}
        onClick={() => seek(at)}><Play size={11} fill="currentColor"/>{clock(at)}</Button>
}
