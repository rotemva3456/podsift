// "Skip sponsors" merges two sources into one skip plan.
import {describe, expect, it} from 'vitest'
import {sponsorParts} from '../../src/ext/features/cuts/sponsors'
import {segment, sponsorSettings} from './fixtures'

const ads = {episode_id: 'public-1', spans: [{start: 0, end: 22, label: 'sponsor'}, {start: 600, end: 700, label: 'sponsor'}]}

describe('sponsor parts', () => {
    it('YouTube episodes: SponsorBlock segments of the categories the user turned on, in seconds', () => {
        const block = {preferences: sponsorSettings, segments: [segment('sponsor', 10, 40), segment('intro', 0, 8), segment('selfpromo', 35, 50)]}
        expect(sponsorParts(block, ads, sponsorSettings)).toEqual({spans: [[10, 50]], source: 'sponsorblock', unsure: 0})
        expect(sponsorParts(block, ads, {...sponsorSettings, skipIntro: true}).spans).toEqual([[0, 8], [10, 50]])
    })

    it('never auto-skips a segment marked durationMismatch, and says how many were left', () => {
        const block = {preferences: sponsorSettings, segments: [segment('sponsor', 10, 40, true), segment('sponsor', 90, 120)]}
        expect(sponsorParts(block, ads, sponsorSettings)).toEqual({spans: [[90, 120]], source: 'sponsorblock', unsure: 1})
    })

    it('acts only on "skip" segments', () => {
        const block = {preferences: sponsorSettings, segments: [{...segment('sponsor', 10, 40), actionType: 'mute'}]}
        expect(sponsorParts(block, ads, sponsorSettings).spans).toEqual([])
    })

    it('every other episode: the sponsor reads found in its transcript, as the "sponsor" category', () => {
        const none = {preferences: sponsorSettings, segments: []}
        expect(sponsorParts(none, ads, sponsorSettings)).toEqual({spans: [[0, 22], [600, 700]], source: 'transcript', unsure: 0})
        expect(sponsorParts(undefined, ads, {...sponsorSettings, skipSponsor: false}).spans).toEqual([])
        expect(sponsorParts(undefined, undefined, sponsorSettings)).toEqual({spans: [], source: 'none', unsure: 0})
    })

    it('the switch off skips nothing', () => {
        const block = {preferences: sponsorSettings, segments: [segment('sponsor', 10, 40)]}
        expect(sponsorParts(block, ads, {...sponsorSettings, enabled: false}).spans).toEqual([])
    })
})
