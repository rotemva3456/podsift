// Smart Play and sponsor skipping in the player: the range math and the watcher.
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest'
import useAudioPlayer, {type SkipPlan} from '../../src/store/AudioPlayerSlice'
import {EARLY_S, normalizeRanges, SEEK_GRACE_MS, SKIP_CHECK_MS, skipDecision, skipSponsorPassage,
    subtractRanges, undoLastSkip, watchSkips} from '../../src/utils/audioPlayer'
import {episode} from './fixtures'

const FILE = 'http://localhost:3000/podcasts/show/bgp.mp3'
const STREAM = 'http://localhost:3000/proxy/podcast?episodeId=public-1'

/** A media element that plays in fake time: 1 s of audio per 1000 ms of timers, times playbackRate. */
class FakeAudio extends EventTarget {
    private time = 0
    paused = true
    src = FILE
    currentSrc = FILE
    playbackRate = 1
    duration = 3480
    seeks: number[] = []
    get currentTime() {return this.time}
    set currentTime(value: number) {
        this.time = Math.min(value, this.duration)
        this.seeks.push(this.time)
        setTimeout(() => this.dispatchEvent(new Event('seeking')), 0)
    }
    /** The listener drags the bar or clicks a timestamp. */
    userSeek(value: number) {this.currentTime = value; this.seeks.pop()}
    /** Playback has reached `value` (no seek). */
    arrive(value: number) {this.time = value}
    play() {this.paused = false; this.dispatchEvent(new Event('play'))}
    pause() {if (!this.paused) {this.paused = true; this.dispatchEvent(new Event('pause'))}}
    /** Play for `ms` of fake time in 10 ms steps, and return every position the listener heard. */
    advance(ms: number): number[] {
        const heard: number[] = []
        for (let step = 0; step < ms; step += 10) {
            if (!this.paused) {
                this.time = Math.min(this.time + .01 * this.playbackRate, this.duration)
                heard.push(this.time)
                if (this.time >= this.duration) {this.pause(); this.dispatchEvent(new Event('ended'))}
            }
            vi.advanceTimersByTime(10)
        }
        return heard
    }
}

const plan = (keep: [number, number][], episodeId = 'public-1'): SkipPlan => ({episodeId, keep, label: 'BGP'})
let audio: FakeAudio, stop: () => void, onNextPlan: ReturnType<typeof vi.fn>
const insideSkipped = (heard: number[], keep: [number, number][]) =>
    heard.filter(t => !keep.some(([start, end]) => t >= start - EARLY_S && t <= end + .2))

beforeEach(() => {
    vi.useFakeTimers()
    audio = new FakeAudio()
    onNextPlan = vi.fn()
    useAudioPlayer.setState({loadedPodcastEpisode: {podcastEpisode: episode(), chapters: []}, pendingSeek: undefined,
        skipPlan: null, skipNext: [], sponsorSkip: null, skipNotice: null, skipReplay: null,
        metadata: {currentTime: 0, duration: 3480, percentage: 0}})
    stop = watchSkips(audio as unknown as HTMLMediaElement, onNextPlan)
})
afterEach(() => {stop(); vi.useRealTimers()})

describe('range math', () => {
    it('sorts, merges and cleans ranges, and clamps them to the episode', () => {
        expect(normalizeRanges([[50, 60], [0, 10], [5, 20], [NaN, 3], [30, 25], [-5, 2], [590, 700]], 600))
            .toEqual([[0, 20], [50, 60], [590, 600]])
        expect(normalizeRanges([[10, 20], [20, 30]])).toEqual([[10, 30]])
    })
    it('subtracts sponsor parts from what is kept', () => {
        expect(subtractRanges([[0, Infinity]], [[0, 30], [100, 160]])).toEqual([[30, 100], [160, Infinity]])
        expect(subtractRanges([[20, 60], [90, 120]], [[50, 95]])).toEqual([[20, 50], [95, 120]])
        expect(subtractRanges([[20, 60]], [[0, 100]])).toEqual([])
    })
    it('decides: play inside a kept range, jump from a gap, stop after the last one', () => {
        const keep: [number, number][] = [[10, 20], [40, 50]]
        expect(skipDecision(keep, 0)).toEqual({kind: 'jump', to: 10})
        expect(skipDecision(keep, 10 - EARLY_S / 2)).toEqual({kind: 'play'})
        expect(skipDecision(keep, 15)).toEqual({kind: 'play'})
        expect(skipDecision(keep, 20)).toEqual({kind: 'jump', to: 40})
        expect(skipDecision(keep, 50)).toEqual({kind: 'end'})
    })
})

describe('Smart Play in the player', () => {
    it('range jumping: playing through a kept range jumps to the next kept start, and never plays a skipped part', () => {
        const keep: [number, number][] = [[100, 103], [200, 202], [300, 301.5]]
        useAudioPlayer.getState().setSkipPlan(plan(keep))
        audio.arrive(100)
        audio.play()
        const heard = audio.advance(8000)
        expect(audio.seeks).toEqual([200, 300])
        expect(insideSkipped(heard, keep)).toEqual([])
        expect(useAudioPlayer.getState().skipNotice?.kind).toBe('finished')  // a plain jump says nothing
    })

    it('checks at least every 100 ms, not only on timeupdate: an end is caught within one check', () => {
        useAudioPlayer.getState().setSkipPlan(plan([[0, 5], [60, 70]]))
        audio.play()
        const heard = audio.advance(5000 + SKIP_CHECK_MS + 20)
        expect(Math.max(...heard.filter(t => t < 60))).toBeLessThan(5 + (SKIP_CHECK_MS + 20) / 1000)
        expect(audio.currentTime).toBeGreaterThanOrEqual(60)
    })

    it('seeking into a skipped part moves on to the next kept start and says "Skipped"', () => {
        useAudioPlayer.getState().setSkipPlan(plan([[100, 200], [400, 500]]))
        audio.arrive(150)
        audio.play()
        audio.advance(100)
        audio.userSeek(300)
        audio.advance(SEEK_GRACE_MS - 50)
        expect(audio.currentTime).toBeLessThan(301)  // the grace lets a drag settle
        audio.advance(100)
        expect(audio.currentTime).toBeGreaterThanOrEqual(400)
        expect(audio.currentTime).toBeLessThan(400.2)
        expect(useAudioPlayer.getState().skipNotice?.kind).toBe('skipped')
    })

    it('seeking while paused also lands on the next kept start', () => {
        useAudioPlayer.getState().setSkipPlan(plan([[100, 200], [400, 500]]))
        audio.userSeek(20)
        audio.advance(SEEK_GRACE_MS + 20)
        expect(audio.currentTime).toBe(100)
        expect(audio.paused).toBe(true)
    })

    it('the last range: pauses there, ends Smart Play and says it finished', () => {
        useAudioPlayer.getState().setSkipPlan(plan([[100, 102]]))
        audio.arrive(100)
        audio.play()
        audio.advance(2500)
        expect(audio.paused).toBe(true)
        expect(audio.currentTime).toBeLessThan(102.2)
        expect(useAudioPlayer.getState().skipPlan).toBeNull()
        expect(useAudioPlayer.getState().skipNotice?.kind).toBe('finished')
        audio.play()  // playing on is normal playback now
        audio.advance(1000)
        expect(audio.paused).toBe(false)
    })

    it('the last range with more plans queued: hands the next episode plan over', () => {
        const next = plan([[30, 40]], 'public-2')
        useAudioPlayer.getState().setSkipPlan(plan([[100, 101]]), [next])
        audio.arrive(100)
        audio.play()
        audio.advance(1500)
        expect(audio.paused).toBe(true)
        expect(onNextPlan).toHaveBeenCalledWith(next)
        expect(useAudioPlayer.getState().skipPlan).toBe(next)
        expect(useAudioPlayer.getState().skipNext).toEqual([])
    })

    it('never skips against a stream: a plan waits for the downloaded file', () => {
        audio.src = audio.currentSrc = STREAM
        useAudioPlayer.getState().setSkipPlan(plan([[100, 200]]))
        audio.play()
        audio.advance(500)
        expect(audio.seeks).toEqual([])
    })

    it('a downloaded flag cannot make a different media URL safe for skipping', () => {
        audio.src = audio.currentSrc = 'http://localhost:3000/podcasts/another/episode.mp3'
        useAudioPlayer.getState().setSkipPlan(plan([[100, 200]]))
        useAudioPlayer.getState().setSponsorSkip({episodeId: 'public-1', spans: [[10, 20]]})
        audio.arrive(15)
        audio.play()
        audio.advance(500)
        expect(audio.seeks).toEqual([])
    })

    it('a matching filename on an unrelated host is still not the downloaded audio', () => {
        audio.src = audio.currentSrc = 'https://publisher.example/podcasts/show/bgp.mp3'
        useAudioPlayer.getState().setSponsorSkip({episodeId: 'public-1', spans: [[10, 20]]})
        audio.arrive(15)
        audio.play()
        audio.advance(500)
        expect(audio.seeks).toEqual([])
    })

    it('waits while a requested position is still being applied', () => {
        useAudioPlayer.setState({pendingSeek: 150})
        useAudioPlayer.getState().setSkipPlan(plan([[100, 200]]))
        audio.play()
        audio.advance(500)
        expect(audio.seeks).toEqual([])
    })

    it('ignores a plan made for another episode', () => {
        useAudioPlayer.getState().setSkipPlan(plan([[100, 200]], 'public-9'))
        audio.play()
        audio.advance(500)
        expect(audio.seeks).toEqual([])
    })

    it('"Stop Smart Play" returns to normal playback', () => {
        useAudioPlayer.getState().setSkipPlan(plan([[100, 200]]))
        useAudioPlayer.getState().stopSmartPlay()
        audio.play()
        audio.advance(500)
        expect(audio.seeks).toEqual([])
    })
})

describe('sponsor skipping', () => {
    it('skips each sponsor part with a notice, and a post-roll ends the episode', () => {
        audio.duration = 400
        useAudioPlayer.getState().setSponsorSkip({episodeId: 'public-1', spans: [[0, 30], [100, 130], [380, 400]]})
        audio.play()
        audio.advance(200)
        expect(audio.currentTime).toBeGreaterThanOrEqual(30)
        expect(useAudioPlayer.getState().skipNotice?.kind).toBe('sponsor')
        audio.arrive(99.95)
        const heard = audio.advance(300)
        expect(heard.some(t => t > 100.1 && t < 130)).toBe(false)
        expect(audio.currentTime).toBeGreaterThanOrEqual(130)
        audio.arrive(379.95)
        audio.advance(200)
        expect(audio.currentTime).toBe(400)
        expect(audio.paused).toBe(true)
    })

    it('combines with Smart Play: a sponsor inside a kept part is skipped too', () => {
        useAudioPlayer.getState().setSkipPlan(plan([[100, 200]]))
        useAudioPlayer.getState().setSponsorSkip({episodeId: 'public-1', spans: [[120, 150]]})
        audio.arrive(119.9)
        audio.play()
        const heard = audio.advance(400)
        expect(heard.some(t => t > 120.2 && t < 150)).toBe(false)
        expect(audio.currentTime).toBeGreaterThanOrEqual(150)
    })

    it('Undo replays the skipped passage once, then skips later ads as usual', () => {
        const settings = {episodeId: 'public-1', spans: [[10, 20], [30, 40]] as [number, number][]}
        useAudioPlayer.getState().setSponsorSkip(settings)
        audio.arrive(15)
        audio.dispatchEvent(new Event('timeupdate'))
        expect(audio.currentTime).toBe(20)
        expect(undoLastSkip(audio as unknown as HTMLMediaElement)).toBe(true)
        expect(audio.currentTime).toBe(15)
        audio.play()
        audio.advance(3000)
        expect(audio.currentTime).toBeCloseTo(18)
        audio.advance(2200)
        expect(useAudioPlayer.getState().skipReplay).toBeNull()
        audio.arrive(30)
        audio.advance(100)
        expect(audio.currentTime).toBeGreaterThanOrEqual(40)
        expect(useAudioPlayer.getState().sponsorSkip).toBe(settings)
    })

    it('Undo also permits a sponsor passage excluded by Smart Play', () => {
        useAudioPlayer.getState().setSkipPlan(plan([[40, 60]]))
        useAudioPlayer.getState().setSponsorSkip({episodeId: 'public-1', spans: [[10, 20]]})
        audio.arrive(15)
        audio.dispatchEvent(new Event('timeupdate'))
        expect(audio.currentTime).toBe(40)
        undoLastSkip(audio as unknown as HTMLMediaElement)
        audio.play()
        audio.advance(3000)
        expect(audio.currentTime).toBeCloseTo(18)
        expect(useAudioPlayer.getState().skipPlan).not.toBeNull()
    })

    it('Undo cannot seek in a different episode or media source', () => {
        useAudioPlayer.getState().setSponsorSkip({episodeId: 'public-1', spans: [[10, 20]]})
        audio.arrive(15)
        audio.dispatchEvent(new Event('timeupdate'))
        audio.src = audio.currentSrc = STREAM
        expect(undoLastSkip(audio as unknown as HTMLMediaElement)).toBe(false)
        audio.src = audio.currentSrc = FILE
        useAudioPlayer.setState({loadedPodcastEpisode: {podcastEpisode: episode({episode_id: 'public-2'}), chapters: []}})
        expect(undoLastSkip(audio as unknown as HTMLMediaElement)).toBe(false)
        expect(audio.currentTime).toBe(20)
    })

    it('leaving the replay cancels it, so a later seek into that ad is skipped', () => {
        useAudioPlayer.getState().setSponsorSkip({episodeId: 'public-1', spans: [[10, 20]]})
        audio.arrive(15)
        audio.dispatchEvent(new Event('timeupdate'))
        undoLastSkip(audio as unknown as HTMLMediaElement)
        audio.userSeek(5)
        audio.advance(SEEK_GRACE_MS + 30)
        audio.userSeek(15)
        audio.advance(SEEK_GRACE_MS + 30)
        expect(audio.currentTime).toBe(20)
        expect(useAudioPlayer.getState().skipReplay).toBeNull()
    })

    it('a duration mismatch prevents an automatic jump before UI metadata refreshes', () => {
        useAudioPlayer.getState().setSponsorSkip({episodeId: 'public-1', duration: 3480, spans: [[10, 20]]})
        audio.duration = 3450
        audio.arrive(15)
        audio.dispatchEvent(new Event('timeupdate'))
        expect(audio.currentTime).toBe(15)
        expect(audio.seeks).toEqual([])
    })

    it('a manual skip leaves playback alone until clicked and can also be undone', () => {
        useAudioPlayer.getState().setSponsorSkip({episodeId: 'public-1', spans: [], manualSpans: [[10, 20]]})
        audio.arrive(15)
        audio.dispatchEvent(new Event('timeupdate'))
        expect(audio.currentTime).toBe(15)
        expect(skipSponsorPassage('public-1', [10, 20], audio as unknown as HTMLMediaElement)).toBe(true)
        expect(audio.currentTime).toBe(20)
        expect(undoLastSkip(audio as unknown as HTMLMediaElement)).toBe(true)
        expect(audio.currentTime).toBe(15)
    })

    it('a stale manual button cannot seek after the listener leaves its passage', () => {
        audio.arrive(25)
        expect(skipSponsorPassage('public-1', [10, 20], audio as unknown as HTMLMediaElement)).toBe(false)
        expect(audio.seeks).toEqual([])
    })
})
