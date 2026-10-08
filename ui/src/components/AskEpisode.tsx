import {useEffect, useRef, useState} from 'react'
import {Trans, useTranslation} from 'react-i18next'
import {Link} from 'react-router-dom'
import {useMutation, useQuery} from '@tanstack/react-query'
import {Loader2, MessageCircle, Mic, Play, Square} from 'lucide-react'
import {Answer, companion} from '../utils/companion'
import {clock} from '../utils/listening'
import {getAudioPlayer} from '../utils/audioPlayer'
import useAudioPlayer from '../store/AudioPlayerSlice'
import {getSpeechStatus} from '../ext/features/voice/api'
import {playChime} from '../ext/features/voice/chime'
import {speak} from '../ext/features/voice/speak'
import {browserRecognitionEnabled, canCapture, setBrowserRecognitionEnabled, speechRecognitionCtor} from '../ext/features/voice/settings'
import {useVoiceCapture} from '../ext/features/voice/useVoiceCapture'
import {Button} from './ui/button'
import {Textarea} from './ui/textarea'
import {Card, CardContent, CardDescription, CardHeader} from './ui/card'
import '../ext/features/voice/voice.css'

/** The words to speak for an Ask-by-voice answer: citations become
 * "from minute N" rather than a time a driver can't tap. */
export function spokenAnswer(result: Answer): string {
    if (result.status === 'not_configured') return 'AI answers are off. You can still find passages by hand.'
    if (result.status === 'no_timed_transcript') return "This episode has no timed transcript, so I can only find passages by hand."
    if (result.status === 'insufficient_evidence') return "I couldn't find enough in this episode to answer that."
    return result.claims.map(claim => {
        const citation = claim.citations[0]
        const passage = citation ? result.passages.find(item => item.id === citation) : undefined
        return passage ? `${claim.text} From minute ${Math.floor(passage.start / 60)}.` : claim.text
    }).join(' ')
}

export function AskEpisode({episodeId, position, onSeek, onUseCurrentTime, prefill}: {
    episodeId: string; position: number; onSeek: (seconds: number) => void; onUseCurrentTime?: () => void
    prefill?: {question: string; sequence: number}
}) {
    const {t} = useTranslation('ai-settings')   // the "not connected" message lives with Settings → AI
    const {t: tv} = useTranslation('voice')
    const [question, setQuestion] = useState('')
    const controller = useRef<AbortController | null>(null)
    useEffect(() => () => controller.current?.abort(), [])
    const service = useQuery({queryKey: ['answer-service'],
        queryFn: () => companion<{configured: boolean}>('/answers/status'), retry: false})
    // Ask by voice: its own speech-capability check, since a provider can
    // answer without being able to transcribe audio (only OpenAI/Groq presets do both).
    const speechService = useQuery({queryKey: ['speech-service'], queryFn: getSpeechStatus, retry: false})
    const isActiveEpisode = useAudioPlayer(state => state.loadedPodcastEpisode?.podcastEpisode?.episode_id === episodeId)
    const capture = useVoiceCapture()
    const [useBrowserRecognition, setUseBrowserRecognition] = useState(() => browserRecognitionEnabled())
    const [voiceError, setVoiceError] = useState('')
    const wasPlaying = useRef(false)
    const spoke = useRef(false)   // the in-flight answer was asked by voice: speak it when it lands
    const answer = useMutation({
        mutationFn: (snapshot: {question: string; position: number}) => {
            controller.current?.abort()
            controller.current = new AbortController()
            return companion<Answer>(`/episodes/${episodeId}/answer`, {
                method: 'POST', body: JSON.stringify(snapshot), signal: controller.current.signal,
            })
        },
    })
    // Selecting an explanation prepares an editable question. Submitting it remains
    // deliberate, and a reply to the previous question cannot replace the new context.
    const appliedPrefill = useRef<number | null>(null)
    useEffect(() => {
        if (!prefill || appliedPrefill.current === prefill.sequence) return
        appliedPrefill.current = prefill.sequence
        controller.current?.abort()
        answer.reset()
        setQuestion(prefill.question)
    }, [prefill, answer.reset])
    useEffect(() => {
        if (!spoke.current) return
        spoke.current = false
        const resume = () => {if (wasPlaying.current) void getAudioPlayer()?.play().catch(() => {})}
        if (answer.data) void speak(spokenAnswer(answer.data)).then(resume)
        else if (answer.isError) void speak(answer.error.message).then(resume)
    }, [answer.data, answer.isError])
    const result = answer.data
    const voiceReady = canCapture() && (useBrowserRecognition || speechService.data?.configured !== false)
    const askByVoice = async () => {
        if (capture.state !== 'idle') {
            capture.stop()
            return
        }
        if (!useBrowserRecognition && speechService.data?.configured === false) {
            playChime(false)
            setVoiceError(tv('need-speech-ask'))
            return
        }
        setVoiceError('')
        const audio = isActiveEpisode ? getAudioPlayer() : null
        wasPlaying.current = !!audio && !audio.paused
        audio?.pause()
        try {
            const heard = (await capture.start()).trim()
            if (!heard) {
                if (wasPlaying.current) void getAudioPlayer()?.play().catch(() => {})
                return
            }
            setQuestion(heard)
            spoke.current = true
            answer.mutate({question: heard, position: audio?.currentTime ?? position})
        } catch (error) {
            playChime(false)
            const message = error instanceof Error ? error.message : tv('ask-error')
            setVoiceError(message)
            void speak(message).then(() => {if (wasPlaying.current) void getAudioPlayer()?.play().catch(() => {})})
        }
    }
    return <section className="ask-panel" aria-label="Ask this episode">
        <Card>
            <CardHeader>
                <h2 className="flex items-center gap-2 text-base font-semibold"><MessageCircle size={18}/> Ask this episode</h2>
                <CardDescription>Ask about this moment or find an idea elsewhere in the episode.</CardDescription>
            </CardHeader>
            <CardContent>
                <form onSubmit={event => {
                    event.preventDefault()
                    if (question.trim() && !answer.isPending) answer.mutate({question: question.trim(), position})
                }}>
                    <label htmlFor={`episode-question-${episodeId}`} className="sr-only">Question about this episode</label>
                    <Textarea id={`episode-question-${episodeId}`} placeholder="What was just explained?" rows={2}
                        maxLength={1000} value={question} disabled={answer.isPending}
                        onChange={event => setQuestion(event.target.value)}/>
                    <div className="ask-actions">
                        {voiceReady && <Button type="button" variant={capture.state === 'listening' ? 'destructive' : 'outline'}
                            size="icon-lg" className="voice-mic-button"
                            aria-label={capture.state === 'listening' ? tv('stop-asking') : tv('ask-by-voice')}
                            aria-pressed={capture.state === 'listening'} disabled={capture.state === 'idle' && answer.isPending}
                            onClick={() => void askByVoice()}>
                            {capture.state === 'processing' ? <Loader2 className="animate-spin"/>
                                : capture.state === 'listening' ? <Square/> : <Mic/>}
                        </Button>}
                        <Button type="button" variant="ghost" disabled={answer.isPending}
                            onClick={() => setQuestion('What was just explained?')}>What was just explained?</Button>
                        {onUseCurrentTime && <Button type="button" variant="link" onClick={onUseCurrentTime}>Use current time</Button>}
                        <Button type="submit" disabled={!question.trim() || answer.isPending || service.isPending || service.isError}>
                            {answer.isPending ? 'Finding an answer…' : `${service.data?.configured ? 'Ask' : 'Find passages'} at ${clock(position)}`}
                        </Button>
                    </div>
                </form>
                {service.isPending && <p role="status" className="text-sm text-muted-foreground mt-3">Checking answer availability…</p>}
                {service.isError && <p role="alert" className="text-sm mt-3">Answer availability couldn't load. <Button variant="link" onClick={() => void service.refetch()}>Try again</Button></p>}
                {service.data?.configured === false && <p className="ask-connect text-sm text-muted-foreground mt-3"><Trans t={t} i18nKey="ask-not-connected" components={{settings: <Link to="/settings/ai"/>}}/></p>}
                {capture.state !== 'idle' && <p role="status" className="text-sm text-muted-foreground mt-3">{capture.state === 'listening' ? tv('listening') : tv('processing')}</p>}
                {voiceError && <p role="alert" className="text-sm text-destructive mt-3">{voiceError}</p>}
                {speechRecognitionCtor() && <label className="voice-recognition-opt-in">
                    <input type="checkbox" checked={useBrowserRecognition} onChange={event => {
                        setUseBrowserRecognition(event.target.checked)
                        setBrowserRecognitionEnabled(event.target.checked)
                    }}/>
                    <span>{tv('browser-recognition-label')}. {tv('browser-recognition-note')}</span>
                </label>}
                {answer.isPending && <p role="status" className="text-sm text-muted-foreground mt-3">Using this episode at {clock(answer.variables.position)}. You can keep listening.</p>}
                {answer.isError && <p role="alert" className="text-sm text-destructive mt-3">{answer.error.message} Your question has been kept.</p>}
                {result && <div className="episode-answer" aria-label="Episode answer">
                    <p className="text-sm font-medium">“{result.question}” · Asked at {clock(result.position)}</p>
                    <p role="status" className="text-sm text-muted-foreground mt-2">
                        {result.status === 'answered' ? 'AI answer · Check the linked passages against what you heard.'
                            : result.status === 'not_configured' ? 'Source passages found. No AI answer was generated.'
                            : result.status === 'no_timed_transcript' ? 'A timed transcript is needed to answer with source links. You can still listen and save notes.'
                            : 'These passages do not provide enough evidence to answer. Try a more specific question or another moment.'}
                    </p>
                    {result.claims.map((claim, index) => <div className="answer-claim" key={index}>
                        <p>{claim.text}</p>
                        <div className="flex flex-wrap gap-2 mt-2">{claim.citations.map(id => {
                            const source = result.passages.find(passage => passage.id === id)
                            return source && <Button key={id} variant="outline" size="sm"
                                aria-label={`Play answer source at ${clock(source.start)}`} onClick={() => onSeek(source.start)}>
                                <Play size={12}/> {clock(source.start)}
                            </Button>
                        })}</div>
                    </div>)}
                    {result.passages.length > 0 && <details className="answer-sources" open={result.status !== 'answered' ? true : undefined}>
                        <summary>Source passages ({result.passages.length})</summary>
                        <ol>{result.passages.map(passage => <li key={passage.id}>
                            <Button variant="link" aria-label={`Play question source at ${clock(passage.start)}`} onClick={() => onSeek(passage.start)}>
                                <Play size={12}/> {clock(passage.start)}
                            </Button><p>{passage.text}</p>
                        </li>)}</ol>
                    </details>}
                </div>}
            </CardContent>
        </Card>
    </section>
}
