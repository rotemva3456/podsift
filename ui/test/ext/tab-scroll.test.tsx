// Tapping a workspace tab (Transcript/Ask/Notes/a tool tab) scrolls the tab row near the top of
// the screen on a phone, so the newly chosen panel starts in view instead of staying scrolled out
// of sight. Desktop never scrolls here,
// and opening the page never auto-scrolls either - only a later tap does.
import {afterEach, beforeAll, beforeEach, describe, expect, it, vi} from 'vitest'
import {act} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {MemoryRouter, Route, Routes} from 'react-router-dom'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'

vi.mock('../../src/utils/http', () => ({client: {GET: async () => ({data: []})}, $api: {}, apiURL: '', uiURL: ''}))

const {Learn} = await import('../../src/pages/Learn')
const useAudioPlayer = (await import('../../src/store/AudioPlayerSlice')).default

const episode = {id: 'internal-1', episode_id: 'public-1', name: 'An episode', description: '', total_time: 600,
    local_url: '/proxy/podcast?episodeId=public-1', local_image_url: '', status: true, url: 'https://example.test/a.mp3'}
let root: Root | undefined
let scrolled: Element[] = []
const scrollIntoView = Element.prototype.scrollIntoView

beforeAll(() => {(globalThis as {IS_REACT_ACT_ENVIRONMENT?: boolean}).IS_REACT_ACT_ENVIRONMENT = true})
beforeEach(() => {
    document.body.innerHTML = '<audio id="audio-player"></audio>'
    scrolled = []
    Element.prototype.scrollIntoView = function () {scrolled.push(this)}  // jsdom has no scrolling
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
    vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
    useAudioPlayer.setState({loadedPodcastEpisode: undefined, metadata: undefined, pendingSeek: undefined})
    vi.stubGlobal('fetch', vi.fn(async (path: string) => {
        const replies: Record<string, unknown> = {
            '/api/v1/episodes/public-1': {podcastEpisode: episode, podcastHistoryItem: null},
            '/companion/episodes/public-1/transcript': {episode_id: 'public-1', source: null, timed: false, text: '', segments: []},
            '/companion/notes?episode_id=public-1': [],
            '/companion/answers/status': {configured: false},
        }
        if (!(path in replies)) throw new Error('unexpected request ' + path)
        return new Response(JSON.stringify(replies[path]), {status: 200})
    }))
})
afterEach(() => {
    act(() => root?.unmount()); root = undefined
    Element.prototype.scrollIntoView = scrollIntoView
    vi.restoreAllMocks(); vi.unstubAllGlobals()
})

async function open() {
    const container = document.body.appendChild(document.createElement('div'))
    const client = new QueryClient({defaultOptions: {queries: {retry: false}}})
    root = createRoot(container)
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/learn?episode=public-1']}>
        <Routes><Route path="/learn" element={<Learn/>}/></Routes></MemoryRouter></QueryClientProvider>))
    for (let i = 0; i < 200 && !container.querySelector('.workspace-tabs'); i++)
        await act(() => new Promise(resolve => setTimeout(resolve, 10)))
    return container
}

const tab = (view: HTMLElement, text: string) =>
    [...view.querySelectorAll<HTMLButtonElement>('.workspace-tabs button')].find(button => button.textContent?.startsWith(text))!

describe('workspace tab tap scroll', () => {
    it('never auto-scrolls just from opening the page, even on a phone', async () => {
        vi.stubGlobal('innerWidth', 375)
        await open()
        expect(scrolled).toEqual([])
    })

    it('on a phone, tapping a tab scrolls the tab row into view', async () => {
        vi.stubGlobal('innerWidth', 375)
        const view = await open()
        await act(async () => tab(view, 'Ask').click())
        expect(scrolled).toEqual([view.querySelector('.workspace-tabs')])
    })

    it('on a wider screen, tapping a tab does not scroll', async () => {
        vi.stubGlobal('innerWidth', 1024)
        const view = await open()
        await act(async () => tab(view, 'Notes').click())
        expect(scrolled).toEqual([])
    })
})
