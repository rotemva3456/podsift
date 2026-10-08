import {afterEach, beforeAll, describe, expect, it, vi} from 'vitest'
import {act} from 'react'
import {createRoot, Root} from 'react-dom/client'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import '../../src/ext/registry'
import {MAKING_LIMIT_MS, MakeTranscript, POLL_MS} from '../../src/ext/shared/MakeTranscript'
import type {PodcastEpisode} from '../../src/ext/types'

const episode = {id: 'internal-1', episode_id: 'public-1', name: 'An episode', status: true} as PodcastEpisode
type Reply = {status: number; body?: unknown}
let root: Root | undefined

beforeAll(() => {(globalThis as {IS_REACT_ACT_ENVIRONMENT?: boolean}).IS_REACT_ACT_ENVIRONMENT = true})
afterEach(() => {act(() => root?.unmount()); root = undefined; document.body.innerHTML = ''; vi.useRealTimers(); vi.unstubAllGlobals()})

/** Answer each request from `routes` ("METHOD path" → replies used in order; the last one repeats). */
function serve(routes: Record<string, Reply[]>) {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
        const key = `${init?.method ?? 'GET'} ${path}`
        calls.push(key)
        const replies = routes[key]
        if (!replies) throw new Error('unexpected request ' + key)
        const reply = replies.length > 1 ? replies.shift()! : replies[0]!
        return new Response(reply.body === undefined ? null : JSON.stringify(reply.body), {status: reply.status})
    }))
    return calls
}

async function show(props: {episode: PodcastEpisode; onReady: () => void}) {
    const container = document.body.appendChild(document.createElement('div'))
    const client = new QueryClient({defaultOptions: {queries: {retry: false}}})
    root = createRoot(container)
    await act(async () => root!.render(<QueryClientProvider client={client}><MakeTranscript {...props}/></QueryClientProvider>))
    return container
}

async function until(check: () => boolean) {
    for (let i = 0; i < 100 && !check(); i++) await act(() => new Promise(resolve => setTimeout(resolve, 10)))
    expect(check()).toBe(true)
}

const named = (container: HTMLElement, name: string) => [...container.querySelectorAll('button')].find(b => b.textContent?.includes(name))
const click = async (container: HTMLElement, name: string) => {
    const button = named(container, name)
    expect(button, `button "${name}"`).toBeTruthy()
    await act(async () => button!.click())
}

const TRANSCRIBE = 'POST /api/v1/podcasts/episodes/internal-1/transcribe'
const LIST = 'GET /api/v1/podcasts/episodes/internal-1/transcripts'

describe('MakeTranscript', () => {
    it('200: shows "Making the transcript…" and reports ready once PodFetch has parsed it', async () => {
        const calls = serve({[LIST]: [{status: 200, body: []}, {status: 200, body: [{source: 'generated', status: 'parsed'}]}],
            [TRANSCRIBE]: [{status: 200}]})
        const onReady = vi.fn()
        const view = await show({episode, onReady})
        await click(view, 'Make a transcript')
        await until(() => onReady.mock.calls.length === 1)
        expect(view.textContent).toContain('The transcript is ready.')
        expect(calls).toContain(TRANSCRIBE)
    })

    it('409 (a job already exists): shows progress and keeps polling until the transcript exists', async () => {
        serve({[LIST]: [{status: 200, body: []}, {status: 200, body: [{source: 'generated', status: 'running'}]},
            {status: 200, body: [{source: 'generated', status: 'parsed'}]}], [TRANSCRIBE]: [{status: 409, body: {errorCode: 'TRANSCRIPTION_JOB_ALREADY_EXISTS'}}]})
        const onReady = vi.fn()
        const view = await show({episode, onReady})
        vi.useFakeTimers({toFake: ['setTimeout', 'setInterval', 'clearTimeout', 'clearInterval']})
        await click(view, 'Make a transcript')
        await act(async () => {await vi.advanceTimersByTimeAsync(10)})
        expect(view.querySelector('[role=status]')?.textContent).toContain('Making the transcript…')
        expect(onReady).not.toHaveBeenCalled()
        await act(async () => {await vi.advanceTimersByTimeAsync(POLL_MS + 10)})
        expect(onReady).toHaveBeenCalledTimes(1)
    })

    it('503: says transcription is off and points to TRANSCRIPTION_API_* in .env', async () => {
        serve({[LIST]: [{status: 200, body: []}], [TRANSCRIBE]: [{status: 503}]})
        const view = await show({episode, onReady: vi.fn()})
        await click(view, 'Make a transcript')
        await until(() => view.textContent!.includes('Transcription is off'))
        expect(view.querySelector('code')?.textContent).toBe('TRANSCRIPTION_API_BASE_URL')
        expect(view.textContent).toContain('.env')
        expect(view.textContent).toContain('Groq')
    })

    it('downloads an episode that is not downloaded yet, then queues the transcript again', async () => {
        const calls = serve({[LIST]: [{status: 200, body: []}, {status: 200, body: [{source: 'generated', status: 'parsed'}]}],
            [TRANSCRIBE]: [{status: 200}],
            'GET /api/v1/episodes/public-1': [{status: 200, body: {podcastEpisode: {status: false}}}, {status: 200, body: {podcastEpisode: {status: true}}}],
            'PUT /api/v1/podcasts/public-1/episodes/download': [{status: 200}]})
        const onReady = vi.fn()
        const view = await show({episode: {...episode, status: false}, onReady})
        await click(view, 'Make a transcript')
        await until(() => onReady.mock.calls.length === 1)
        expect(calls.filter(call => call === TRANSCRIBE)).toHaveLength(2)
        expect(calls.indexOf('PUT /api/v1/podcasts/public-1/episodes/download')).toBeLessThan(calls.lastIndexOf(TRANSCRIBE))
    })

    it('shows why a job failed and lets the user try again', async () => {
        serve({[LIST]: [{status: 200, body: [{source: 'generated', status: 'failed', error: 'whisper down'}]},
            {status: 200, body: [{source: 'generated', status: 'parsed'}]}], [TRANSCRIBE]: [{status: 200}]})
        const onReady = vi.fn()
        const view = await show({episode, onReady})
        await until(() => view.textContent!.includes("The transcript couldn't be made: whisper down"))
        await click(view, 'Try again')
        await until(() => onReady.mock.calls.length === 1)
    })

    it('a job that fails while we wait stops the polling and shows the reason with a retry', async () => {
        const calls = serve({[LIST]: [{status: 200, body: []}, {status: 200, body: [{source: 'generated', status: 'running'}]},
            {status: 200, body: [{source: 'generated', status: 'failed', error: 'Whisper answered 413 Payload Too Large'}]}],
            [TRANSCRIBE]: [{status: 200}]})
        const view = await show({episode, onReady: vi.fn()})
        vi.useFakeTimers({toFake: ['setTimeout', 'setInterval', 'clearTimeout', 'clearInterval']})
        await click(view, 'Make a transcript')
        await act(async () => {await vi.advanceTimersByTimeAsync(POLL_MS + 10)})
        expect(view.querySelector('[role=alert]')?.textContent).toBe(
            "The transcript couldn't be made: Whisper answered 413 Payload Too Large Long episodes can be bigger than the transcription service accepts.")
        const polls = calls.filter(call => call === LIST).length
        await act(async () => {await vi.advanceTimersByTimeAsync(POLL_MS * 4)})
        expect(calls.filter(call => call === LIST)).toHaveLength(polls)
        expect(named(view, 'Try again')).toBeTruthy()
    })

    it('a job that never finishes stops polling after the time limit and offers to check again', async () => {
        const calls = serve({[LIST]: [{status: 200, body: []}, {status: 200, body: [{source: 'generated', status: 'running'}]}],
            [TRANSCRIBE]: [{status: 409}]})
        const view = await show({episode, onReady: vi.fn()})
        vi.useFakeTimers({toFake: ['setTimeout', 'setInterval', 'clearTimeout', 'clearInterval', 'Date']})
        await click(view, 'Make a transcript')
        await act(async () => {await vi.advanceTimersByTimeAsync(MAKING_LIMIT_MS + 2 * POLL_MS)})
        expect(view.textContent).toContain("still isn't ready after 30 minutes")
        const polls = calls.filter(call => call === LIST).length
        await act(async () => {await vi.advanceTimersByTimeAsync(POLL_MS * 4)})
        expect(calls.filter(call => call === LIST)).toHaveLength(polls)
        await click(view, 'Check again')
        await act(async () => {await vi.advanceTimersByTimeAsync(10)})
        expect(view.textContent).toContain('Making the transcript…')
    }, 30_000)  // 30 fake minutes are ~360 polls; slow on a busy machine

    it('stops after the time limit even when PodFetch keeps failing to answer', async () => {
        serve({[LIST]: [{status: 200, body: []}, {status: 500}], [TRANSCRIBE]: [{status: 200}]})
        const view = await show({episode, onReady: vi.fn()})
        vi.useFakeTimers({toFake: ['setTimeout', 'setInterval', 'clearTimeout', 'clearInterval', 'Date']})
        await click(view, 'Make a transcript')
        await act(async () => {await vi.advanceTimersByTimeAsync(MAKING_LIMIT_MS + 2 * POLL_MS)})
        expect(view.textContent).toContain("still isn't ready after 30 minutes")
    }, 30_000)

    it('a job that ends without any transcript says so instead of waiting', async () => {
        serve({[LIST]: [{status: 200, body: []}], [TRANSCRIBE]: [{status: 409}]})
        const view = await show({episode, onReady: vi.fn()})
        await click(view, 'Make a transcript')
        await until(() => view.textContent!.includes('Transcription finished without a transcript'))
    })

    it('a job that is already running shows progress on arrival', async () => {
        serve({[LIST]: [{status: 200, body: [{source: 'generated', status: 'pending'}]}]})
        const view = await show({episode, onReady: vi.fn()})
        await until(() => view.textContent!.includes('Making the transcript…'))
    })

    it('403: explains that only an admin can make transcripts', async () => {
        serve({[LIST]: [{status: 200, body: []}], [TRANSCRIBE]: [{status: 403}]})
        const view = await show({episode, onReady: vi.fn()})
        await click(view, 'Make a transcript')
        await until(() => view.textContent!.includes('Only an admin can make transcripts'))
    })
})
