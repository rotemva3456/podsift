import {act} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {afterEach, beforeAll, describe, expect, it, vi} from 'vitest'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import {MemoryRouter} from 'react-router-dom'

vi.mock('../../../utils/http', () => ({client: {}, $api: {}, apiURL: '', uiURL: ''}))
const {VideoWorkspace} = await import('./VideoWorkspace')
const {default: audio} = await import('../../../store/AudioPlayerSlice')
let root: Root | undefined

const source = {
    id: 'video-1', title: 'Downloaded lesson.mp4', duration: 90, bytes: 12345, has_audio: true,
    status: 'saved', job_kind: null, progress: 0, error: null, has_transcript: false, has_speech: false,
    media_url: '/companion/videos/video-1/media?token=private-read', learning: null, visual_review: null,
}
const configured = {speech_configured: true, learning_configured: true, vision_configured: false, max_bytes: 1024**3}
const page = {video_id: source.id, digest: 'd', segments: [{id: 'v1', start: 12, end: 22, text: 'The original source words.'}],
    next_cursor: null, total_segments: 1, processed_seconds: 90, visual_coverage: 'none'}
const learning = {task: 'watch_plan', goal: '', minutes: 1, moments: [{start: 42, end: 63, title: 'Inspect the example', why: 'The speaker explains a useful diagram.', action: 'check_screen'}],
    points: [{text: 'A supported recap.', sources: [{id: 'v1', start: 12, end: 22, text: page.segments[0]!.text}]}], caveat: 'Speech only; diagrams remain unobserved.'}

beforeAll(() => {(globalThis as {IS_REACT_ACT_ENVIRONMENT?: boolean}).IS_REACT_ACT_ENVIRONMENT = true})
afterEach(() => {act(() => root?.unmount()); root = undefined; document.body.innerHTML = ''; vi.restoreAllMocks(); vi.unstubAllGlobals(); audio.setState({isPlaying: false})})

async function show({id = source.id, data = source, settings = configured, transcript = page, handler}: {
    id?: string | null; data?: unknown; settings?: unknown; transcript?: unknown
    handler?: (path: string, options?: RequestInit) => Response | undefined
} = {}) {
    const calls: {path: string; method: string}[] = []
    vi.stubGlobal('fetch', vi.fn(async (path: string, options?: RequestInit) => {
        calls.push({path, method: options?.method ?? 'GET'})
        const custom = handler?.(path, options)
        if (custom) return custom
        const payload = path.endsWith('/status') ? settings : path.includes('/transcript') ? transcript : path.endsWith('/videos?cursor=0') ? {videos: [source], next_cursor: null} : data
        return new Response(JSON.stringify(payload), {status: 200})
    }))
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function(this: HTMLMediaElement) {this.dispatchEvent(new Event('play')); return Promise.resolve()})
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
    const view = document.body.appendChild(document.createElement('div'))
    const client = new QueryClient({defaultOptions: {queries: {retry: false}}})
    root = createRoot(view)
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter><VideoWorkspace videoId={id ?? undefined}/></MemoryRouter></QueryClientProvider>))
    return {view, calls}
}

async function until(check: () => boolean) {
    for (let i = 0; i < 100 && !check(); i++) await act(() => new Promise(resolve => setTimeout(resolve, 10)))
    expect(check()).toBe(true)
}
const button = (view: HTMLElement, text: string) => [...view.querySelectorAll('button')].find(item => item.textContent === text)!

describe('Downloaded video learning', () => {
    it('opens a saved video for playback without automatically processing it', async () => {
        const {view, calls} = await show()
        await until(() => !!view.querySelector('video'))
        expect(view.querySelector('video')?.getAttribute('src')).toBe(source.media_url)
        expect(view.textContent).toContain('Transcribe full video')
        expect(calls.every(c => c.method === 'GET')).toBe(true)
        expect(view.textContent).not.toContain('Course Watcher')
        expect(view.textContent).not.toContain('quiz')
    })

    it('keeps playback available when speech and learning providers are unconfigured', async () => {
        const {view, calls} = await show({settings: {...configured, speech_configured: false, learning_configured: false}})
        await until(() => view.textContent!.includes('Connect a speech provider'))
        expect(button(view, 'Transcribe full video').disabled).toBe(true)
        expect(view.querySelector('video')).not.toBeNull()
        expect(calls.every(c => c.method === 'GET')).toBe(true)
    })

    it('returns cited guidance to the original second and pauses podcast audio', async () => {
        const {view} = await show({data: {...source, has_transcript: true, has_speech: true, status: 'ready', learning}})
        await until(() => view.textContent!.includes('Inspect the example'))
        await act(async () => view.querySelector('video')!.dispatchEvent(new Event('loadedmetadata')))
        audio.setState({isPlaying: true})
        const seek = [...view.querySelectorAll('button')].find(item => item.textContent!.includes('Inspect the example'))!
        await act(async () => seek.click())
        expect(view.querySelector('video')!.currentTime).toBe(42)
        expect(audio.getState().isPlaying).toBe(false)
        expect(view.textContent).toContain('Visuals have not been analyzed')
        expect(view.textContent).toContain('Check the screen here')
        await act(async () => button(view, 'Source 0:12').click())
        expect(view.querySelector('video')!.currentTime).toBe(12)
    })

    it('reads later transcript pages without changing the video player', async () => {
        const {view, calls} = await show({data: {...source, has_transcript: true, has_speech: true}, transcript: {...page, next_cursor: 1},
            handler: path => path.endsWith('cursor=1') ? new Response(JSON.stringify({...page, segments: [{id: 'v2', start: 75, end: 89, text: 'Important final explanation.'}]})) : undefined})
        await until(() => view.textContent!.includes('Read more transcript'))
        const player = view.querySelector('video')!
        player.currentTime = 31
        await act(async () => button(view, 'Read more transcript').click())
        await until(() => view.textContent!.includes('Important final explanation.'))
        expect(calls.some(c => c.path.endsWith('cursor=1'))).toBe(true)
        expect(view.querySelector('video')).toBe(player)
        expect(player.currentTime).toBe(31)
    })

    it('honors a citation clicked before video metadata arrives instead of resetting to resume time', async () => {
        const {view} = await show({data: {...source, has_transcript: true, has_speech: true, status: 'ready', learning}})
        await until(() => view.textContent!.includes('Inspect the example'))
        const player = view.querySelector('video')!
        const seek = [...view.querySelectorAll('button')].find(item => item.textContent!.includes('Inspect the example'))!
        await act(async () => seek.click())
        expect(player.currentTime).toBe(0)
        await act(async () => player.dispatchEvent(new Event('loadedmetadata')))
        expect(player.currentTime).toBe(42)
    })

    it('exposes optional visual preparation only when the watcher is configured', async () => {
        const {view, calls} = await show({settings: {...configured, vision_configured: true}})
        await until(() => view.textContent!.includes('Check visuals with Course Watcher'))
        const details = [...view.querySelectorAll('details')].find(item => item.textContent!.includes('Course Watcher'))!
        expect(details.open).toBe(false)
        expect(calls.every(c => c.method === 'GET')).toBe(true)
        expect(view.textContent).toContain('edit the plan before')
    })

    it('keeps the original video and transcript when learning fails', async () => {
        const {view, calls} = await show({data: {...source, has_transcript: true, has_speech: true}, handler: (path, options) =>
            options?.method === 'POST' && path.endsWith('/learn') ? new Response(JSON.stringify({detail: 'Provider unavailable. Your words are saved.'}), {status: 502}) : undefined})
        await until(() => !!button(view, 'Summarize'))
        await act(async () => button(view, 'Summarize').click())
        await until(() => view.textContent!.includes('Provider unavailable.'))
        expect(view.textContent).toContain(page.segments[0]!.text)
        expect(view.querySelector('video')).not.toBeNull()
        expect(calls.filter(c => c.method === 'POST').map(c => c.path)).toEqual(['/companion/videos/video-1/learn'])
    })

    it('shows existing videos alongside import without starting AI', async () => {
        const {view, calls} = await show({id: null})
        await until(() => view.textContent!.includes(source.title))
        expect(view.querySelector('input[type="file"]')).not.toBeNull()
        expect(view.querySelector('a[href="/learn?video=video-1"]')).not.toBeNull()
        expect(view.textContent).toContain('start when you choose them')
        expect(calls.every(c => c.method === 'GET')).toBe(true)
    })
})
