// The player bar: Smart Play status, the "Skip sponsors" switch, notices, the
// ranges on the progress bar, and Smart Play across episodes in HiddenAudioPlayer.
import {afterEach, beforeAll, beforeEach, describe, expect, it, vi} from 'vitest'
import {act, type ReactNode} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {MemoryRouter} from 'react-router-dom'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'

vi.mock('../../src/utils/http', () => ({$api: {useMutation: () => ({mutate() {}, mutateAsync: async () => ({})})}, client: {}}))

await import('../../src/ext/registry')
const {PlayerControls} = await import('../../src/ext/features/cuts/PlayerControls')
const {HiddenAudioPlayer} = await import('../../src/components/HiddenAudioPlayer')
const {Slider} = await import('../../src/components/ui/slider')
const {MakeTranscript} = await import('../../src/ext/shared/MakeTranscript')
const useAudioPlayer = (await import('../../src/store/AudioPlayerSlice')).default
const {episode, serve, sponsorSettings} = await import('./fixtures')

const FILE = 'http://localhost:3000/podcasts/show/bgp.mp3'
const SETTINGS = 'GET /api/v1/settings/sponsorblock'
const BLOCK = 'GET /api/v1/podcasts/episodes/internal-1/sponsorblock'
const ADS = 'GET /companion/episodes/public-1/skip-segments'
const PREFERENCES = 'GET /companion/settings/skipping'
const servePlayer = (routes: Parameters<typeof serve>[1]) => serve(vi.stubGlobal, {
    [PREFERENCES]: {body: {manual_categories: [], minimum_seconds: 0}}, ...routes,
})
const nativeSkips = (spans: {start: number; end: number; label: string}[], timed = true) => ({
    episode_id: 'public-1', source: 'podsift-transcript', origin: 'generated', timed,
    transcript_digest: 'current-audio', duration: 3480,
    timing_status: timed ? 'matched' : 'missing', auto_skip_safe: timed,
    spans: spans.map(span => ({...span, category: 'sponsor', reason: 'Explicit sponsor read.', segment_ids: ['ad']})),
})
let root: Root | undefined
vi.setConfig({testTimeout: 30_000})  // a busy machine makes these slow; each wait below still has its own limit

beforeAll(() => {(globalThis as {IS_REACT_ACT_ENVIRONMENT?: boolean}).IS_REACT_ACT_ENVIRONMENT = true})
beforeEach(() => {
    document.body.innerHTML = '<audio id="audio-player"></audio>'
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
    vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
    useAudioPlayer.setState({loadedPodcastEpisode: {podcastEpisode: episode(), chapters: []}, mediaSource: FILE, pendingSeek: undefined,
        skipPlan: null, skipNext: [], sponsorSkip: null, skipNotice: null, skipReplay: null, metadata: {currentTime: 250, duration: 3480, percentage: 7}})
})
afterEach(() => {act(() => root?.unmount()); root = undefined; vi.restoreAllMocks(); vi.unstubAllGlobals()})

/** PlayerControls inside the drawer's markup (this copies DrawerAudioPlayer's shape). */
async function showBar(client = new QueryClient({defaultOptions: {queries: {retry: false}}}), extra?: ReactNode) {
    const container = document.body.appendChild(document.createElement('div'))
    root = createRoot(container)
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter>
        <div className="listen-player"><div className="player-center"><div className="player-controls"><div className="player-actions">
            <PlayerControls episodeId="public-1" position={250}/></div></div>
            <div className="player-progress"><Slider aria-label="Playback position" min={0} max={3480} value={[250]}/></div></div></div>
        {extra}
    </MemoryRouter></QueryClientProvider>))
    return container
}
async function until(check: () => boolean, tries = 500) {
    for (let i = 0; i < tries && !check(); i++) await act(() => new Promise(resolve => setTimeout(resolve, 10)))
    expect(check(), document.body.innerHTML).toBe(true)
}
const sponsorSwitch = (view: HTMLElement) => view.querySelector<HTMLButtonElement>('button[aria-label="Skip sponsors"]')!
const track = (view: HTMLElement) => view.querySelector('.player-progress [data-slot="slider-track"]')!

describe('player bar', () => {
    it('Skip sponsors on: uses our transcript evidence without requesting community data', async () => {
        const calls = servePlayer({[SETTINGS]: {body: sponsorSettings}, [BLOCK]: {body: {segments: [], preferences: sponsorSettings}},
            [ADS]: {body: nativeSkips([{start: 0, end: 30, label: 'sponsor'}, {start: 1740, end: 1827, label: 'sponsor'}])}})
        const view = await showBar()
        await until(() => !!useAudioPlayer.getState().sponsorSkip)
        expect(useAudioPlayer.getState().sponsorSkip).toEqual({episodeId: 'public-1', duration: 3480, spans: [[0, 30], [1740, 1827]]})
        expect(calls.map(c => c.key)).toEqual(expect.arrayContaining([SETTINGS, ADS]))
        expect(calls.some(c => c.key === BLOCK)).toBe(false)
        expect(sponsorSwitch(view).getAttribute('aria-pressed')).toBe('true')
        expect(sponsorSwitch(view).title).toBe('Skip sponsors: on. 2 sponsor parts, 1:57.')
        const sponsors = [...track(view).querySelectorAll<HTMLElement>('[data-range="sponsor"]')].map(bar => [parseFloat(bar.style.left), parseFloat(bar.style.width)])
        expect(sponsors).toHaveLength(2)
        expect(sponsors[0]![0]).toBe(0)
        expect(sponsors[0]![1]).toBeCloseTo(30 / 3480 * 100, 3)
        expect(sponsors[1]![0]).toBeCloseTo(50, 3)
        expect(sponsors[1]![1]).toBeCloseTo(87 / 3480 * 100, 3)
    })

    it('the switch turns sponsor skipping off in PodFetch\'s settings and says so', async () => {
        const calls = servePlayer({[SETTINGS]: {body: sponsorSettings}, [BLOCK]: {body: {segments: [], preferences: sponsorSettings}},
            [ADS]: {body: nativeSkips([{start: 0, end: 30, label: 'sponsor'}])},
            'PUT /api/v1/settings/sponsorblock': {body: {...sponsorSettings, enabled: false}}})
        const view = await showBar()
        await until(() => !!useAudioPlayer.getState().sponsorSkip)
        await act(async () => sponsorSwitch(view).click())
        await until(() => sponsorSwitch(view).getAttribute('aria-pressed') === 'false')
        expect(calls.find(c => c.key === 'PUT /api/v1/settings/sponsorblock')?.body).toEqual({...sponsorSettings, enabled: false})
        expect(useAudioPlayer.getState().sponsorSkip).toBeNull()
        expect(view.querySelector('.cut-notice')?.textContent).toContain('Skip sponsors: off.')
        expect(track(view).querySelector('[data-range]')).toBeNull()
    })

    it('missing timed words asks for a transcript and leaves playback untouched', async () => {
        const calls = servePlayer({[SETTINGS]: {body: sponsorSettings}, [ADS]: {body: nativeSkips([], false)}})
        const view = await showBar()
        await until(() => sponsorSwitch(view).title.includes('add timed words first'))
        expect(useAudioPlayer.getState().sponsorSkip).toBeNull()
        expect(calls.some(c => c.key === BLOCK)).toBe(false)
    })

    it('a failed scan reports an error instead of claiming that no sponsors were found', async () => {
        servePlayer({[SETTINGS]: {body: sponsorSettings}, [ADS]: {status: 503, body: {detail: 'Try again'}}})
        const view = await showBar()
        await until(() => sponsorSwitch(view).title.includes("couldn't load"))
        expect(useAudioPlayer.getState().sponsorSkip).toBeNull()
    })

    it('a failed preference lookup cannot silently fall back to automatic skipping', async () => {
        servePlayer({[SETTINGS]: {body: sponsorSettings}, [ADS]: {body: nativeSkips([{start: 0, end: 30, label: 'Sponsor'}])},
            [PREFERENCES]: {status: 503, body: {detail: 'Try again'}}})
        const view = await showBar()
        await until(() => sponsorSwitch(view).title.includes("couldn't load"))
        expect(useAudioPlayer.getState().sponsorSkip).toBeNull()
    })

    it('publisher timing offers a manual button without installing automatic ranges', async () => {
        const found = {...nativeSkips([{start: 240, end: 270, label: 'Sponsor'}]),
            origin: 'feed', auto_skip_safe: false, timing_status: 'unverified'}
        servePlayer({[SETTINGS]: {body: sponsorSettings}, [ADS]: {body: found}})
        const view = await showBar()
        await until(() => !!useAudioPlayer.getState().sponsorSkip?.manualSpans?.length)
        expect(useAudioPlayer.getState().sponsorSkip?.spans).toEqual([])
        expect(sponsorSwitch(view).title).toContain('Choose skips manually')
        expect(view.querySelector('.cut-notice')?.textContent).toContain('Skip this part')
        expect(track(view).querySelectorAll('[data-range="sponsor"]')).toHaveLength(1)
    })

    it('a changed duration gives a timing warning and removes all ranges', async () => {
        servePlayer({[SETTINGS]: {body: sponsorSettings}, [ADS]: {body: nativeSkips([{start: 240, end: 270, label: 'Sponsor'}])}})
        useAudioPlayer.setState({metadata: {currentTime: 250, duration: 3300, percentage: 7}})
        const view = await showBar()
        await until(() => sponsorSwitch(view).title.includes('do not match'))
        expect(useAudioPlayer.getState().sponsorSkip).toBeNull()
        expect(track(view).querySelector('[data-range="sponsor"]')).toBeNull()
    })

    it('Undo is offered for a recorded jump and returns to that passage', async () => {
        servePlayer({[SETTINGS]: {body: sponsorSettings}, [ADS]: {body: nativeSkips([{start: 240, end: 270, label: 'Sponsor'}])}})
        const view = await showBar()
        await until(() => !!useAudioPlayer.getState().sponsorSkip)
        const audio = document.querySelector('audio')!
        audio.src = FILE
        audio.currentTime = 270
        await act(async () => useAudioPlayer.getState().notifySkip('sponsor', {episodeId: 'public-1', source: FILE, start: 250, end: 270}))
        await act(async () => [...view.querySelectorAll<HTMLButtonElement>('.cut-notice button')].find(b => b.textContent === 'Undo skip')!.click())
        expect(audio.currentTime).toBe(250)
        expect(useAudioPlayer.getState().skipReplay?.end).toBe(270)
        expect(useAudioPlayer.getState().skipNotice).toBeNull()
    })

    it('a newly ready transcript refreshes a cached no-transcript scan for the playing episode', async () => {
        const client = new QueryClient({defaultOptions: {queries: {retry: false}}})
        client.setQueryData(['cuts', 'skip-segments', 'public-1'], nativeSkips([], false))
        const calls = servePlayer({[SETTINGS]: {body: sponsorSettings},
            [ADS]: {body: nativeSkips([{start: 0, end: 30, label: 'Sponsor'}])},
            'GET /api/v1/podcasts/episodes/internal-1/transcripts': {body: [{source: 'generated', status: 'parsed'}]}})
        await showBar(client, <MakeTranscript episode={episode()} onReady={() => {}}/>)
        await until(() => !!useAudioPlayer.getState().sponsorSkip)
        expect(useAudioPlayer.getState().sponsorSkip).toEqual({episodeId: 'public-1', duration: 3480, spans: [[0, 30]]})
        expect(calls.some(c => c.key === ADS)).toBe(true)
        expect(calls.some(c => c.key === BLOCK || c.key.startsWith('POST'))).toBe(false)
    })

    it('a streamed episode: nothing is skipped, and turning the switch on offers the download', async () => {
        useAudioPlayer.setState({mediaSource: 'http://localhost:3000/proxy/podcast?episodeId=public-1',
            loadedPodcastEpisode: {podcastEpisode: episode({status: false}), chapters: []}})
        const calls = servePlayer({[SETTINGS]: {body: {...sponsorSettings, enabled: false}},
            'PUT /api/v1/settings/sponsorblock': {body: sponsorSettings}, 'GET /api/v1/users/me': {body: {role: 'admin'}}})
        const view = await showBar()
        await until(() => !sponsorSwitch(view).disabled)
        await act(async () => sponsorSwitch(view).click())
        await until(() => view.querySelector('.cut-notice')!.textContent!.includes('Download this episode first'))
        expect([...view.querySelectorAll('.cut-notice button')].map(b => b.textContent)).toContain('Download')
        expect(calls.some(c => c.key === BLOCK || c.key === ADS)).toBe(false)
        expect(useAudioPlayer.getState().sponsorSkip).toBeNull()
    })

    it('Smart Play: the bar shows it, draws the kept parts, and stops it', async () => {
        servePlayer({[SETTINGS]: {body: {...sponsorSettings, enabled: false}}})
        useAudioPlayer.getState().setSkipPlan({episodeId: 'public-1', keep: [[200, 440], [900, 1380]], label: 'BGP'})
        const view = await showBar()
        const stop = view.querySelector<HTMLButtonElement>('button[aria-label="Stop Smart Play (12 of 58 min)"]')
        expect(stop?.textContent).toBe('Smart Play')
        expect(track(view).querySelectorAll('[data-range="keep"]')).toHaveLength(2)
        await act(async () => stop!.click())
        expect(useAudioPlayer.getState().skipPlan).toBeNull()
        expect(track(view).querySelector('[data-range]')).toBeNull()
    })

    it('a skip notice: "Skipped" with a way to stop Smart Play', async () => {
        servePlayer({[SETTINGS]: {body: {...sponsorSettings, enabled: false}}})
        useAudioPlayer.getState().setSkipPlan({episodeId: 'public-1', keep: [[200, 440]], label: 'BGP'})
        const view = await showBar()
        await act(async () => useAudioPlayer.getState().notifySkip('skipped'))
        const notice = view.querySelector('.cut-notice')!
        expect(notice.getAttribute('role')).toBe('status')
        expect(notice.textContent).toContain('Skipped. Smart Play plays only your parts.')
        await act(async () => [...notice.querySelectorAll('button')].find(b => b.textContent === 'Stop Smart Play')!.click())
        expect(useAudioPlayer.getState().skipPlan).toBeNull()
    })
})

describe('HiddenAudioPlayer', () => {
    async function showPlayer() {
        const container = document.body.appendChild(document.createElement('div'))
        root = createRoot(container)
        await act(async () => root!.render(<QueryClientProvider client={new QueryClient()}><HiddenAudioPlayer setAudioAmplifier={() => {}}/></QueryClientProvider>))
    }

    it('playing another episode ends Smart Play', async () => {
        await showPlayer()
        useAudioPlayer.getState().setSkipPlan({episodeId: 'public-1', keep: [[200, 440]], label: 'BGP'})
        act(() => useAudioPlayer.setState({loadedPodcastEpisode: {podcastEpisode: episode({episode_id: 'public-2'}), chapters: []}}))
        expect(useAudioPlayer.getState().skipPlan).toBeNull()
    })

    it('changing the episode clears old replay and Undo state even without Smart Play', async () => {
        await showPlayer()
        const passage = {episodeId: 'public-1', source: FILE, start: 10, end: 20}
        act(() => useAudioPlayer.setState({skipReplay: passage, skipNotice: {kind: 'sponsor', at: Date.now(), passage}}))
        act(() => useAudioPlayer.setState({loadedPodcastEpisode: {podcastEpisode: episode({episode_id: 'public-2'}), chapters: []}}))
        expect(useAudioPlayer.getState().skipReplay).toBeNull()
        expect(useAudioPlayer.getState().skipNotice).toBeNull()
    })

    it('Smart Play across episodes: after the last kept part, the next plan\'s episode plays from its first kept start', async () => {
        const next = {episodeId: 'public-2', keep: [[30, 60]] as [number, number][], label: 'BGP'}
        servePlayer({'GET /api/v1/episodes/public-2': {body: {podcastEpisode: episode({id: 'internal-2', episode_id: 'public-2',
            local_url: '/podcasts/show/two.mp3'}), podcastHistoryItem: null}}})
        await showPlayer()
        const audio = document.querySelector('audio')!
        audio.src = FILE
        audio.currentTime = 441
        useAudioPlayer.getState().setSkipPlan({episodeId: 'public-1', keep: [[200, 440]], label: 'BGP'}, [next])
        await act(async () => {audio.dispatchEvent(new Event('timeupdate'))})
        await until(() => useAudioPlayer.getState().loadedPodcastEpisode?.podcastEpisode.episode_id === 'public-2')
        expect(useAudioPlayer.getState().skipPlan).toEqual(next)
        expect(useAudioPlayer.getState().pendingSeek).toBe(30)
        expect(audio.src).toBe('http://localhost:3000/podcasts/show/two.mp3')
    })
})
