// The voice-note player action: a note saves at the right
// position through the default (server transcription) path, and through the browser-recognition
// fallback, without ever uploading audio for that second path.
import {afterEach, beforeAll, describe, expect, it, vi} from 'vitest'
import {act, type ReactNode} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import i18n from '../../../language/i18n'
import en from './locales/en.json'

i18n.addResourceBundle('en', 'voice', en, true, true)
const {VoiceNoteButton} = await import('./VoiceNoteButton')

type Reply = {status: number; body?: unknown}
type Call = {key: string; body: unknown}
let root: Root | undefined

beforeAll(() => {(globalThis as {IS_REACT_ACT_ENVIRONMENT?: boolean}).IS_REACT_ACT_ENVIRONMENT = true})
afterEach(() => {
    act(() => root?.unmount())
    root = undefined
    document.body.innerHTML = ''
    vi.unstubAllGlobals()
    localStorage.clear()
})

/** Answers each request from `routes` ("METHOD path" -> replies in order; the last one repeats),
 * and records the parsed body of every call (companion.ts always sends JSON or a Blob). */
function serve(routes: Record<string, Reply[]>): Call[] {
    const calls: Call[] = []
    vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
        const key = `${init?.method ?? 'GET'} ${path}`
        const body = typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body
        calls.push({key, body})
        const replies = routes[key]
        if (!replies) throw new Error('unexpected request ' + key)
        const reply = replies.length > 1 ? replies.shift()! : replies[0]!
        return new Response(reply.body === undefined ? null : JSON.stringify(reply.body), {status: reply.status})
    }))
    return calls
}

async function show(ui: ReactNode) {
    const container = document.body.appendChild(document.createElement('div'))
    const client = new QueryClient({defaultOptions: {queries: {retry: false}}})
    root = createRoot(container)
    await act(async () => root!.render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>))
    return container
}

async function until(check: () => boolean) {
    for (let i = 0; i < 200 && !check(); i++) await act(() => new Promise(resolve => setTimeout(resolve, 10)))
    expect(check()).toBe(true)
}

const mic = (view: HTMLElement) => view.querySelector('button')!
const savedNote = (calls: Call[]) => calls.find(call => call.key === 'POST /companion/notes')?.body as
    Record<string, unknown> | undefined

class FakeMediaRecorder {
    static instances: FakeMediaRecorder[] = []
    static isTypeSupported = () => true
    state: 'inactive' | 'recording' = 'inactive'
    ondataavailable: ((event: {data: Blob}) => void) | null = null
    onstop: (() => void) | null = null
    onerror: (() => void) | null = null
    constructor(public stream: MediaStream) {FakeMediaRecorder.instances.push(this)}
    start() {this.state = 'recording'}
    stop() {
        this.state = 'inactive'
        this.ondataavailable?.({data: new Blob(['fake audio'], {type: 'audio/webm'})})
        this.onstop?.()
    }
}

function stubRecording() {
    FakeMediaRecorder.instances = []
    vi.stubGlobal('MediaRecorder', FakeMediaRecorder)
    const track = {stop: vi.fn()}
    const getUserMedia = vi.fn(async () => ({getTracks: () => [track]}) as unknown as MediaStream)
    vi.stubGlobal('navigator', {...navigator, mediaDevices: {getUserMedia}})
    return getUserMedia
}

type ResultEvent = {results: {0: {transcript: string}; isFinal: boolean}[]}

class FakeSpeechRecognition {
    static instances: FakeSpeechRecognition[] = []
    lang = ''
    interimResults = false
    continuous = false
    maxAlternatives = 1
    onresult: ((event: ResultEvent) => void) | null = null
    onerror: ((event: {error?: string}) => void) | null = null
    onend: (() => void) | null = null
    constructor() {FakeSpeechRecognition.instances.push(this)}
    start() { /* the test fires onresult directly */ }
    stop() { /* no-op: nothing pending to cancel in these tests */ }
}

describe('voice note', () => {
    it('records, transcribes on the server, and saves a note at the given position', async () => {
        const getUserMedia = stubRecording()
        const calls = serve({
            'GET /companion/speech/status': [{status: 200, body: {configured: true}}],
            'POST /companion/speech/transcribe': [{status: 200, body: {text: 'Check the BGP config', duration: 2}}],
            'POST /companion/notes': [{status: 201, body: {
                id: 'n1', episode_id: 'ep-1', title: 'x', position: 42, text: 'Check the BGP config', created_at: 'now'}}],
        })
        const view = await show(<VoiceNoteButton episodeId="ep-1" position={42}/>)
        await until(() => calls.some(call => call.key === 'GET /companion/speech/status'))
        await act(async () => mic(view).click())
        await until(() => FakeMediaRecorder.instances.length === 1)
        expect(getUserMedia).toHaveBeenCalled()
        await act(async () => mic(view).click())   // tap again: stop, transcribe, save
        await until(() => !!savedNote(calls))
        expect(calls.some(call => call.key === 'POST /companion/speech/transcribe')).toBe(true)
        const saved = savedNote(calls)!
        expect(saved.episode_id).toBe('ep-1')
        expect(saved.position).toBe(42)
        expect(saved.text).toBe('Check the BGP config')
        expect(typeof saved.id).toBe('string')
    })

    it('falls back to the browser\'s own speech recognition when that setting is on, with no audio upload', async () => {
        localStorage.setItem('voice.browserSpeechRecognition', '1')
        FakeSpeechRecognition.instances = []
        vi.stubGlobal('SpeechRecognition', FakeSpeechRecognition)
        const calls = serve({
            'GET /companion/speech/status': [{status: 200, body: {configured: false}}],   // must not matter: recognition needs no AI
            'POST /companion/notes': [{status: 201, body: {
                id: 'n2', episode_id: 'ep-1', title: 'x', position: 10, text: 'Remember the sponsor read', created_at: 'now'}}],
        })
        const view = await show(<VoiceNoteButton episodeId="ep-1" position={10}/>)
        await act(async () => mic(view).click())
        await until(() => FakeSpeechRecognition.instances.length === 1)
        const recognizer = FakeSpeechRecognition.instances[0]!
        await act(async () => recognizer.onresult?.({results: [{0: {transcript: 'Remember the sponsor read'}, isFinal: true}]}))
        await until(() => !!savedNote(calls))
        expect(calls.some(call => call.key === 'POST /companion/speech/transcribe')).toBe(false)
        const saved = savedNote(calls)!
        expect(saved.text).toBe('Remember the sponsor read')
        expect(saved.position).toBe(10)
    })

    it('does not open the mic when speech-to-text is unavailable and browser recognition is off', async () => {
        const getUserMedia = stubRecording()
        const calls = serve({'GET /companion/speech/status': [{status: 200, body: {configured: false}}]})
        const view = await show(<VoiceNoteButton episodeId="ep-1" position={5}/>)
        await until(() => calls.some(call => call.key === 'GET /companion/speech/status'))
        await act(() => new Promise(resolve => setTimeout(resolve, 50)))   // let the query result commit
        await act(async () => mic(view).click())
        await act(() => new Promise(resolve => setTimeout(resolve, 50)))   // let any (wrongful) async chain run
        expect(getUserMedia).not.toHaveBeenCalled()
        expect(FakeMediaRecorder.instances).toHaveLength(0)
        expect(calls.some(call => call.key === 'POST /companion/speech/transcribe')).toBe(false)
        expect(calls.some(call => call.key === 'POST /companion/notes')).toBe(false)
        expect(mic(view).getAttribute('aria-pressed')).toBe('false')
    })
})
