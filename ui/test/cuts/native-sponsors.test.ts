import {describe, expect, it} from 'vitest'
import {nativeSkipChoices, nativeSponsorParts} from '../../src/ext/features/cuts/sponsors'
import type {NativeSkips} from '../../src/ext/features/cuts/api'
import {sponsorSettings} from './fixtures'

const found: NativeSkips = {
    episode_id: 'public-1', source: 'podsift-transcript', origin: 'generated', timed: true,
    transcript_digest: 'current-audio', duration: 400,
    timing_status: 'matched', auto_skip_safe: true,
    spans: [
        {start: 0, end: 8, category: 'intro', label: 'Intro', reason: 'Opening greeting.', segment_ids: ['hello']},
        {start: 10, end: 30, category: 'sponsor', label: 'Sponsor', reason: 'Explicit sponsor announcement.', segment_ids: ['ad']},
        {start: 30, end: 40, category: 'selfpromo', label: 'Self-promotion', reason: 'Support request.', segment_ids: ['support']},
        {start: 200, end: 210, category: 'interaction', label: 'Interaction reminder', reason: 'Please subscribe.', segment_ids: ['subscribe']},
    ],
}

describe('native sponsor skipping', () => {
    it('applies the existing independent category preferences to our own evidence', () => {
        expect(nativeSponsorParts(found, sponsorSettings)).toEqual({spans: [[10, 40]], source: 'transcript', unsure: 0})
        expect(nativeSponsorParts(found, {...sponsorSettings, skipIntro: true, skipInteraction: true}).spans)
            .toEqual([[0, 8], [10, 40], [200, 210]])
        expect(nativeSponsorParts(found, {...sponsorSettings, skipSponsor: false}).spans).toEqual([[30, 40]])
    })

    it('never invents timing or skips when the user switches it off', () => {
        expect(nativeSponsorParts({...found, timed: false}, sponsorSettings).spans).toEqual([])
        expect(nativeSponsorParts(found, {...sponsorSettings, enabled: false}).spans).toEqual([])
        expect(nativeSponsorParts(undefined, sponsorSettings).spans).toEqual([])
    })

    it('rejects nonfinite, reversed, negative and out-of-file spans from a bad response', () => {
        const ad = found.spans[1]!
        const spans = [ad, {...ad, start: -1}, {...ad, end: NaN}, {...ad, end: 5}, {...ad, end: 401}]
        expect(nativeSponsorParts({...found, spans}, sponsorSettings)).toEqual({spans: [[10, 30]], source: 'transcript', unsure: 4})
    })

    it('manual categories remain marked but are not automatic skips', () => {
        const choices = nativeSkipChoices(found, sponsorSettings, {manual_categories: ['sponsor'], minimum_seconds: 0})
        expect(choices.spans).toEqual([[30, 40]])
        expect(choices.manual.map(s => [s.start, s.end])).toEqual([[10, 30]])
    })

    it('a minimum length preserves short passages in both automatic and manual mode', () => {
        const choices = nativeSkipChoices(found, sponsorSettings, {manual_categories: ['sponsor'], minimum_seconds: 15})
        expect(choices.spans).toEqual([])
        expect(choices.manual.map(s => s.category)).toEqual(['sponsor'])
        expect(choices.unsure).toBe(0)
    })

    it('publisher and older unchecked responses offer only manual suggestions', () => {
        const unchecked = {...found, origin: 'feed', timing_status: 'unverified' as const, auto_skip_safe: false}
        expect(nativeSkipChoices(unchecked, sponsorSettings).spans).toEqual([])
        expect(nativeSkipChoices(unchecked, sponsorSettings).manual).toHaveLength(2)
        expect(nativeSponsorParts(unchecked, sponsorSettings).spans).toEqual([])
    })

    it('a changed file duration disables every skip, including manual suggestions', () => {
        const choices = nativeSkipChoices(found, sponsorSettings, {manual_categories: [], minimum_seconds: 0}, 390)
        expect(choices.spans).toEqual([])
        expect(choices.manual).toEqual([])
        expect(choices.unsure).toBe(2)
        expect(nativeSkipChoices({...found, timing_status: 'mismatch'}, sponsorSettings).spans).toEqual([])
    })
})
