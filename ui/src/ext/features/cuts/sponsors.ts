import type {TimeRange} from '../../../store/AudioPlayerSlice'
import {normalizeRanges} from '../../../utils/audioPlayer'
import type {Ads, NativeSkips, NativeSkipSpan, SkipPreferences, SponsorBlock, SponsorSettings} from './api'

/** Category preferences retained from PodFetch, now applied to our own transcript evidence. */
export const CATEGORIES = [
    ['sponsor', 'skipSponsor'], ['selfpromo', 'skipSelfpromo'], ['interaction', 'skipInteraction'], ['intro', 'skipIntro'],
    ['outro', 'skipOutro'], ['preview', 'skipPreview'], ['filler', 'skipFiller'], ['music_offtopic', 'skipMusicOfftopic'],
] as const satisfies readonly (readonly [string, keyof SponsorSettings])[]

export type SponsorParts = {
    spans: TimeRange[]
    /** Where the times came from: SponsorBlock (YouTube episodes), the transcript, or nowhere. */
    source: 'sponsorblock' | 'transcript' | 'none'
    /** SponsorBlock parts left alone because their times may not match this file (durationMismatch). */
    unsure: number
}

export const DEFAULT_SKIP_PREFERENCES: SkipPreferences = {manual_categories: [], minimum_seconds: 0}
export type NativeSkipChoices = SponsorParts & {manual: NativeSkipSpan[]}

/** Also check the loaded media: cached episode duration may describe an earlier download. */
export function skipTimingMismatch(found: NativeSkips | undefined, actualDuration?: number): boolean {
    return found?.timing_status === 'mismatch' || Boolean(found?.duration && actualDuration
        && Number.isFinite(actualDuration) && Math.abs(found.duration - actualDuration) > 1)
}

/** Only file-derived timing can be automatic. Unknown/older responses become manual suggestions. */
export function nativeSkipChoices(found: NativeSkips | undefined, settings: SponsorSettings,
    preferences: SkipPreferences = DEFAULT_SKIP_PREFERENCES, actualDuration?: number): NativeSkipChoices {
    if (!settings.enabled || !found?.timed) return {spans: [], manual: [], source: 'none', unsure: 0}
    const on = new Set<string>(CATEGORIES.filter(([, key]) => settings[key]).map(([category]) => category))
    const wanted = found.spans.filter(span => on.has(span.category))
    if (skipTimingMismatch(found, actualDuration)) return {spans: [], manual: [], source: 'none', unsure: wanted.length}
    const valid = wanted.filter(span => Number.isFinite(span.start) && Number.isFinite(span.end)
        && span.start >= 0 && span.end > span.start && (found.duration === null || span.end <= found.duration)
        && (!actualDuration || span.end <= actualDuration))
    const eligible = valid.filter(span => span.end - span.start >= preferences.minimum_seconds)
    const autoSafe = found.auto_skip_safe === true && found.timing_status === 'matched'
    const manual = eligible.filter(span => !autoSafe || preferences.manual_categories.includes(span.category))
    const automatic = eligible.filter(span => !manual.includes(span))
    return {spans: normalizeRanges(automatic.map(span => [span.start, span.end])), manual,
        source: 'transcript', unsure: wanted.length - valid.length}
}

/** Backward-compatible range adapter; the player also reads the manual suggestions. */
export function nativeSponsorParts(found: NativeSkips | undefined, settings: SponsorSettings): SponsorParts {
    const choices = nativeSkipChoices(found, settings)
    return {spans: choices.spans, source: choices.source, unsure: choices.unsure}
}

/**
 * Legacy adapter for existing clients. The Podsift player uses nativeSponsorParts instead.
 * The sponsor parts to skip in one episode. SponsorBlock segments come first (YouTube-origin
 * episodes): only the categories the user turned on, and never a segment marked durationMismatch.
 * Every other episode uses the companion's sponsor reads from the transcript, which count as the
 * "sponsor" category. Nothing is skipped when the switch (`enabled`) is off.
 */
export function sponsorParts(block: SponsorBlock | undefined, ads: Ads | undefined, settings: SponsorSettings): SponsorParts {
    if (!settings.enabled) return {spans: [], source: 'none', unsure: 0}
    const segments = block?.segments ?? []
    if (segments.length) {
        const on = new Set<string>(CATEGORIES.filter(([, key]) => settings[key]).map(([category]) => category))
        const wanted = segments.filter(segment => segment.actionType === 'skip' && on.has(segment.category))
        const sure = wanted.filter(segment => !segment.durationMismatch)
        return {spans: normalizeRanges(sure.map(s => [s.startMs / 1000, s.endMs / 1000])), source: 'sponsorblock', unsure: wanted.length - sure.length}
    }
    if (!ads) return {spans: [], source: 'none', unsure: 0}
    return {spans: settings.skipSponsor ? normalizeRanges(ads.spans.map(s => [s.start, s.end])) : [], source: 'transcript', unsure: 0}
}
