// Each slot is wired once: a fixture feature shows up in every screen that owns a slot.
import {afterEach, beforeAll, beforeEach, describe, expect, it, vi} from 'vitest'
import {act, type ReactNode} from 'react'
import {createRoot, Root} from 'react-dom/client'
import {MemoryRouter, Route, Routes} from 'react-router-dom'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import type {PodcastEpisode} from '../../src/ext/types'

vi.mock('../../src/utils/http', () => ({client: {GET: async () => ({data: []})}, $api: {}, apiURL: '', uiURL: ''}))
vi.mock('../../src/ext/registry', () => import('./fixtures/demo-registry'))

const {Sidebar} = await import('../../src/components/Sidebar')
const {HomePageSelector} = await import('../../src/pages/HomePageSelector')
const {SettingsPage} = await import('../../src/pages/SettingsPage')
const {ListenEpisodeRow} = await import('../../src/components/ListenEpisodeRow')
const {DrawerAudioPlayer} = await import('../../src/components/DrawerAudioPlayer')
const {Learn} = await import('../../src/pages/Learn')
const useAudioPlayer = (await import('../../src/store/AudioPlayerSlice')).default

const episode = {id: 'internal-1', episode_id: 'public-1', name: 'An episode', description: '', total_time: 600,
    local_url: '/proxy/podcast?episodeId=public-1', local_image_url: '', status: true, url: 'https://example.test/a.mp3',
    date_of_recording: '2026-09-01'} as unknown as PodcastEpisode
let root: Root | undefined

beforeAll(() => {(globalThis as {IS_REACT_ACT_ENVIRONMENT?: boolean}).IS_REACT_ACT_ENVIRONMENT = true})
beforeEach(() => {
    document.body.innerHTML = '<audio id="audio-player"></audio>'
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
    vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
})
afterEach(() => {act(() => root?.unmount()); root = undefined; vi.restoreAllMocks(); vi.unstubAllGlobals()})

async function show(ui: ReactNode, at = '/') {
    const container = document.body.appendChild(document.createElement('div'))
    const client = new QueryClient({defaultOptions: {queries: {retry: false}}})
    root = createRoot(container)
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[at]}>{ui}</MemoryRouter></QueryClientProvider>))
    return container
}

async function until(check: () => boolean) {
    for (let i = 0; i < 200 && !check(); i++) await act(() => new Promise(resolve => setTimeout(resolve, 10)))
    expect(check()).toBe(true)
}

const buttons = (root: ParentNode) => [...root.querySelectorAll('button')]
const named = (root: ParentNode, text: string) => buttons(root).find(button => button.textContent === text)

describe('feature slots', () => {
    it('nav links: More tools exposes feature links and counts on desktop and phone', async () => {
        const view = await show(<Sidebar/>)
        const desktopToggle = view.querySelector<HTMLButtonElement>('nav[aria-label="Main navigation"] button[aria-expanded]')!
        expect(desktopToggle.getAttribute('aria-expanded')).toBe('false')
        expect(view.querySelector('nav[aria-label="Main navigation"] a[href="/demo"]')).toBeNull()
        await act(async () => desktopToggle.click())
        const desktop = view.querySelector('nav[aria-label="Main navigation"] a[href="/demo"]')
        expect(desktopToggle.getAttribute('aria-expanded')).toBe('true')
        expect(desktop?.textContent).toBe('Demo feature3')
        expect(desktop?.querySelector('.nav-count')?.textContent).toBe('3')
        expect(view.querySelector('nav[aria-label="Main navigation"] a[href="/demo/desk"]')?.textContent).toBe('Demo desk')
        expect(view.querySelector('nav[aria-label="Mobile navigation"] a[href="/demo"]')).toBeNull()
        expect(view.querySelector('nav[aria-label="Mobile navigation"] a[href="/demo/desk"]')).toBeNull()
        const mobileToggle = view.querySelector<HTMLButtonElement>('nav[aria-label="Mobile navigation"] button[aria-label="More tools"]')!
        await act(async () => mobileToggle.click())
        await until(() => !!document.querySelector('nav[aria-label="More navigation"] a[href="/demo"]'))
        const phone = document.querySelector('nav[aria-label="More navigation"] a[href="/demo"]')
        expect(phone?.textContent).toBe('Demo feature3')
        expect(phone?.querySelector('.nav-count')?.textContent).toBe('3')
        expect(document.querySelector('nav[aria-label="More navigation"] a[href="/demo/desk"]')?.textContent).toBe('Demo desk')
    })

    it('home section: Listen now shows the feature section above the episodes', async () => {
        const view = await show(<Routes><Route path="/home" element={<HomePageSelector/>}>
            <Route path="view" element={<p className="home-episodes">Episodes</p>}/></Route></Routes>, '/home/view')
        const section = view.querySelector('.home-sections .demo-home'), episodes = view.querySelector('.home-episodes')
        expect(section?.textContent).toBe('Demo home section')
        expect(section!.compareDocumentPosition(episodes!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    })

    it('settings tabs: the settings page links to the feature tab', async () => {
        const view = await show(<Routes><Route path="/settings" element={<SettingsPage/>}>
            <Route path="demo" element={<p>Demo settings page</p>}/></Route></Routes>, '/settings/demo')
        expect(view.querySelector('a[href="/settings/demo"]')?.textContent).toBe('Demo settings')
        expect(view.textContent).toContain('Demo settings page')
    })

    it('routes: App registers the feature page and settings tab', async () => {
        vi.stubGlobal('matchMedia', () => ({matches: false, addEventListener() {}, removeEventListener() {}}))  // jsdom has none
        const {router} = await import('../../src/App')
        const root = router.routes.find(route => route.path === '/')!
        expect(root.children?.some(route => route.path === 'demo')).toBe(true)
        const settings = root.children?.find(route => route.path === 'settings')
        expect(settings?.children?.some(route => route.path === 'demo')).toBe(true)
    }, 30_000)  // App.tsx pulls in every page; importing it is slow on a busy machine

    it('row badges: episode rows show the badge for the episode', async () => {
        const view = await show(<ListenEpisodeRow episode={episode}/>)
        expect(view.querySelector('.episode-badges .demo-badge')?.textContent).toBe('HEAR public-1')
    })

    it('player actions: the player bar shows the action for the playing episode', async () => {
        useAudioPlayer.setState({loadedPodcastEpisode: {podcastEpisode: episode, chapters: []}, metadata: {currentTime: 30, duration: 600, percentage: 5}})
        const view = await show(<DrawerAudioPlayer audioAmplifier={undefined}/>)
        expect(view.querySelector('.player-actions button')?.getAttribute('aria-label')).toBe('Demo action for public-1 at 30')
    })

    it('episode workspace: header above the tabs, a tool tab with its panel, and Make a transcript', async () => {
        useAudioPlayer.setState({loadedPodcastEpisode: undefined, metadata: undefined, pendingSeek: undefined})
        vi.stubGlobal('fetch', vi.fn(async (path: string) => {
            const replies: Record<string, unknown> = {
                '/api/v1/episodes/public-1': {podcastEpisode: episode, podcastHistoryItem: null},
                '/companion/episodes/public-1/transcript': {episode_id: 'public-1', source: null, timed: false, text: '', segments: []},
                '/companion/notes?episode_id=public-1': [],
                '/companion/answers/status': {configured: false},
                '/api/v1/podcasts/episodes/internal-1/transcripts': [],
            }
            if (!(path in replies)) throw new Error('unexpected request ' + path)
            return new Response(JSON.stringify(replies[path]), {status: 200})
        }))
        const view = await show(<Routes><Route path="/learn" element={<Learn/>}/></Routes>, '/learn?episode=public-1')
        await until(() => !!view.querySelector('.demo-header'))
        expect(view.querySelector('.demo-header')?.textContent).toBe('Demo header for public-1 at 0')
        const header = view.querySelector('.episode-headers')!, tabs = view.querySelector('.workspace-tabs')!
        expect(header.compareDocumentPosition(tabs) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
        expect(tabs.hasAttribute('data-tools')).toBe(true)
        expect(buttons(tabs).map(button => button.textContent)).toEqual(['Transcript', 'Transcript', 'Ask', 'Notes ', 'Demo tool'])
        await until(() => !!named(view, 'Make a transcript'))
        await act(async () => named(tabs, 'Demo tool')!.click())
        expect(view.querySelector('.learning-layout')?.getAttribute('data-panel')).toBe('tool:demo:tool')
        await act(async () => named(view.querySelector('.episode-tool')!, 'Jump to 0:42')!.click())
        expect(view.querySelector('.demo-header')?.textContent).toBe('Demo header for public-1 at 42')
        await act(async () => view.querySelector<HTMLButtonElement>('.workspace-tab-desktop')!.click())
        expect(view.querySelector('.learning-layout')?.getAttribute('data-panel')).toBe('transcript')
        expect(view.querySelector('.episode-tool')).toBeNull()
    })
})
