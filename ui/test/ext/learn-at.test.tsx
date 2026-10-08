// /learn?episode=…&at=<seconds> opens the transcript at the passage that holds that time.
import {afterEach, beforeAll, beforeEach, describe, expect, it, vi} from 'vitest'
import {act} from 'react'
import {createRoot, Root} from 'react-dom/client'
import {MemoryRouter, Route, Routes} from 'react-router-dom'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'

vi.mock('../../src/utils/http', () => ({client: {GET: async () => ({data: []})}, $api: {}, apiURL: '', uiURL: ''}))

const {Learn} = await import('../../src/pages/Learn')
const useAudioPlayer = (await import('../../src/store/AudioPlayerSlice')).default

const episode = {id: 'internal-1', episode_id: 'public-1', name: 'An episode', description: '', total_time: 600,
    local_url: '/proxy/podcast?episodeId=public-1', local_image_url: '', status: true, url: 'https://example.test/a.mp3'}
const segments = [{id: '0', start: 0, end: 20, text: 'The introduction.'}, {id: '1', start: 20, end: 45, text: 'What subnets are for.'},
    {id: '2', start: 45, end: 60, text: 'How a prefix sets the size.'}]
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
            '/companion/episodes/public-1/transcript': {episode_id: 'public-1', source: 'Publisher transcript', timed: true,
                text: segments.map(s => s.text).join('\n'), segments},
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

async function open(url: string) {
    const container = document.body.appendChild(document.createElement('div'))
    const client = new QueryClient({defaultOptions: {queries: {retry: false}}})
    root = createRoot(container)
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[url]}>
        <Routes><Route path="/learn" element={<Learn/>}/></Routes></MemoryRouter></QueryClientProvider>))
    for (let i = 0; i < 200 && !container.querySelector('.transcript-passages li'); i++)
        await act(() => new Promise(resolve => setTimeout(resolve, 10)))
    return container
}

const marked = (view: HTMLElement) => [...view.querySelectorAll('.transcript-passages .active-passage')].map(li => li.textContent)

describe('/learn?at=', () => {
    it('scrolls to the passage that holds the time and marks it', async () => {
        const view = await open('/learn?episode=public-1&at=30')
        expect(marked(view)).toEqual(['0:20What subnets are for.'])
        expect(scrolled).toHaveLength(1)
        expect(scrolled[0]?.textContent).toBe('0:20What subnets are for.')
        expect(view.textContent).toContain('Play from 0:30')
        await act(() => new Promise(resolve => setTimeout(resolve, 30)))
        expect(scrolled).toHaveLength(1)  // once, not on every render
    })

    it('choosing another moment moves on from the requested passage', async () => {
        const view = await open('/learn?episode=public-1&at=30')
        const later = view.querySelector<HTMLButtonElement>('button[aria-label="Play passage at 0:45"]')!
        await act(async () => later.click())
        expect(view.querySelector('.requested-passage')).toBeNull()
        expect(view.textContent).toContain('Play from 0:45')
    })

    it('without at, or at a time outside every passage, nothing is marked or scrolled', async () => {
        for (const url of ['/learn?episode=public-1', '/learn?episode=public-1&at=900']) {
            const view = await open(url)
            expect(marked(view)).toEqual([])
            expect(scrolled).toEqual([])
            act(() => root?.unmount())
            view.remove()
        }
    })
})
