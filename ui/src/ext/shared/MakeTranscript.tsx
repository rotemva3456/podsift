import {useEffect, useRef, useState} from 'react'
import {Trans, useTranslation} from 'react-i18next'
import {useQuery, useQueryClient} from '@tanstack/react-query'
import {FileText, LoaderCircle} from 'lucide-react'
import {Button} from '../../components/ui/button'
import type {PodcastEpisode} from '../types'
import {podfetch} from './podfetch'

type Entry = {source: string; status: string; error?: string | null}
type Step = 'idle' | 'starting' | 'downloading' | 'making' | 'slow' | 'off' | 'not-allowed' | 'error'

export const POLL_MS = 5000
export const MAKING_LIMIT_MS = 30 * 60 * 1000
const DOWNLOAD_POLL_MS = 3000
const DOWNLOAD_LIMIT_MS = 30 * 60 * 1000
const TOO_LARGE = /413|too large|payload|file size|size limit/i
const hasParsed = (entries?: Entry[]) => entries?.some(entry => entry.status === 'parsed') ?? false
const isRunning = (entries?: Entry[]) => entries?.some(e => e.source === 'generated' && ['pending', 'running'].includes(e.status)) ?? false
const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

async function listTranscripts(id: string): Promise<Entry[]> {
    const response = await podfetch(`/api/v1/podcasts/episodes/${encodeURIComponent(id)}/transcripts`)
    if (!response.ok) throw new Error(`PodFetch answered ${response.status}`)
    return response.json()
}

async function isDownloaded(episodeId: string): Promise<boolean> {
    const response = await podfetch(`/api/v1/episodes/${encodeURIComponent(episodeId)}`)
    if (!response.ok) throw new Error('make-transcript-error')
    return Boolean((await response.json())?.podcastEpisode?.status)
}

/**
 * The one "no transcript" action for every feature. It asks PodFetch to transcribe the episode
 * (PodFetch's Whisper works on the downloaded file, so it downloads the episode first when needed),
 * polls until a transcript exists, then calls `onReady`. A failed job shows its reason and a retry;
 * polling stops after MAKING_LIMIT_MS. Give it `key={episode.id}`.
 */
export function MakeTranscript({episode, onReady}: {episode: PodcastEpisode; onReady: () => void}) {
    const {t} = useTranslation('shared')
    const client = useQueryClient()
    const [step, setStep] = useState<Step>('idle')
    const [problem, setProblem] = useState('make-transcript-error')
    const stepRef = useRef(step), since = useRef(0), deadline = useRef(0), mounted = useRef(true), ready = useRef(onReady)
    stepRef.current = step
    ready.current = onReady
    useEffect(() => {mounted.current = true; return () => {mounted.current = false}}, [])
    const list = useQuery({queryKey: ['make-transcript', episode.id], queryFn: () => listTranscripts(episode.id), retry: false,
        refetchInterval: query => stepRef.current === 'making' && !hasParsed(query.state.data) ? POLL_MS : false})
    const generated = list.data?.find(entry => entry.source === 'generated')
    const exists = hasParsed(list.data)

    const follow = () => {
        since.current = Date.now()
        deadline.current = since.current + MAKING_LIMIT_MS
        setStep('making')
        void list.refetch()
    }
    useEffect(() => {
        if (!exists) return
        // The new words may replace publisher timing or a cached "no transcript".
        void client.invalidateQueries({queryKey: ['cuts', 'skip-segments', episode.episode_id]})
        ready.current()
    }, [exists, client, episode.episode_id])
    // Arriving while PodFetch is already working on this episode.
    useEffect(() => {if (step === 'idle' && !exists && isRunning(list.data)) follow()}, [step, exists, list.data])
    // Every poll after we started following the job, answered or not.
    useEffect(() => {
        if (step !== 'making' || exists) return
        const fresh = list.dataUpdatedAt > since.current
        if (fresh && generated?.status === 'failed') setStep('idle')
        else if (fresh && !generated) {setProblem('make-transcript-missing'); setStep('error')}
        else if (Date.now() > deadline.current) setStep('slow')
    }, [step, exists, generated, list.dataUpdatedAt, list.errorUpdatedAt])

    const transcribe = () => podfetch(`/api/v1/podcasts/episodes/${encodeURIComponent(episode.id)}/transcribe`, {method: 'POST'})
    const start = async () => {
        setStep('starting')
        try {
            let response = await transcribe()
            if (response.status === 503) return void setStep('off')
            if (response.status === 401 || response.status === 403) return void setStep('not-allowed')
            if (response.status !== 200 && response.status !== 409) throw new Error('make-transcript-error')
            if (!episode.status && !(await isDownloaded(episode.episode_id))) {
                if (!mounted.current) return
                setStep('downloading')
                // Despite the path, PodFetch wants the episode's episode_id here.
                const download = await podfetch(`/api/v1/podcasts/${encodeURIComponent(episode.episode_id)}/episodes/download`, {method: 'PUT'})
                if (download.status === 401 || download.status === 403) return void setStep('not-allowed')
                if (!download.ok) throw new Error('make-transcript-download-error')
                const until = Date.now() + DOWNLOAD_LIMIT_MS
                while (!(await isDownloaded(episode.episode_id))) {
                    if (!mounted.current) return
                    if (Date.now() > until) throw new Error('make-transcript-download-error')
                    await sleep(DOWNLOAD_POLL_MS)
                }
                // The job queued before the file existed may have failed by now; this queues it again.
                response = await transcribe()
                if (response.status !== 200 && response.status !== 409) throw new Error('make-transcript-error')
            }
            if (mounted.current) follow()
        } catch (error) {
            if (!mounted.current) return
            setProblem(error instanceof Error && error.message.startsWith('make-transcript-') ? error.message : 'make-transcript-error')
            setStep('error')
        }
    }

    const busy = (text: string) => <p role="status" className="flex items-center gap-2"><LoaderCircle size={16} className="animate-spin"/>{text}</p>
    const retry = <Button variant="outline" onClick={() => void start()}>{t('try-again')}</Button>
    const reason = generated?.error || ''
    let body
    if (exists) body = <><p role="status">{t('make-transcript-ready')}</p><Button variant="outline" onClick={() => ready.current()}>{t('make-transcript-show')}</Button></>
    else if (step === 'off') body = <p role="status"><Trans t={t} i18nKey="make-transcript-off" components={{code: <code/>}}/></p>
    else if (step === 'not-allowed') body = <p role="status">{t('make-transcript-not-allowed')}</p>
    else if (step === 'error') body = <><p role="alert">{t(problem)}</p>{retry}</>
    else if (step === 'downloading') body = busy(t('make-transcript-downloading'))
    else if (step === 'making') body = busy(t('make-transcript-making'))
    else if (step === 'slow') body = <><p role="status">{t('make-transcript-slow')}</p><Button variant="outline" onClick={follow}>{t('make-transcript-check')}</Button></>
    else if (step === 'idle' && generated?.status === 'failed') body = <><p role="alert">{t('make-transcript-failed', {reason: reason || '—', interpolation: {escapeValue: false}})}{TOO_LARGE.test(reason) && ' ' + t('make-transcript-too-large')}</p>{retry}</>
    else body = <><Button onClick={() => void start()} disabled={step === 'starting'}>
        {step === 'starting' ? <LoaderCircle className="animate-spin"/> : <FileText/>}{t(step === 'starting' ? 'make-transcript-starting' : 'make-transcript')}
    </Button><p>{t('make-transcript-hint')}</p></>
    return <div className="make-transcript">{body}</div>
}
