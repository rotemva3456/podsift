// The player action: a big mic button that records a hands-free note at the current position.
import {useRef} from 'react'
import {useTranslation} from 'react-i18next'
import {useMutation, useQuery} from '@tanstack/react-query'
import {Loader2, Mic, Square} from 'lucide-react'
import {Button} from '../../../components/ui/button'
import {companion} from '../../../utils/companion'
import {getAudioPlayer} from '../../../utils/audioPlayer'
import {enqueueSnackbar} from '../../../utils/toast'
import {getSpeechStatus} from './api'
import {playChime} from './chime'
import {browserRecognitionEnabled, canCapture} from './settings'
import {useVoiceCapture} from './useVoiceCapture'
import './voice.css'

export function VoiceNoteButton({episodeId, position}: {episodeId: string; position: number}) {
    const {t} = useTranslation('voice')
    const capture = useVoiceCapture()
    // Cached with react-query like the Ask panel's own check (AskEpisode.tsx): the default
    // (server-transcription) path needs a speech-capable AI provider, and the driver must know
    // that BEFORE they talk, not after wasting up to 30 s on a recording that can't be used.
    const speechService = useQuery({queryKey: ['speech-service'], queryFn: getSpeechStatus, retry: false})
    const wasPlaying = useRef(false)
    const save = useMutation({
        mutationFn: (text: string) => companion('/notes', {
            method: 'POST',
            body: JSON.stringify({id: crypto.randomUUID(), episode_id: episodeId, position, text}),
        }),
    })
    if (!canCapture()) return null

    const busy = capture.state !== 'idle'
    const onClick = async () => {
        if (busy) {
            capture.stop()
            return
        }
        if (!browserRecognitionEnabled() && speechService.data?.configured === false) {
            playChime(false)
            enqueueSnackbar(t('need-speech'), {variant: 'error'})
            return
        }
        const audio = getAudioPlayer()
        wasPlaying.current = !!audio && !audio.paused
        audio?.pause()
        try {
            const text = await capture.start()
            await save.mutateAsync(text)
            playChime(true)
            enqueueSnackbar(t('note-saved'), {variant: 'success'})
        } catch (error) {
            playChime(false)
            enqueueSnackbar(error instanceof Error ? error.message : t('note-error'), {variant: 'error'})
        } finally {
            if (wasPlaying.current) void getAudioPlayer()?.play().catch(() => {})
        }
    }

    const label = capture.state === 'listening' ? t('stop-recording')
        : capture.state === 'processing' ? t('processing') : t('record-note')
    return <Button type="button" variant={capture.state === 'listening' ? 'destructive' : 'ghost'} size="icon-lg"
        className="voice-mic-button" aria-label={label} aria-pressed={capture.state === 'listening'}
        disabled={capture.state === 'processing'} onClick={() => void onClick()}>
        {capture.state === 'processing' ? <Loader2 className="animate-spin"/> : capture.state === 'listening' ? <Square/> : <Mic/>}
    </Button>
}
