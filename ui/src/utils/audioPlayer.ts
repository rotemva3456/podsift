// Modified by Podsift contributors, 2026-09-22. See CHANGES.md.
import {createElement, type ReactElement} from 'react'
import useAudioPlayer, {type SkipPlan, type TimeRange} from '../store/AudioPlayerSlice'
export const getAudioPlayer = () => document.getElementById('audio-player') as HTMLMediaElement | null
export const isVideoUrl = (url?: string | null): boolean => ['mp4','m4v','mov','webm'].includes((url?.split('?')[0]?.split('.').pop() ?? '').toLowerCase())
let cancelSeek: (()=>void) | undefined
let generation = 0
export const startAudioPlayer = async (audioUrl: string, position: number, autoplay = true) => {
    const audio = getAudioPlayer()
    if (!audio) return
    const mediaUrl = new URL(audioUrl, location.href)
    if (mediaUrl.pathname.startsWith('/proxy/') || mediaUrl.pathname.startsWith('/podcasts/')) {
        audioUrl = location.origin + mediaUrl.pathname + mediaUrl.search
    }
    const current = ++generation
    cancelSeek?.()
    const target = Number.isFinite(position) && position > 0 ? position : 0
    useAudioPlayer.setState({pendingSeek:target, playbackError:undefined})
    let applied=false
    const apply = () => {
        if (current!==generation || applied || audio.readyState<1) return
        const duration=Number.isFinite(audio.duration)?audio.duration:useAudioPlayer.getState().metadata?.duration ?? 0
        const time=duration>0 ? Math.min(target,duration) : target
        audio.currentTime=time
        applied=true
        useAudioPlayer.setState({pendingSeek:undefined,metadata:{currentTime:time,duration,percentage:duration?time/duration*100:0}})
        cleanup()
    }
    const fail = () => {if(current===generation) useAudioPlayer.setState({playbackError:'This episode could not play. Check your connection and try again.',isPlaying:false});cleanup()}
    const cleanup = () => {audio.removeEventListener('loadedmetadata',apply);audio.removeEventListener('canplay',apply);audio.removeEventListener('error',fail)}
    cancelSeek=cleanup
    audio.addEventListener('loadedmetadata',apply)
    audio.addEventListener('canplay',apply)
    audio.addEventListener('error',fail)
    if (audio.src !== new URL(audioUrl,location.href).href) {audio.pause();audio.src=audioUrl;audio.load()} else apply()
    audio.playbackRate=useAudioPlayer.getState().playBackRate
    if (!autoplay) {audio.pause();return}
    try {await audio.play();apply()}
    catch (error) {
        if(current!==generation || (error as DOMException)?.name==='AbortError') return
        useAudioPlayer.setState({playbackError:'Playback did not start. Press play to try again.',isPlaying:false})
    }
}

// ---- Smart Play and sponsor skipping ----
// Times are seconds from the start of the episode's downloaded file. The player never skips
// against a stream: a stream can carry different ads on every request.

/** How often the player checks its position while something is to be skipped. */
export const SKIP_CHECK_MS = 50
/** A position this close before a kept start counts as inside it: a seek can land a little early. */
export const EARLY_S = 0.25
/** After the user seeks, wait this long before skipping, so a drag on the bar is not fought. */
export const SEEK_GRACE_MS = 250
const MERGE_S = 0.01

/** Valid ranges, sorted and merged; ends are clamped to `limit` (Infinity means "to the end"). */
export function normalizeRanges(ranges: readonly (readonly [number, number])[], limit = Infinity): TimeRange[] {
    const valid = ranges
        .map(([start, end]): TimeRange => [Math.max(0, start), Math.min(end, limit)])
        .filter(([start, end]) => Number.isFinite(start) && !Number.isNaN(end) && end - start > MERGE_S)
        .sort((a, b) => a[0] - b[0])
    const merged: TimeRange[] = []
    for (const [start, end] of valid) {
        const last = merged[merged.length - 1]
        if (last && start <= last[1] + MERGE_S) last[1] = Math.max(last[1], end)
        else merged.push([start, end])
    }
    return merged
}

/** `keep` with every `remove` range taken out. */
export function subtractRanges(keep: readonly (readonly [number, number])[], remove: readonly (readonly [number, number])[]): TimeRange[] {
    const cuts = normalizeRanges(remove), left: TimeRange[] = []
    for (const [start, end] of normalizeRanges(keep)) {
        let from = start
        for (const [cutStart, cutEnd] of cuts) {
            if (cutEnd <= from || cutStart >= end) continue
            if (cutStart > from) left.push([from, cutStart])
            from = cutEnd
            if (from >= end) break
        }
        if (from < end) left.push([from, end])
    }
    return normalizeRanges(left)
}

export type SkipDecision = {kind: 'play'} | {kind: 'jump', to: number} | {kind: 'end'}

/** What to do at `position`, given sorted kept ranges: keep playing, jump to the next kept start, or stop. */
export function skipDecision(keep: readonly TimeRange[], position: number): SkipDecision {
    for (const [start, end] of keep) {
        if (position < start - EARLY_S) return {kind: 'jump', to: start}
        if (position < end) return {kind: 'play'}
    }
    return {kind: 'end'}
}

/** True when `source` (the media element's src) is the episode's downloaded file, not the stream (PodFetch's /proxy/). */
export function playsDownloadedFile(source: string | undefined, episode?: {status?: boolean; local_url?: string}): boolean {
    if (!episode?.status || !episode.local_url || !source) return false
    try {
        const actual = new URL(source, location.href), expected = new URL(episode.local_url, location.href)
        return !actual.pathname.startsWith('/proxy/') && actual.pathname === expected.pathname && actual.search === expected.search
            && (actual.origin === expected.origin || actual.origin === location.origin)
    } catch {return false}
}
const sourceOf = (audio: Pick<HTMLMediaElement, 'src' | 'currentSrc'>) => audio.currentSrc || audio.src

export type ActiveSkips = {keep: TimeRange[], sponsors: TimeRange[], smart: boolean}

/** What the player must skip right now, or null when nothing applies (no plan, a stream, a pending seek). */
export function activeSkips(state: ReturnType<typeof useAudioPlayer.getState>, audio: Pick<HTMLMediaElement, 'src' | 'currentSrc' | 'duration'>): ActiveSkips | null {
    const episode = state.loadedPodcastEpisode?.podcastEpisode
    if (!episode) return null
    const plan = state.skipPlan?.episodeId === episode.episode_id ? state.skipPlan : null
    const sponsorSkip = state.sponsorSkip?.episodeId === episode.episode_id ? state.sponsorSkip : null
    const durationMatches = !sponsorSkip?.duration || !Number.isFinite(audio.duration) || Math.abs(sponsorSkip.duration - audio.duration) <= 1
    const sponsors = sponsorSkip && durationMatches ? normalizeRanges(sponsorSkip.spans) : []
    if ((!plan && !sponsors.length) || state.pendingSeek !== undefined || !playsDownloadedFile(sourceOf(audio), episode)) return null
    return {keep: subtractRanges(plan ? plan.keep : [[0, Infinity]], sponsors), sponsors, smart: !!plan}
}

/** Undo a real jump, and let that passage play once before the existing skips resume. */
export function undoLastSkip(audio = getAudioPlayer()): boolean {
    const state = useAudioPlayer.getState(), passage = state.skipNotice?.passage
    const episode = state.loadedPodcastEpisode?.podcastEpisode
    if (!audio || !passage || episode?.episode_id !== passage.episodeId
        || sourceOf(audio) !== passage.source || !playsDownloadedFile(sourceOf(audio), episode)
        || state.pendingSeek !== undefined) return false
    useAudioPlayer.setState({skipReplay: passage, skipNotice: null})
    audio.currentTime = passage.start
    state.setCurrentTimeUpdate(passage.start)
    return true
}

/** The manual button acts only on the passage still playing in the downloaded episode. */
export function skipSponsorPassage(episodeId: string, span: TimeRange, audio = getAudioPlayer()): boolean {
    const state = useAudioPlayer.getState(), episode = state.loadedPodcastEpisode?.podcastEpisode
    const candidates = state.sponsorSkip?.episodeId === episodeId ? state.sponsorSkip : null
    const available = candidates?.manualSpans?.some(([start, end]) => start === span[0] && end === span[1])
    if (!audio || episode?.episode_id !== episodeId || state.pendingSeek !== undefined
        || !available || (candidates?.duration && Number.isFinite(audio.duration) && Math.abs(candidates.duration - audio.duration) > 1)
        || !playsDownloadedFile(sourceOf(audio), episode) || audio.currentTime < span[0] - EARLY_S
        || audio.currentTime >= span[1] || !span.every(Number.isFinite) || span[0] < 0
        || span[1] <= span[0] || (Number.isFinite(audio.duration) && span[1] > audio.duration)) return false
    const passage = {episodeId, source: sourceOf(audio), start: audio.currentTime, end: span[1]}
    useAudioPlayer.setState({skipReplay: null})
    audio.currentTime = span[1]
    state.setCurrentTimeUpdate(span[1])
    state.notifySkip('sponsor', passage)
    return true
}

/**
 * Enforce the store's skip plan on the media element: check the position every SKIP_CHECK_MS while
 * it plays (and on timeupdate, seeks and the end), jump over skipped parts, and after the last kept
 * part of a Smart Play plan pause, or hand the next plan to `onNextPlan` (it plays that episode).
 * A seek into a skipped part moves on to the next kept start with a "Skipped" notice.
 * Returns a function that stops watching.
 */
export function watchSkips(audio: HTMLMediaElement, onNextPlan: (plan: SkipPlan) => void = () => {}, every = SKIP_CHECK_MS): () => void {
    let timer: ReturnType<typeof setInterval> | undefined, settle: ReturnType<typeof setTimeout> | undefined
    let ownTarget: number | undefined, userSeekAt = -Infinity, seekedByUser = false
    const check = () => {
        if (Date.now() - userSeekAt < SEEK_GRACE_MS) return
        const fromSeek = seekedByUser
        seekedByUser = false
        const state = useAudioPlayer.getState()
        if (state.skipReplay) {
            const replay = state.skipReplay
            if (replay.episodeId === state.loadedPodcastEpisode?.podcastEpisode.episode_id
                && replay.source === sourceOf(audio) && audio.currentTime >= replay.start - EARLY_S
                && audio.currentTime < replay.end) return
            useAudioPlayer.setState({skipReplay: null})
        }
        const skips = activeSkips(state, audio)
        if (!skips) return
        const position = audio.currentTime, decision = skipDecision(skips.keep, position)
        if (decision.kind === 'play') return
        if (decision.kind === 'jump') {
            const sponsor = skips.sponsors.some(([start, end]) => position >= start - EARLY_S && position < end)
            ownTarget = decision.to
            audio.currentTime = decision.to
            state.setCurrentTimeUpdate(decision.to)
            if (fromSeek || sponsor) state.notifySkip(sponsor ? 'sponsor' : 'skipped', {
                episodeId: state.loadedPodcastEpisode!.podcastEpisode.episode_id,
                source: sourceOf(audio), start: position, end: decision.to,
            })
            return
        }
        if (!skips.smart) return
        audio.pause()
        const [next, ...rest] = state.skipNext
        if (!next) return state.stopSmartPlay('finished')
        state.setSkipPlan(next, rest)
        state.notifySkip('next')
        onNextPlan(next)
    }
    const start = () => {timer ??= setInterval(check, every)}
    const stop = () => {clearInterval(timer); timer = undefined}
    const seeking = () => {
        const own = ownTarget !== undefined && Math.abs(audio.currentTime - ownTarget) < .5
        ownTarget = undefined
        if (own) return
        seekedByUser = true
        userSeekAt = Date.now()
        clearTimeout(settle)
        settle = setTimeout(check, SEEK_GRACE_MS)
    }
    // Which file plays, for the screens (the store's mediaSource): the stream or the downloaded file.
    const source = () => {if (useAudioPlayer.getState().mediaSource !== sourceOf(audio))
        useAudioPlayer.setState({mediaSource: sourceOf(audio), skipReplay: null, skipNotice: null})}
    const loaded = () => {source(); check()}
    const listeners: [string, () => void][] = [['play', start], ['playing', start], ['pause', stop], ['timeupdate', check],
        ['ended', check], ['seeking', seeking], ['loadstart', source], ['emptied', source], ['loadedmetadata', loaded]]
    listeners.forEach(([event, listener]) => audio.addEventListener(event, listener))
    source()
    if (!audio.paused) start()
    return () => {stop(); clearTimeout(settle); listeners.forEach(([event, listener]) => audio.removeEventListener(event, listener))}
}

/** The kept parts (Smart Play) and sponsor parts the player skips in the loaded episode right now. */
export function useSkipRanges(): {keep: TimeRange[] | null, sponsors: TimeRange[], duration: number} {
    const episode = useAudioPlayer(state => state.loadedPodcastEpisode?.podcastEpisode)
    const plan = useAudioPlayer(state => state.skipPlan)
    const sponsorSkip = useAudioPlayer(state => state.sponsorSkip)
    const source = useAudioPlayer(state => state.mediaSource)
    const duration = useAudioPlayer(state => state.metadata?.duration) || episode?.total_time || 0
    if (!episode || !duration || !playsDownloadedFile(source, episode)) return {keep: null, sponsors: [], duration}
    return {
        keep: plan?.episodeId === episode.episode_id ? normalizeRanges(plan.keep, duration) : null,
        sponsors: sponsorSkip?.episodeId === episode.episode_id
            ? normalizeRanges([...sponsorSkip.spans, ...(sponsorSkip.manualSpans ?? [])], duration) : [],
        duration,
    }
}

const RANGE_COLORS = {keep: 'color-mix(in srgb, var(--primary) 45%, transparent)', sponsor: 'var(--skip-sponsor, #d97706)'}

/**
 * The skip plan drawn on a progress bar: kept parts in a light accent, sponsor parts in amber. Place
 * it inside the bar's track (a positioned box). Decorative: the player's controls say it in words.
 * (Plain createElement, so this file stays free of JSX and of the PodFetch client.)
 */
export function SkipRanges(): ReactElement | null {
    const {keep, sponsors, duration} = useSkipRanges()
    if (!keep && !sponsors.length) return null
    const bar = (kind: keyof typeof RANGE_COLORS) => ([start, end]: TimeRange) => createElement('span', {
        key: `${kind}-${start}`, 'data-range': kind, style: {position: 'absolute', top: 0, bottom: 0, background: RANGE_COLORS[kind],
            left: `${start / duration * 100}%`, width: `${(end - start) / duration * 100}%`}})
    return createElement('span', {className: 'skip-ranges', 'aria-hidden': true, style: {position: 'absolute', inset: 0, pointerEvents: 'none'}},
        ...(keep ?? []).map(bar('keep')), ...sponsors.map(bar('sponsor')))
}
