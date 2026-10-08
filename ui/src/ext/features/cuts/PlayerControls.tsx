// The player bar: Smart Play status and stop, the "Skip sponsors" switch, skip notices, and the
// kept and sponsor parts drawn on the progress bar.
import {useEffect, useLayoutEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {Link} from 'react-router-dom'
import {useTranslation} from 'react-i18next'
import {useMutation, useQuery, useQueryClient} from '@tanstack/react-query'
import {Megaphone, MegaphoneOff, Scissors, X} from 'lucide-react'
import {Button} from '../../../components/ui/button'
import useAudioPlayer from '../../../store/AudioPlayerSlice'
import {playsDownloadedFile, skipSponsorPassage, SkipRanges, undoLastSkip} from '../../../utils/audioPlayer'
import {clock, fetchEpisode, playEpisode} from '../../../utils/listening'
import {getSkipPreferences, getSkipSegments, getSponsorSettings, putSponsorSettings, SKIP_PREFERENCES_KEY,
    SPONSOR_SETTINGS_KEY, type SponsorSettings} from './api'
import {nativeSkipChoices, skipTimingMismatch} from './sponsors'
import {keptSeconds, shortMinutes} from './smartPlay'
import {useDownload} from './useDownload'

export const NOTICE_MS = 5000
type SponsorStatus = 'off' | 'waiting' | 'loading' | 'error' | 'needs-transcript' | 'timing-unchecked' | 'timing-mismatch' | 'ready'

/** Keeps the store's sponsor parts in step with the switch and the playing episode's file. */
export function useSponsorSkip(episodeId: string) {
    const episode = useAudioPlayer(state => state.loadedPodcastEpisode?.podcastEpisode)
    const source = useAudioPlayer(state => state.mediaSource)
    const duration = useAudioPlayer(state => state.metadata?.duration)
    const settings = useQuery({queryKey: SPONSOR_SETTINGS_KEY, queryFn: getSponsorSettings, staleTime: 60_000, retry: false})
    const on = settings.data?.enabled ?? false
    const file = episode?.episode_id === episodeId && playsDownloadedFile(source, episode)
    const preferences = useQuery({queryKey: SKIP_PREFERENCES_KEY, queryFn: getSkipPreferences,
        enabled: on && file, retry: false, staleTime: 60_000})
    const found = useQuery({queryKey: ['cuts', 'skip-segments', episodeId], queryFn: () => getSkipSegments(episodeId),
        enabled: on && file, retry: false, staleTime: 60_000})
    const parts = on && file && settings.data && found.data && preferences.data && !preferences.isError && !found.isError
        ? nativeSkipChoices(found.data, settings.data, preferences.data, duration) : null
    const status: SponsorStatus = settings.isError ? 'error' : settings.isPending ? 'loading'
        : !on ? 'off' : !file ? 'waiting' : found.isError || preferences.isError ? 'error'
        : !found.data || !preferences.data ? 'loading' : !found.data.timed ? 'needs-transcript'
        : skipTimingMismatch(found.data, duration) ? 'timing-mismatch'
        : !found.data.auto_skip_safe || found.data.timing_status !== 'matched' ? 'timing-unchecked' : 'ready'
    const spans = JSON.stringify(parts ? {spans: parts.spans, manualSpans: parts.manual.map(s => [s.start, s.end]), duration: found.data?.duration} : null)
    useEffect(() => {
        const found = JSON.parse(spans) as {spans: [number, number][]; manualSpans: [number, number][]; duration: number | null} | null
        useAudioPlayer.getState().setSponsorSkip(found && (found.spans.length || found.manualSpans.length)
            ? {episodeId, spans: found.spans, ...(found.manualSpans.length ? {manualSpans: found.manualSpans} : {}),
                ...(found.duration ? {duration: found.duration} : {})} : null)
    }, [episodeId, spans])
    useEffect(() => () => useAudioPlayer.getState().setSponsorSkip(null), [])
    const client = useQueryClient()
    const toggle = useMutation({
        mutationFn: putSponsorSettings,
        onMutate: (next: SponsorSettings) => {
            const previous = client.getQueryData<SponsorSettings>(SPONSOR_SETTINGS_KEY)
            client.setQueryData(SPONSOR_SETTINGS_KEY, next)
            return {previous}
        },
        onError: (_error, _next, context) => client.setQueryData(SPONSOR_SETTINGS_KEY, context?.previous),
        onSuccess: saved => client.setQueryData(SPONSOR_SETTINGS_KEY, saved),
    })
    return {settings, on, status, parts, toggle: () => settings.data && !toggle.isPending && toggle.mutate({...settings.data, enabled: !on}),
        togglePending: toggle.isPending, toggleError: toggle.isError,
        retry: () => {void settings.refetch(); if (on && file) {void found.refetch(); void preferences.refetch()}}}
}

export function PlayerControls({episodeId}: {episodeId: string; position: number}) {
    const {t} = useTranslation('cuts')
    const plan = useAudioPlayer(state => state.skipPlan?.episodeId === episodeId ? state.skipPlan : null)
    const duration = useAudioPlayer(state => state.metadata?.duration) ?? 0
    const position = useAudioPlayer(state => state.metadata?.currentTime) ?? 0
    const notice = useAudioPlayer(state => state.skipNotice)
    const replay = useAudioPlayer(state => state.skipReplay)
    const sponsors = useSponsorSkip(episodeId)
    const [shown, setShown] = useState<{notice?: number; status?: number; dismissedStatus?: SponsorStatus}>({})
    const [track, setTrack] = useState<Element | null>(null)
    const anchor = useRef<HTMLSpanElement>(null)
    // The drawer's progress bar is not ours to edit: draw the ranges into its track.
    useLayoutEffect(() => setTrack(anchor.current?.closest('.listen-player')?.querySelector('.player-progress [data-slot="slider-track"]') ?? null), [])
    useEffect(() => setShown(value => ({...value, dismissedStatus: undefined})), [episodeId, sponsors.status])
    useEffect(() => {
        if (!notice) return
        setShown(value => ({...value, notice: notice.at}))
        const timer = setTimeout(() => setShown(value => value.notice === notice.at ? {...value, notice: undefined} : value), NOTICE_MS)
        return () => clearTimeout(timer)
    }, [notice])
    // The switch's answer stays while it offers a download; otherwise it fades like a notice.
    useEffect(() => {
        const since = shown.status
        if (!since || ['waiting', 'error', 'timing-mismatch', 'needs-transcript'].includes(sponsors.status)) return
        const timer = setTimeout(() => setShown(value => value.status === since ? {...value, status: undefined} : value), NOTICE_MS)
        return () => clearTimeout(timer)
    }, [shown.status, sponsors.status])
    const download = useDownload(async () => {
        // Play on from the file, so the skips land where they should.
        const state = useAudioPlayer.getState(), audio = state.loadedPodcastEpisode?.podcastEpisode
        if (audio?.episode_id !== episodeId) return
        const position = state.pendingSeek ?? state.metadata?.currentTime ?? 0
        const {podcastEpisode} = await fetchEpisode(episodeId)
        await playEpisode(podcastEpisode, position, state.isPlaying)
    })
    const sponsorText = sponsors.status === 'off' ? t('sponsors-off')
        : sponsors.status === 'waiting' ? t('sponsors-waiting')
        : sponsors.status === 'loading' ? t('sponsors-loading')
        : sponsors.status === 'error' ? t('sponsors-error')
        : sponsors.status === 'needs-transcript' ? t('sponsors-needs-transcript')
        : sponsors.status === 'timing-unchecked' ? t('sponsors-timing-unchecked')
        : sponsors.status === 'timing-mismatch' ? t('sponsors-timing-mismatch')
        : sponsors.parts?.spans.length ? t('sponsors-on', {count: sponsors.parts.spans.length, time: clock(sponsors.parts.spans.reduce((s, [a, b]) => s + b - a, 0))})
        : sponsors.parts?.manual.length ? t('sponsors-manual-only')
        : t('sponsors-on-none')
    const manualText = sponsors.parts?.manual.length ? ' ' + t('sponsors-manual-parts', {count: sponsors.parts.manual.length}) : ''
    const unsure = (sponsors.parts?.unsure ? ' ' + t('sponsors-unsure', {count: sponsors.parts.unsure}) : '') + manualText
    const manual = !replay ? sponsors.parts?.manual.find(span => position >= span.start && position < span.end) : undefined
    const close = () => setShown({dismissedStatus: sponsors.status})
    const closeButton = <Button variant="ghost" size="icon-xs" aria-label={t('close')} onClick={close}><X/></Button>
    let bubble = null
    if (shown.notice && notice?.at === shown.notice) bubble = <>
        <span>{t(`notice-${notice.kind}`)}</span>
        {notice.passage?.episodeId === episodeId && <Button variant="outline" size="xs"
            onClick={() => {if (undoLastSkip()) close()}}>{t('undo-skip')}</Button>}
        {notice.kind === 'skipped' && plan && <Button variant="outline" size="xs" onClick={() => useAudioPlayer.getState().stopSmartPlay()}>{t('stop-smart-play')}</Button>}
        {closeButton}</>
    else if (shown.status || (shown.dismissedStatus !== sponsors.status && ['error', 'timing-mismatch', 'needs-transcript'].includes(sponsors.status))) bubble = <>
        <span>{sponsors.toggleError ? t('sponsors-switch-error') : sponsorText + unsure}</span>
        {sponsors.status === 'error' && <Button variant="outline" size="xs" onClick={sponsors.retry}>{t('try-again')}</Button>}
        {['timing-mismatch', 'needs-transcript'].includes(sponsors.status) && <Button variant="outline" size="xs"
            render={<Link to={`/learn?episode=${encodeURIComponent(episodeId)}`}/>}>{t('open-transcript')}</Button>}
        {sponsors.status === 'waiting' && (download.state === 'not-allowed' ? <span>{t('download-not-allowed')}</span>
            : download.state === 'error' ? <Button variant="outline" size="xs" onClick={() => void download.start([episodeId])}>{t('try-again')}</Button>
            : <Button variant="outline" size="xs" disabled={download.state === 'downloading'} onClick={() => void download.start([episodeId])}>
                {t(download.state === 'downloading' ? 'downloading' : 'download')}</Button>)}
        {closeButton}</>
    else if (manual) bubble = <>
        <span title={manual.reason}>{t('manual-skip-passage', {category: t(`category-${manual.category}`)})}</span>
        <Button variant="outline" size="xs" title={manual.reason}
            onClick={() => skipSponsorPassage(episodeId, [manual.start, manual.end])}>{t('skip-this-part')}</Button>
    </>
    const stopLabel = plan ? t('player-stop', {kept: shortMinutes(keptSeconds([plan])), total: shortMinutes(duration || plan.keep.at(-1)?.[1] || 0)}) : ''
    return <span ref={anchor} className="cut-player">
        {plan && <Button variant="ghost" size="sm" className="cut-smart-chip" onClick={() => useAudioPlayer.getState().stopSmartPlay()}
            aria-label={stopLabel} title={stopLabel}>
            <Scissors/><span className="cut-player-label">{t('player-smart')}</span><X className="cut-player-stop"/>
        </Button>}
        <Button variant="ghost" size="icon" className="cut-sponsor-switch" data-status={sponsors.status} aria-pressed={sponsors.on}
            aria-label={t('player-sponsors')} title={sponsorText + unsure} disabled={!sponsors.settings.data || sponsors.togglePending}
            onClick={() => {sponsors.toggle(); setShown({status: Date.now()})}}>
            {sponsors.on ? <MegaphoneOff/> : <Megaphone/>}
        </Button>
        <span className="cut-notice" role="status">{bubble}</span>
        {track && createPortal(<SkipRanges/>, track)}
    </span>
}
