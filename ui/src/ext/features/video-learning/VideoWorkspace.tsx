import {useEffect, useRef, useState} from 'react'
import {useInfiniteQuery, useMutation, useQuery} from '@tanstack/react-query'
import {Link, useNavigate} from 'react-router-dom'
import {ArrowLeft, FileVideo, Play, Upload} from 'lucide-react'
import {Button} from '../../../components/ui/button'
import {Input} from '../../../components/ui/input'
import {Card, CardContent, CardHeader, CardTitle} from '../../../components/ui/card'
import {ListenLoading, ListenState} from '../../../components/ListenState'
import {clock} from '../../../utils/listening'
import useAudioPlayer from '../../../store/AudioPlayerSlice'
import {processing, uploadVideo, video, videoAction, videoLibrary, videoStatus, videoTranscript, type Video} from './api'

export function VideoWorkspace({videoId, at}: {videoId?: string; at?: number}) {
    return videoId ? <VideoDetail key={videoId} id={videoId} at={at}/> : <VideoLibrary/>
}

function VideoLibrary() {
    const navigate = useNavigate()
    const controller = useRef<AbortController | null>(null)
    const input = useRef<HTMLInputElement>(null)
    const [file, setFile] = useState<File | null>(null)
    const sources = useInfiniteQuery({queryKey: ['videos'], initialPageParam: 0,
        queryFn: ({pageParam}) => videoLibrary(pageParam), getNextPageParam: page => page.next_cursor ?? undefined})
    const upload = useMutation({mutationFn: (selected: File) => {
        controller.current = new AbortController()
        return uploadVideo(selected, controller.current.signal)
    }, onSuccess: source => {if (!controller.current?.signal.aborted) navigate(`/learn?video=${source.id}`)}})
    useEffect(() => () => controller.current?.abort(), [])
    return <div className="video-workspace video-library space-y-6 max-w-4xl mx-auto">
        <Link to="/home/view" className="back-link"><ArrowLeft size={16}/> Back to listening</Link>
        <div className="page-title"><h1>Learn from a video</h1><p>Bring a downloaded lesson or recording. Keep its words and original moments together.</p></div>
        <Card><CardHeader><CardTitle><Upload className="inline mr-2" size={18}/> Add a video</CardTitle></CardHeader><CardContent className="space-y-3">
            <label htmlFor="video-file" className="text-sm">Choose an MP4, MOV, M4V, MKV, or WebM file, up to 1 GB.</label>
            <Input ref={input} id="video-file" type="file" accept=".mp4,.mov,.m4v,.mkv,.webm" disabled={upload.isPending}
                onChange={event => {setFile(event.target.files?.[0] ?? null); upload.reset()}}/>
            <p className="text-sm text-muted-foreground">Importing saves the video. Transcription and AI help start when you choose them.</p>
            <div className="flex flex-wrap gap-2"><Button disabled={!file || upload.isPending} onClick={() => {if (file) upload.mutate(file)}}>{upload.isPending ? 'Saving video…' : 'Import video'}</Button>
                {upload.isPending && <Button variant="outline" onClick={() => controller.current?.abort()}>Cancel upload</Button>}</div>
            {upload.isError && <p role="alert" className="text-sm text-destructive">{upload.error.name === 'AbortError' ? 'Upload cancelled. Choose Import video to try again.' : upload.error.message}</p>}
        </CardContent></Card>
        <section aria-label="Your videos" className="space-y-3"><h2 className="font-medium">Your videos</h2>
            {sources.isLoading ? <ListenLoading/> : sources.isError ? <ListenState title="Videos couldn't load" retry={() => void sources.refetch()}/> :
                sources.data?.pages.flatMap(page => page.videos).length ? <ul className="divide-y divide-border">{sources.data.pages.flatMap(page => page.videos).map(source =>
                    <li key={source.id} className="py-4"><Link to={`/learn?video=${source.id}`} className="flex items-start gap-3"><FileVideo size={20} className="shrink-0 mt-1"/><span className="min-w-0"><span className="block font-medium break-words">{source.title}</span><span className="text-sm text-muted-foreground">{clock(source.duration)} · {source.has_transcript ? 'Transcript saved' : 'Original video saved'}</span></span></Link></li>)}</ul> :
                    <p className="text-sm text-muted-foreground">Imported videos will appear here. Your podcast library stays available.</p>}
            {sources.hasNextPage && <Button variant="outline" disabled={sources.isFetchingNextPage} onClick={() => void sources.fetchNextPage()}>More videos</Button>}
        </section>
    </div>
}

function VideoDetail({id, at}: {id: string; at?: number}) {
    const player = useRef<HTMLVideoElement>(null)
    const metadataLoaded = useRef(false)
    const pendingSeek = useRef<{seconds: number; play: boolean} | null>(null)
    const [goal, setGoal] = useState('')
    const [minutes, setMinutes] = useState('')
    const [panel, setPanel] = useState<'transcript' | 'result'>('transcript')
    const [position, setPosition] = useState(at ?? 0)
    const [playError, setPlayError] = useState('')
    const podcastPlaying = useAudioPlayer(state => state.isPlaying)
    const status = useQuery({queryKey: ['video-status'], queryFn: videoStatus})
    const source = useQuery({queryKey: ['video', id], queryFn: () => video(id),
        refetchInterval: query => processing(query.state.data) ? 1500 : false})
    const words = useInfiniteQuery({queryKey: ['video-transcript', id], initialPageParam: 0,
        queryFn: ({pageParam}) => videoTranscript(id, pageParam), enabled: !!source.data?.has_transcript,
        getNextPageParam: page => page.next_cursor ?? undefined, retry: false})
    const action = useMutation({mutationFn: ({kind, task}: {kind: 'transcribe' | 'learn' | 'cancel' | 'visual-review'; task?: 'summary' | 'watch_plan'}) =>
        videoAction(id, kind, kind === 'learn' ? {task, goal: goal.trim(), minutes: minutes ? Number(minutes) : null} : undefined),
        onSuccess: () => void source.refetch()})
    useEffect(() => {if (podcastPlaying) player.current?.pause()}, [podcastPlaying])
    useEffect(() => {if (source.data?.learning) setPanel('result')}, [source.data?.learning])
    useEffect(() => {
        if (at !== undefined) {
            if (player.current && metadataLoaded.current) player.current.currentTime = Math.min(at, player.current.duration)
            else pendingSeek.current = {seconds: at, play: false}
            setPosition(at)
        }
    }, [at])
    const seek = (seconds: number) => {
        if (!player.current) return
        setPosition(seconds)
        if (!metadataLoaded.current) {
            pendingSeek.current = {seconds, play: true}
            return
        }
        player.current.currentTime = seconds
        void player.current.play().catch(() => setPlayError('Playback could not start. Use the video controls to play.'))
    }
    if (source.isLoading) return <ListenLoading/>
    if (source.isError || !source.data) return <ListenState title="This video couldn't load" retry={() => void source.refetch()}/>
    const current = source.data
    const busy = processing(current) || action.isPending
    const settings = status.data
    const invalidMinutes = minutes !== '' && (!Number.isInteger(Number(minutes)) || Number(minutes) < 1 || Number(minutes) > 480)
    const transcript = words.data?.pages.flatMap(page => page.segments) ?? []
    const result = current.learning
    const doAction = (kind: 'transcribe' | 'learn' | 'visual-review', task?: 'summary' | 'watch_plan') => {action.mutate({kind, task})}
    return <div className="video-workspace video-detail space-y-5 min-w-0">
        <Link to="/learn?source=videos" className="back-link"><ArrowLeft size={16}/> Your videos</Link>
        <div><p className="eyebrow">Original video</p><h1 className="text-2xl font-medium break-words mt-1">{current.title}</h1><p className="text-sm text-muted-foreground mt-2">{clock(current.duration)} · {current.has_transcript ? 'Full audio processed · screen not analyzed' : 'Saved and ready to play'}</p></div>
        <div className="grid lg:grid-cols-2 gap-6 items-start">
            <section className="space-y-4 min-w-0" aria-label="Video player">
                <video ref={player} src={current.media_url} controls preload="metadata" playsInline className="w-full max-h-80 bg-secondary rounded-lg"
                    onLoadedMetadata={() => {
                        metadataLoaded.current = true
                        const requested = pendingSeek.current
                        pendingSeek.current = null
                        let start = requested?.seconds ?? at ?? 0
                        if (!requested && at === undefined) {try {start = Number(localStorage.getItem(`video-position:${id}`)) || 0} catch { /* storage is optional */ }}
                        if (player.current) {player.current.currentTime = Math.min(Math.max(0, start), current.duration); setPosition(player.current.currentTime)}
                        if (requested?.play) void player.current?.play().catch(() => setPlayError('Playback could not start. Use the video controls to play.'))
                    }}
                    onPlay={() => {useAudioPlayer.getState().setPlaying(false); setPlayError('')}}
                    onPause={() => {try {localStorage.setItem(`video-position:${id}`, String(player.current?.currentTime ?? 0))} catch { /* storage is optional */ }}}
                    onTimeUpdate={() => setPosition(player.current?.currentTime ?? 0)}
                    onError={() => setPlayError('This browser could not play the file. You can download the original or try an MP4 with H.264 video.')}/>
                {playError && <p role="alert" className="text-sm text-destructive">{playError}</p>}
                <Button variant="link" nativeButton={false} render={<a href={current.media_url} download={current.title}/>}>Download original video</Button>
                {status.isError && <p role="alert" className="text-sm">Processing options couldn't load. <Button variant="link" onClick={() => void status.refetch()}>Try again</Button></p>}
                {!current.has_transcript && <Card><CardContent className="space-y-3">
                    <h2 className="font-medium">Get the full transcript</h2><p className="text-sm text-muted-foreground">Process all the audio, with timestamps in this video. Completed parts are saved for retry.</p>
                    <Button disabled={busy || !settings?.speech_configured || !current.has_audio} onClick={() => doAction('transcribe')}>Transcribe full video</Button>
                    {!current.has_audio ? <p className="text-sm text-muted-foreground">This file has no audio track. You can still watch it or prepare visual review.</p> : settings && !settings.speech_configured ?
                        <p className="text-sm text-muted-foreground">Connect a speech provider in <Link to="/settings/ai" className="underline">Settings → AI</Link>, or configure the server transcription provider.</p> :
                        <p className="text-sm text-muted-foreground">The configured speech provider receives the full audio; its charges and data handling apply.</p>}
                </CardContent></Card>}
                {current.has_speech && <Card><CardContent className="space-y-3">
                    <h2 className="font-medium">Learn from this</h2>
                    <details><summary className="text-sm cursor-pointer">Focus & time (optional)</summary><div className="space-y-3 mt-3">
                        <label htmlFor="video-goal" className="block text-sm">What do you want to understand?</label><Input id="video-goal" maxLength={1000} value={goal} onChange={event => setGoal(event.target.value)} placeholder="For example, diagnosing routing failures"/>
                        <label htmlFor="video-minutes" className="block text-sm">Minutes available</label><Input id="video-minutes" type="number" min={1} max={480} value={minutes} onChange={event => setMinutes(event.target.value)} placeholder="Watch at your own pace"/>
                    </div></details>
                    {invalidMinutes && <p role="alert" className="text-sm text-destructive">Choose a whole number of minutes from 1 to 480, or leave it empty.</p>}
                    <div className="flex flex-wrap gap-2"><Button disabled={busy || !settings?.learning_configured || invalidMinutes} onClick={() => doAction('learn', 'summary')}>Summarize</Button><Button variant="outline" disabled={busy || !settings?.learning_configured || invalidMinutes} onClick={() => doAction('learn', 'watch_plan')}>What should I watch?</Button></div>
                    {settings && !settings.learning_configured && <p className="text-sm text-muted-foreground">Connect AI in <Link to="/settings/ai" className="underline">Settings → AI</Link>. The full transcript and video stay usable.</p>}
                </CardContent></Card>}
                {current.has_transcript && !current.has_speech && <p role="status" className="text-sm">No speech was recognized. The original video is still available; its visuals have not been checked.</p>}
                {processing(current) && <div role="status" className="space-y-2 text-sm"><p>{jobLabel(current)} · {Math.round(current.progress * 100)}%</p><progress aria-label="Processing progress" max={1} value={current.progress} className="w-full"/><Button variant="outline" disabled={action.isPending || current.status === 'cancelling'} onClick={() => action.mutate({kind: 'cancel'})}>Cancel processing</Button></div>}
                {current.error && <p role={current.status === 'failed' ? 'alert' : 'status'} className="text-sm text-destructive">{current.error}</p>}
                {action.isError && <p role="alert" className="text-sm text-destructive">{action.error.message}</p>}
                {settings?.vision_configured && <details className="text-sm"><summary className="cursor-pointer">Check visuals with Course Watcher</summary><div className="space-y-3 mt-3">
                    <p className="text-muted-foreground">Prepare a review using relevant moments from your watch guide. Course Watcher receives this video; you can edit the plan before asking it to inspect the screen.</p>
                    {current.duration > 1800 ? <p className="text-muted-foreground">Visual review currently supports videos up to 30 minutes.</p> : <>
                        <Button variant="outline" disabled={busy} onClick={() => doAction('visual-review')}>Prepare visual review</Button>
                        {current.visual_review && <Button nativeButton={false} variant="link" render={<a href={current.visual_review.url} target="_blank" rel="noreferrer"/>}>Open visual review</Button>}
                    </>}
                </div></details>}
            </section>
            <section aria-label="Video words and learning" className="min-w-0 space-y-4">
                <div role="group" aria-label="Video reading view" className="flex gap-2"><Button variant={panel === 'transcript' ? 'secondary' : 'ghost'} aria-pressed={panel === 'transcript'} onClick={() => setPanel('transcript')}>Transcript</Button>{result && <Button variant={panel === 'result' ? 'secondary' : 'ghost'} aria-pressed={panel === 'result'} onClick={() => setPanel('result')}>{result.task === 'summary' ? 'Recap' : 'Watch guide'}</Button>}</div>
                {panel === 'result' && result ? <div className="space-y-5">
                    <h2 className="text-lg font-medium">{result.task === 'summary' ? 'Your recap' : 'Useful moments to watch'}</h2>
                    {result.moments.length > 0 && <ol className="space-y-4">{result.moments.map((moment, index) => <li key={index}>
                        <Button variant="link" className="h-auto whitespace-normal text-left justify-start p-0" onClick={() => seek(moment.start)}><Play size={14}/>{clock(moment.start)}–{clock(moment.end)} · {moment.title}</Button><p className="text-sm mt-1">{moment.why}</p>{moment.action === 'check_screen' && <p className="text-xs text-muted-foreground mt-1">Check the screen here; speech alone does not establish what is shown.</p>}
                    </li>)}</ol>}
                    <ul className="space-y-4 video-learning-points">{result.points.map((point, index) => <li key={index}><p>{point.text}</p><div className="flex flex-wrap gap-2">{point.sources.map(passage => <Button key={passage.id} variant="link" className="px-0 h-7" onClick={() => seek(passage.start)}>Source {clock(passage.start)}</Button>)}</div></li>)}</ul>
                    <p className="text-sm text-muted-foreground">{result.caveat}</p>
                    <p className="text-xs text-muted-foreground">Full audio processed. Visuals have not been analyzed.</p>
                </div> : !current.has_transcript ? <p className="text-sm text-muted-foreground">Your timed words will appear here after transcription. You can watch the video now.</p> : words.isLoading ? <ListenLoading/> : words.isError && !words.data ? <ListenState title="Transcript couldn't load" retry={() => void words.refetch()}/> : <>
                    <p className="text-xs text-muted-foreground">{words.data?.pages[0]?.total_segments ?? 0} timed passages · click a time to return to the source.</p>
                    <ol className="video-transcript space-y-4 max-h-[36rem] overflow-y-auto pr-2">{transcript.map(passage => <li key={passage.id} className="border-l-2 pl-3" style={{borderColor: passage.start <= position && position < (passage.end ?? Infinity) ? 'var(--primary)' : 'transparent'}}>
                        <Button variant="link" className="h-7 px-0" onClick={() => seek(passage.start)}>{clock(passage.start)}</Button><p>{passage.text}</p>
                    </li>)}</ol>
                    {words.hasNextPage && <Button variant="outline" disabled={words.isFetchingNextPage} onClick={() => void words.fetchNextPage()}>Read more transcript</Button>}
                    {words.isError && words.data && <p role="alert" className="text-sm text-destructive">More words couldn't load. The loaded passages are still available.</p>}
                </>}
            </section>
        </div>
    </div>
}

function jobLabel(source: Video) {
    if (source.status === 'cancelling') return 'Cancelling after the current provider call'
    if (source.status === 'queued') return 'Waiting to process'
    return source.job_kind === 'transcribe' ? 'Transcribing the full audio' : source.job_kind === 'visual-review' ? 'Preparing visual review' : 'Reading the transcript'
}
