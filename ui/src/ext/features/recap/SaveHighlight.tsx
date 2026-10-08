// The player action: "Save last 30 s", also bound to the `h` key. Saves a
// highlight — the transcript quote for that range — through the same POST /companion/notes a
// plain note uses (voice notes share it too).
import {useEffect, useRef} from 'react'
import {useTranslation} from 'react-i18next'
import {useMutation, useQuery} from '@tanstack/react-query'
import {Highlighter} from 'lucide-react'
import {Button} from '../../../components/ui/button'
import {companion, type Passage, type Transcript} from '../../../utils/companion'
import {enqueueSnackbar} from '../../../utils/toast'
import {saveNote} from './api'

export const LOOKBACK_SECONDS = 30
const KEY = 'h'

/** Every segment touching [start, end), joined into one quote. */
export function quoteFor(segments: Passage[], start: number, end: number): string {
    return segments.filter(seg => seg.start < end && (seg.end ?? Infinity) > start)
        .map(seg => seg.text.trim()).filter(Boolean).join(' ')
}

function typingInField(target: EventTarget | null): boolean {
    const el = target as HTMLElement | null
    return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)
}

export function SaveHighlight({episodeId, position}: {episodeId: string; position: number}) {
    const {t} = useTranslation('recap')
    const transcript = useQuery({queryKey: ['transcript', episodeId], retry: false, staleTime: Infinity,
        queryFn: () => companion<Transcript>(`/episodes/${encodeURIComponent(episodeId)}/transcript`)})
    const segments = transcript.data?.segments ?? []
    const positionRef = useRef(position)
    positionRef.current = position

    const save = useMutation({
        mutationFn: async () => {
            const end = positionRef.current
            const start = Math.max(0, end - LOOKBACK_SECONDS)
            const quote = quoteFor(segments, start, end)
            if (!quote) throw new Error(t('no-quote'))
            return saveNote({id: crypto.randomUUID(), episode_id: episodeId, position: start, text: quote,
                kind: 'highlight', start, end, quote})
        },
        onSuccess: () => enqueueSnackbar(t('saved'), {variant: 'success'}),
        onError: (error: Error) => enqueueSnackbar(error.message || t('save-error'), {variant: 'error'}),
    })
    const trigger = save.mutate

    useEffect(() => {
        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key !== KEY || event.ctrlKey || event.metaKey || event.altKey || typingInField(event.target)) return
            event.preventDefault()
            trigger()
        }
        document.addEventListener('keydown', onKeyDown)
        return () => document.removeEventListener('keydown', onKeyDown)
    }, [trigger])

    const disabled = save.isPending || segments.length === 0 || position <= 0
    const label = t('save-last', {seconds: LOOKBACK_SECONDS})
    return <Button type="button" variant="ghost" size="icon" className="save-highlight-button" aria-label={label}
        title={segments.length === 0 ? t('no-transcript-title') : label} disabled={disabled}
        onClick={() => trigger()}>
        <Highlighter/>
    </Button>
}
