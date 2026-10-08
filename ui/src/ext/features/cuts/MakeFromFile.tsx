// "Needs timing": the transcript's times don't fit the downloaded file (a publisher transcript made
// for other audio, or one without times). PodFetch's Whisper makes one from the file itself, and the
// companion prefers that one. Shared/MakeTranscript only covers "no transcript at all".
import {useEffect, useRef, useState} from 'react'
import {useTranslation} from 'react-i18next'
import {useQuery} from '@tanstack/react-query'
import {Download, FileText, LoaderCircle} from 'lucide-react'
import {Button} from '../../../components/ui/button'
import {fetchEpisode} from '../../../utils/listening'
import {podfetch} from '../../shared/podfetch'
import {useDownload} from './useDownload'

export const MAKE_POLL_MS = 5000
type Entry = {source: string; status: string}
type Step = 'idle' | 'making' | 'off' | 'not-allowed' | 'error'

export function MakeFromFile({episodeId, onReady, label}: {episodeId: string; onReady: () => void; label?: string}) {
    const {t} = useTranslation('cuts')
    const [step, setStep] = useState<Step>('idle')
    const since = useRef(0), ready = useRef(onReady)
    ready.current = onReady
    const episode = useQuery({queryKey: ['listen-episode', episodeId], queryFn: () => fetchEpisode(episodeId)})
    const internal = episode.data?.podcastEpisode.id
    const list = useQuery({queryKey: ['cuts', 'transcripts', internal], enabled: step === 'making' && !!internal, retry: false,
        refetchInterval: MAKE_POLL_MS, queryFn: async (): Promise<Entry[]> => {
            const response = await podfetch(`/api/v1/podcasts/episodes/${encodeURIComponent(internal!)}/transcripts`)
            if (!response.ok) throw new Error(`PodFetch answered ${response.status}`)
            return response.json()
        }})
    useEffect(() => {
        if (step !== 'making' || !list.data || list.dataUpdatedAt <= since.current) return
        const generated = list.data.find(entry => entry.source === 'generated')
        if (generated?.status === 'parsed') {setStep('idle'); ready.current()}
        else if (generated?.status === 'failed') setStep('error')
    }, [step, list.data, list.dataUpdatedAt])
    const download = useDownload()
    const start = async () => {
        since.current = Date.now()
        try {
            const response = await podfetch(`/api/v1/podcasts/episodes/${encodeURIComponent(internal!)}/transcribe`, {method: 'POST'})
            setStep(response.status === 503 ? 'off' : [401, 403].includes(response.status) ? 'not-allowed'
                : [200, 409].includes(response.status) ? 'making' : 'error')
        } catch {setStep('error')}
    }
    if (!episode.data) return null
    const name = label ? <strong>{label}: </strong> : null
    // Whisper works on the downloaded file, so download first.
    if (!episode.data.podcastEpisode.status) return <div className="cut-make">{name}
        {download.state === 'not-allowed' ? <span>{t('download-not-allowed')}</span>
            : <Button variant="outline" size="sm" disabled={download.state === 'downloading'} onClick={() => void download.start([episodeId])}>
                {download.state === 'downloading' ? <LoaderCircle className="animate-spin"/> : <Download/>}{t(download.state === 'downloading' ? 'downloading' : 'download')}</Button>}
        {download.state === 'error' && <span role="alert">{t('download-error')}</span>}</div>
    return <div className="cut-make">{name}
        {step === 'making' ? <span role="status"><LoaderCircle size={14} className="animate-spin inline"/> {t('making-from-file')}</span>
            : step === 'off' ? <span role="status">{t('make-off')}</span>
            : step === 'not-allowed' ? <span role="status">{t('make-not-allowed')}</span>
            : <><Button variant="outline" size="sm" disabled={!internal} onClick={() => void start()}><FileText/>{t('make-from-file')}</Button>
                {step === 'error' && <span role="alert">{t('make-error')}</span>}</>}
    </div>
}
