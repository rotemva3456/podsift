import {useEffect, useRef, useState} from 'react'
import {useTranslation} from 'react-i18next'
import {Link, useSearchParams} from 'react-router-dom'
import {useMutation, useQuery, useQueryClient} from '@tanstack/react-query'
import {ArrowLeft, Bookmark, BookOpen, Check, Copy, Play} from 'lucide-react'
import {companion, Note, passageAt, saveCompanionNote, Transcript} from '../utils/companion'
import {clock, fetchEpisode, plainText, playEpisode} from '../utils/listening'
import useAudioPlayer from '../store/AudioPlayerSlice'
import {Button} from '../components/ui/button'
import {ListenLoading, ListenState} from '../components/ListenState'
import {Textarea} from '../components/ui/textarea'
import {AskEpisode} from '../components/AskEpisode'
import {episodeHeaders, episodeTools} from '../ext/registry'
import type {EpisodeCtx} from '../ext/types'
import {SourceWorkspace} from '../ext/features/source-workspace/SourceWorkspace'
import {VideoWorkspace} from '../ext/features/video-learning/VideoWorkspace'

export const Learn = ({notebook = false}: {notebook?: boolean}) => {
    const [params] = useSearchParams()
    const videoId = params.get('video')
    const at = Number(params.get('at'))
    if (!notebook && (videoId || params.get('source') === 'videos')) {
        return <VideoWorkspace videoId={videoId ?? undefined} at={params.has('at') && Number.isFinite(at) && at >= 0 ? at : undefined}/>
    }
    return <EpisodeLearn notebook={notebook}/>
}

const EpisodeLearn = ({notebook = false}: {notebook?: boolean}) => {
    const {t} = useTranslation()
    const [params, setParams] = useSearchParams()
    const playing = useAudioPlayer(state => state.loadedPodcastEpisode?.podcastEpisode)
    const position = useAudioPlayer(state => state.pendingSeek ?? state.metadata?.currentTime ?? 0)
    const id = notebook ? undefined : params.get('episode') ?? playing?.episode_id
    const requestedTime=params.get('at')
    const requested=requestedTime!==null && Number.isFinite(Number(requestedTime)) && Number(requestedTime)>=0 ? Number(requestedTime) : null
    const episode = useQuery({queryKey: ['listen-episode', id], queryFn: () => fetchEpisode(id!), enabled: !!id})
    const transcript = useQuery({queryKey: ['transcript', id], queryFn: () => companion<Transcript>(`/episodes/${id}/transcript`), enabled: !!id, retry: false})
    const notes = useQuery({queryKey: ['notes', id], queryFn: () => companion<Note[]>(`/notes${id ? `?episode_id=${id}` : ''}`)})
    const [draft, setDraft] = useState('')
    const [selected, setSelected] = useState<number | null>(null)
    const [copied, setCopied] = useState(false)
    const [copyError, setCopyError] = useState('')
    const [follow, setFollow] = useState(false)
    const [mobilePanel,setMobilePanel]=useState<string>('transcript') // transcript | ask | notes | tool:<feature>:<tool>
    const [askPrefill, setAskPrefill] = useState<{episodeId: string; question: string; sequence: number}>()
    const overviewRequested = params.get('overview') === '1'
    const [overviewOpen, setOverviewOpen] = useState(overviewRequested)
    useEffect(() => {setOverviewOpen(overviewRequested)}, [id, overviewRequested])
    // On a phone, a tab tap can leave the chosen panel scrolled out of view (e.g. under the
    // fixed player) if the page was scrolled down for a longer tab. Bring the tab row back near
    // the top so the panel starts in view. Desktop never scrolls here; skip the first render so
    // opening the page doesn't jump.
    const tabsRef = useRef<HTMLDivElement>(null), tabsMounted = useRef(false)
    useEffect(() => {
        if (!tabsMounted.current) {tabsMounted.current = true; return}
        if (typeof window !== 'undefined' && window.innerWidth <= 767) tabsRef.current?.scrollIntoView({block: 'start', behavior: 'smooth'})
    }, [mobilePanel])
    const activeRef = useRef<HTMLLIElement>(null)
    const cache = useQueryClient()
    const activeEpisodeRef=useRef(id)
    activeEpisodeRef.current=id
    const save = useMutation({mutationFn: saveCompanionNote,
        onSuccess: (saved) => {if(activeEpisodeRef.current===saved.episode_id)setDraft(''); void cache.invalidateQueries({queryKey:['notes']})}})
    useEffect(() => {setDraft(''); setSelected(requested); save.reset(); setCopied(false)}, [id,requestedTime])
    const current = episode.data?.podcastEpisode
    const sourcePosition = playing?.episode_id === id ? position : episode.data?.podcastHistoryItem?.position ?? 0
    const segments = transcript.data?.segments ?? []
    const active = passageAt(segments, sourcePosition)
    const selectedPosition = selected ?? sourcePosition
    useEffect(() => {if(follow) activeRef.current?.scrollIntoView({block:'nearest', behavior:'smooth'})}, [active, follow])
    // ?at=<seconds> opens at the passage holding that time, marked until another moment is chosen.
    const requestedPassage = requested!==null && selected===requested ? passageAt(segments, requested) : -1
    const passagesRef = useRef<HTMLOListElement>(null), openedAt = useRef('')
    useEffect(() => {
        if (requestedPassage<0 || openedAt.current===`${id}@${requested}`) return
        openedAt.current=`${id}@${requested}`
        passagesRef.current?.querySelector('.requested-passage')?.scrollIntoView({block:'center'})
    }, [id, requested, requestedPassage])
    const seek = (seconds: number) => {if (current) {setSelected(seconds); void playEpisode(current, seconds)}}
    const passage = selected === null ? segments[active] : segments[passageAt(segments,selected)]
    const copy = async () => {
        if (!current) return
        try {await navigator.clipboard.writeText(`${plainText(current.name)} — ${clock(selectedPosition)}\n${passage?.text ?? ''}\n${current.url}#t=${Math.floor(selectedPosition)}`);setCopied(true);setCopyError('')}
        catch {setCopyError('Copy is unavailable in this browser. You can select and copy the transcript text.')}
    }
    if (!id && !notebook) return <ListenState title="Choose a source to learn from"><p>Open a podcast or bring a downloaded video to inspect its words and useful moments.</p><div className="flex flex-wrap gap-2 mt-4 justify-center"><Button nativeButton={false} render={<Link to="/home/view"/>}>Go to Today</Button><Button variant="outline" nativeButton={false} render={<Link to="/podcasts"/>}>Open Library</Button><Button variant="outline" nativeButton={false} render={<Link to="/learn?source=videos"/>}>Open or add a video</Button></div></ListenState>
    if (!id) return <><div className="page-title"><h1>Your notes</h1><p>Keep the parts you want to remember.</p></div>
        {notes.isLoading ? <ListenLoading/> : notes.isError ? <ListenState title="Notes couldn't load" retry={()=>void notes.refetch()}/> : notes.data?.length ? <div className="notebook-list">{notes.data.map(note=><article key={note.id}><Link to={`/learn?episode=${note.episode_id}&at=${note.position}`}><BookOpen size={16}/>{plainText(note.title)}</Link><p>{note.text}</p><span>{clock(note.position)} · {new Date(note.created_at).toLocaleDateString()}</span></article>)}</div> : <ListenState title="Good ideas deserve a bookmark"><p>Open an episode's transcript to save a note with its timestamp.</p><Button variant="outline" className="mt-4" nativeButton={false} render={<Link to="/home/view"/>}>Choose an episode</Button></ListenState>}</>
    if (episode.isLoading) return <ListenLoading/>
    if (episode.isError || !current) return <ListenState title="This episode couldn't load" retry={()=>void episode.refetch()}>It may have been removed from your library.</ListenState>
    const ctx: EpisodeCtx = {episodeId: current.episode_id, episode: current, position: selectedPosition, seek}
    const tool = episodeTools.find(item => mobilePanel === `tool:${item.key}`)
    const panel = mobilePanel.startsWith('tool:') && !tool ? 'transcript' : mobilePanel
    return <div className="learn-source-shell">
        <div className="flex flex-wrap items-center justify-between gap-2"><Link to="/home/view" className="back-link"><ArrowLeft size={16}/> Back to listening</Link><Button variant="ghost" nativeButton={false} render={<Link to="/learn?source=videos"/>}>Open or add a video</Button></div>
        <div className="workspace-heading"><img src={current.local_image_url} alt=""/><div><p className="eyebrow">Transcript & notes</p><h1>{plainText(current.name)}</h1><div className="flex gap-3 mt-4"><Button onClick={()=>void playEpisode(current, selectedPosition)}><Play size={15} fill="currentColor"/> Play from {clock(selectedPosition)}</Button>{playing && playing.episode_id !== id && <Button variant="outline" onClick={()=>setParams({episode:playing.episode_id})}>Go to playing episode</Button>}</div></div></div>
        {episodeHeaders.length > 0 && <details className="source-overview" open={overviewOpen} onToggle={event=>setOverviewOpen(event.currentTarget.open)}><summary>Overview</summary><div className="episode-headers">{episodeHeaders.map(({featureId, Component}) => <Component key={featureId} {...ctx}/>)}</div></details>}
        <div className="workspace-tabs" ref={tabsRef} role="group" aria-label="Episode workspace" data-tools={episodeTools.length ? '' : undefined}>{episodeTools.length > 0 && <Button className="workspace-tab-desktop" variant={tool?'ghost':'secondary'} aria-pressed={!tool} onClick={()=>setMobilePanel('transcript')}>{t('transcript')}</Button>}<Button variant={mobilePanel==='transcript'?'secondary':'ghost'} aria-pressed={mobilePanel==='transcript'} onClick={()=>setMobilePanel('transcript')}>Transcript</Button><Button variant={mobilePanel==='ask'?'secondary':'ghost'} aria-pressed={mobilePanel==='ask'} onClick={()=>setMobilePanel('ask')}>Ask</Button><Button variant={mobilePanel==='notes'?'secondary':'ghost'} aria-pressed={mobilePanel==='notes'} onClick={()=>setMobilePanel('notes')}>Notes {notes.data?.length ? `(${notes.data.length})` : ''}</Button>
            {episodeTools.map(item => <Button key={item.key} data-tool={item.key} variant={tool===item?'secondary':'ghost'} aria-pressed={tool===item} onClick={()=>setMobilePanel(`tool:${item.key}`)}>{t(item.label, {ns: item.featureId})}</Button>)}</div>
        <div className="learning-layout" data-panel={panel}>
        <AskEpisode key={id} episodeId={id} position={selectedPosition} onSeek={seek} onUseCurrentTime={selected !== null ? ()=>setSelected(null) : undefined}
            prefill={askPrefill?.episodeId === id ? {question: askPrefill.question, sequence: askPrefill.sequence} : undefined}/>
        <SourceWorkspace key={`source-${id}`} episode={current} transcript={transcript} sourcePosition={sourcePosition} active={active} requestedPassage={requestedPassage}
            playing={playing?.episode_id===id} follow={follow} onFollow={()=>setFollow(!follow)} onSeek={seek} onExplain={(seconds, quote)=>{setSelected(seconds);setAskPrefill(previous=>({episodeId:id,question:`Explain this passage in context: ${quote}`.slice(0,2000),sequence:(previous?.sequence??0)+1}));setMobilePanel('ask')}} passagesRef={passagesRef} activeRef={activeRef}/>
        <aside className="note-panel" aria-label="Episode notes"><h2><Bookmark size={18}/> Keep a thought</h2><p className="text-muted-foreground text-sm mt-2">Saved with a link to this moment.</p>
            <div className="note-moment"><span>{clock(selectedPosition)}</span>{selected !== null && <Button variant="link" onClick={()=>setSelected(null)}>Use current time</Button>}</div>
            {passage && <blockquote>{passage.text}</blockquote>}
            <form onSubmit={e=>{e.preventDefault();save.mutate({id:crypto.randomUUID(),episode_id:current.episode_id,position:selectedPosition,text:draft})}}>
                <Textarea disabled={save.isPending} aria-label="Your note" placeholder="What do you want to remember?" rows={4} maxLength={4000} value={draft} onChange={e=>setDraft(e.target.value)}/>
                <Button type="submit" className="w-full mt-3" disabled={!draft.trim() || save.isPending}>{save.isPending?'Saving…':`Save note at ${clock(selectedPosition)}`}</Button>
            </form>
            {save.isSuccess && <p role="status" className="save-status"><Check size={15}/> Note saved</p>}{save.isError && <p role="alert" className="text-destructive text-sm mt-2">{save.error.message}</p>}
            <Button variant="ghost" className="w-full mt-2" onClick={()=>void copy()}><Copy size={14}/>{copied?'Copied passage':'Copy passage & source'}</Button>{copyError && <p role="alert" className="text-sm">{copyError}</p>}
            <div className="saved-notes"><h3>Notes from this episode</h3>{notes.isError ? <p role="alert">Notes couldn't load. <Button variant="link" onClick={()=>void notes.refetch()}>Try again</Button></p> : notes.data?.length ? notes.data.map(note=><article key={note.id}><Button variant="link" onClick={()=>seek(note.position)}>{clock(note.position)}</Button><p>{note.text}</p></article>) : <p>Your saved notes will appear here.</p>}</div>
        </aside></div>
        {tool && <section className="episode-tool" aria-label={t(tool.label, {ns: tool.featureId})}><tool.Component {...ctx}/></section>}
    </div>
}
