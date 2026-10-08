import {afterEach, beforeAll, beforeEach, describe, expect, it, vi} from 'vitest'
import {act, useRef, type ComponentProps} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {MemoryRouter, Route, Routes, useLocation, useNavigate} from 'react-router-dom'
import {QueryClient, QueryClientProvider, type UseQueryResult} from '@tanstack/react-query'
import type {Transcript} from '../../src/utils/companion'

vi.mock('../../src/ext/features/cuts/CutWorkspace', () => ({
    CutWorkspace: ({initialPlanId}: {initialPlanId?: string}) => <p data-testid="cut-workspace">Plan {initialPlanId}</p>,
}))
vi.mock('../../src/components/AskEpisode', () => ({
    AskEpisode: ({prefill}: {prefill?: {question: string}}) => <section data-testid="ask-panel" data-question={prefill?.question ?? ''}>Ask this episode</section>,
}))
vi.mock('../../src/ext/registry', () => ({
    episodeHeaders: [{featureId: 'demo', Component: () => <p data-testid="overview-content">Overview content</p>}],
    episodeTools: [],
}))

const {SourceWorkspace} = await import('../../src/ext/features/source-workspace/SourceWorkspace')
const {Learn} = await import('../../src/pages/Learn')

const EP1 = '11111111-1111-4111-8111-111111111111'
const EP2 = '22222222-2222-4222-8222-222222222222'
const DIGEST = '0123456789abcdef0123456789abcdef'
const episode = (id = EP1) => ({id: `internal-${id}`, episode_id: id, name: `Episode ${id}`, description: '', total_time: 90,
    local_url: `/audio/${id}`, local_image_url: '', status: true, url: `https://example.test/${id}`})
const timed = (id = EP1): Transcript => ({episode_id: id, source: 'Publisher transcript', timed: true, digest: DIGEST,
    text: 'One. Two. Three.', segments: [{id: 's1', start: 0, end: 10, text: 'One.'}, {id: 's2', start: 10, end: 20, text: 'Two.'}, {id: 's3', start: 20, end: 30, text: 'Three.'}]})
const query = (data: Transcript) => ({data, isLoading: false, isPending: false, isError: false, error: null, refetch: vi.fn()}) as unknown as UseQueryResult<Transcript, Error>

let root: Root | undefined
let calls: {method: string; path: string; body?: Record<string, unknown>}[] = []
let planResolve: ((value: Response) => void) | undefined
let noteResolve: ((value: Response) => void) | undefined
let deferNote = false
let testClient: QueryClient

beforeAll(() => {(globalThis as {IS_REACT_ACT_ENVIRONMENT?: boolean}).IS_REACT_ACT_ENVIRONMENT = true})
beforeEach(() => {
    calls = []; planResolve = undefined; noteResolve = undefined; deferNote = false
    vi.stubGlobal('crypto', {randomUUID: () => '33333333-3333-4333-8333-333333333333'})
    vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
        const method = init?.method ?? 'GET'
        const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined
        calls.push({method, path: input, body})
        if (input.includes('/chapters')) return new Response('[]', {status: 200})
        if (input.startsWith('/companion/briefs')) return new Response('[]', {status: 200})
        if (input.startsWith('/companion/plans?')) return new Response('[]', {status: 200})
        if (input === `/api/v1/episodes/${EP1}`) return new Response(JSON.stringify({podcastEpisode: episode(), podcastHistoryItem: null}), {status: 200})
        if (input === `/companion/episodes/${EP1}/transcript`) return new Response(JSON.stringify(timed()), {status: 200})
        if (input === `/companion/notes?episode_id=${EP1}`) return new Response('[]', {status: 200})
        if (input === '/companion/notes' && method === 'POST') {
            if (deferNote) return new Promise<Response>(resolve => {noteResolve = resolve})
            return new Response(JSON.stringify({...body, title: 'Episode', created_at: '2026-10-04T00:00:00Z'}), {status: 201})
        }
        if (input === '/companion/plans' && method === 'POST') return new Promise<Response>(resolve => {planResolve = resolve})
        throw new Error(`unexpected ${method} ${input}`)
    }))
})
afterEach(() => {act(() => root?.unmount()); root = undefined; vi.restoreAllMocks(); vi.unstubAllGlobals()})

function Location() {const location = useLocation(); return <output data-testid="location">{location.search}</output>}
function HistoryControls() {const navigate = useNavigate(); return <><button data-testid="back" onClick={() => navigate(-1)}>Back</button><button data-testid="forward" onClick={() => navigate(1)}>Forward</button></>}
function Harness({sourcePosition = 0, ...props}: Pick<ComponentProps<typeof SourceWorkspace>, 'episode' | 'transcript'> & {sourcePosition?: number}) {
    const passages = useRef<HTMLOListElement>(null), active = useRef<HTMLLIElement>(null)
    return <><SourceWorkspace {...props} sourcePosition={sourcePosition} active={0} requestedPassage={-1} playing={false} follow={false}
        onFollow={() => {}} onSeek={() => {}} onExplain={() => {}} passagesRef={passages} activeRef={active}/><Location/><HistoryControls/></>
}
async function show(transcript = timed(), id = EP1, url = `/learn?episode=${id}`, sourcePosition = 0) {
    const container = document.body.appendChild(document.createElement('div'))
    root = createRoot(container)
    testClient = new QueryClient({defaultOptions: {queries: {retry: false}}})
    await act(async () => root!.render(<QueryClientProvider client={testClient}><MemoryRouter initialEntries={[url]}><Routes><Route path="/learn" element={<Harness episode={episode(id)} transcript={query(transcript)} sourcePosition={sourcePosition}/>}/></Routes></MemoryRouter></QueryClientProvider>))
    await act(() => new Promise(resolve => setTimeout(resolve, 0)))
    return container
}
async function showLearn(url = `/learn?episode=${EP1}`) {
    const container = document.body.appendChild(document.createElement('div'))
    root = createRoot(container)
    testClient = new QueryClient({defaultOptions: {queries: {retry: false}}})
    await act(async () => root!.render(<QueryClientProvider client={testClient}><MemoryRouter initialEntries={[url]}><Routes><Route path="/learn" element={<Learn/>}/></Routes></MemoryRouter></QueryClientProvider>))
    for (let index = 0; index < 100 && !container.querySelector('.transcript-passages li'); index++) await act(() => new Promise(resolve => setTimeout(resolve, 10)))
    return container
}
const button = (view: ParentNode, name: string) => [...view.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent === name || item.getAttribute('aria-label')?.startsWith(name))!
const type = async (element: HTMLTextAreaElement, value: string) => act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!
    setter.call(element, value); element.dispatchEvent(new Event('input', {bubbles: true}))
})

describe('transcript-first source workspace', () => {
    it.each(['focus', 'chill'])('labels manual selections as %s and persists the choice in the URL', async effort => {
        const view = await show(timed(), EP1, `/learn?episode=${EP1}&at=12`)
        await act(async () => button(view, 'Select passage at 0:00').click())
        await act(async () => button(view, effort === 'focus' ? 'Focus' : 'Chill').click())
        expect(view.querySelector('[data-testid="location"]')?.textContent).toContain(`learning_mode=${effort}`)
        expect(view.querySelector('[data-testid="location"]')?.textContent).toContain('at=12')
        await act(async () => button(view, 'Keep selected').click())
        const request = calls.find(call => call.method === 'POST' && call.path === '/companion/plans')!.body!
        expect(request).toMatchObject({mode: 'agent', learning_mode: effort, selections: [{keep: [{start_id: 's1', end_id: 's1'}]}]})
        for (let index = 0; index < 100 && !button(view, effort === 'focus' ? 'Focus' : 'Chill').disabled; index++) {
            await act(() => new Promise(resolve => setTimeout(resolve, 10)))
        }
        expect(button(view, effort === 'focus' ? 'Focus' : 'Chill').disabled).toBe(true)
    })

    it('keeps subject-load errors visible and retries without generating content', async () => {
        const normalFetch = globalThis.fetch
        let fail = true
        vi.stubGlobal('fetch', vi.fn((input: string, init?: RequestInit) => input.startsWith('/companion/briefs') && fail
            ? Promise.resolve(new Response(JSON.stringify({detail: 'Temporarily unavailable'}), {status: 503}))
            : normalFetch(input, init)))
        const view = await show()
        for (let i = 0; i < 100 && !view.querySelector('[data-subjects="error"]'); i++) await act(() => new Promise(resolve => setTimeout(resolve, 10)))
        expect(view.querySelector('.source-workspace')?.getAttribute('data-subjects')).toBe('error')
        expect(view.querySelector('.source-subjects [role="alert"]')?.textContent).toContain("Subjects couldn't load")
        fail = false
        await act(async () => button(view.querySelector('.source-subjects')!, 'Try again').click())
        for (let i = 0; i < 100 && !view.querySelector('[data-subjects="empty"]'); i++) await act(() => new Promise(resolve => setTimeout(resolve, 10)))
        expect(view.querySelector('.source-workspace')?.getAttribute('data-subjects')).toBe('empty')
        expect(calls.some(call => call.method === 'POST')).toBe(false)
    })

    it('opens on source words without generation and moves the selected quote into the Ask tab', async () => {
        const view = await showLearn()
        const overview = view.querySelector<HTMLDetailsElement>('.source-overview')!
        expect(overview.open).toBe(false)
        expect(overview.querySelector('[data-testid="overview-content"]')).not.toBeNull()
        expect([...view.querySelectorAll('.workspace-tabs button')].filter(item => item.textContent === 'Transcript')).toHaveLength(1)
        expect(calls.some(call => call.method === 'POST')).toBe(false)
        await act(async () => button(view, 'Select passage at 0:00').click())
        await act(async () => button(view, 'Explain in Ask').click())
        expect(view.querySelector('.learning-layout')?.getAttribute('data-panel')).toBe('ask')
        expect(view.querySelector('[data-testid="ask-panel"]')?.getAttribute('data-question')).toContain('One.')
        expect(calls.some(call => call.method === 'POST')).toBe(false)
    })

    it('honors overview=1 while keeping the overview content mounted', async () => {
        const view = await showLearn(`/learn?episode=${EP1}&overview=1`)
        expect(view.querySelector<HTMLDetailsElement>('.source-overview')?.open).toBe(true)
        expect(view.querySelector('[data-testid="overview-content"]')).not.toBeNull()
    })

    it('does not generate anything on load and sends selected source IDs in mode=agent', async () => {
        const view = await show(timed(), EP1, `/learn?episode=${EP1}&at=12`)
        expect(calls.every(call => call.method === 'GET')).toBe(true)
        await act(async () => button(view, 'Select passage').click())
        await act(async () => button(view, 'Keep selected').click())
        const request = calls.find(call => call.method === 'POST' && call.path === '/companion/plans')!.body!
        expect(request).toMatchObject({mode: 'agent', skip_ads: false, episode_ids: [EP1], selections: [{episode_id: EP1, transcript_digest: DIGEST,
            keep: [{start_id: 's1', end_id: 's1', why: 'Kept because the user selected this passage in Learn.', relevance: 3}]}]})
        await act(async () => planResolve?.(new Response(JSON.stringify({id: 'plan-1', want: 'Selected', created_at: 'now', episodes: [{episode_id: EP1}]}), {status: 201})))
        const location = view.querySelector('[data-testid="location"]')?.textContent ?? ''
        expect(location).toContain('plan=plan-1')
        expect(location).not.toContain('at=')
    })

    it('keeps sponsor skipping only when making a listen without the selected pin', async () => {
        const view = await show()
        await act(async () => button(view, 'Select passage at 0:00').click())
        await act(async () => button(view, 'Make a listen without selected').click())
        const request = calls.find(call => call.method === 'POST' && call.path === '/companion/plans')!.body!
        expect(request).toMatchObject({mode: 'agent', skip_ads: true, selections: [{skip: [
            {start_id: 's1', end_id: 's1', why: 'Skipped because the user selected this passage for removal in Learn.'},
        ]}]})
    })

    it('saves an exact quote without requiring a note and links the success to Knowledge', async () => {
        const view = await show()
        await act(async () => button(view, 'Select passage').click())
        await act(async () => button(view, 'Save idea').click())
        const saved = calls.find(call => call.path === '/companion/notes' && call.method === 'POST')!.body
        expect(saved).toMatchObject({episode_id: EP1, kind: 'highlight', start: 0, end: 10, quote: 'One.', text: 'One.'})
        expect(view.querySelector('a[href="/knowledge"]')?.textContent).toBe('Open Knowledge')
    })

    it('does not turn separate selected passages into one authoritative range', async () => {
        const view = await show(timed(), EP1, `/learn?episode=${EP1}`, 77)
        await act(async () => button(view, 'Select passage at 0:00').click())
        await act(async () => button(view, 'Select passage at 0:20').click())
        await act(async () => button(view, 'Save idea').click())
        const saved = calls.find(call => call.path === '/companion/notes' && call.method === 'POST')!.body!
        expect(saved).toMatchObject({episode_id: EP1, position: 0, text: 'One. Three.'})
        expect(saved).not.toHaveProperty('kind')
        expect(view.textContent).toContain('words between them are not included')
    })

    it('opens an older deep-linked plan even when it is outside the newest plan list', async () => {
        const view = await show(timed(), EP1, `/learn?episode=${EP1}&plan=older-plan`)
        expect(view.querySelector('[data-testid="cut-workspace"]')?.textContent).toBe('Plan older-plan')
    })

    it('follows Back and Forward URL changes between the original and selected listen', async () => {
        const view = await show(timed(), EP1, `/learn?episode=${EP1}&plan=older-plan`)
        expect(button(view, 'My selected listen').getAttribute('aria-pressed')).toBe('true')
        await act(async () => button(view, 'Original transcript').click())
        expect(button(view, 'Original transcript').getAttribute('aria-pressed')).toBe('true')
        await act(async () => view.querySelector<HTMLButtonElement>('[data-testid="back"]')!.click())
        expect(button(view, 'My selected listen').getAttribute('aria-pressed')).toBe('true')
        expect(view.querySelector('[data-testid="cut-workspace"]')?.textContent).toBe('Plan older-plan')
        await act(async () => view.querySelector<HTMLButtonElement>('[data-testid="forward"]')!.click())
        expect(button(view, 'Original transcript').getAttribute('aria-pressed')).toBe('true')
    })

    it('keeps reading and notes usable while precise actions are unavailable without timing, digest, or IDs', async () => {
        const untimed = {...timed(), timed: false, digest: undefined, segments: [{start: 0, end: null, text: 'Readable words.'}]}
        const view = await show(untimed)
        await act(async () => button(view, 'Select passage').click())
        expect(button(view, 'Keep selected').disabled).toBe(true)
        expect(button(view, 'Make a listen without selected').disabled).toBe(true)
        await type(view.querySelector('textarea')!, 'A plain note still works')
        expect(button(view, 'Save idea').disabled).toBe(false)
        expect(view.textContent).toContain('Reading and saving still work.')
    })

    it('ignores a late plan after the source changes', async () => {
        const view = await show(timed(), EP1, `/learn?episode=${EP1}&at=12`)
        await act(async () => button(view, 'Select passage').click())
        await act(async () => button(view, 'Keep selected').click())
        const client = new QueryClient({defaultOptions: {queries: {retry: false}}})
        await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/learn?episode=${EP2}&at=7`]}><Routes><Route path="/learn" element={<Harness episode={episode(EP2)} transcript={query(timed(EP2))}/>}/></Routes></MemoryRouter></QueryClientProvider>))
        await act(async () => planResolve?.(new Response(JSON.stringify({id: 'late-plan', want: 'Late', created_at: 'now', episodes: [{episode_id: EP1}]}), {status: 201})))
        expect(view.querySelector('[data-testid="location"]')?.textContent).not.toContain('late-plan')
    })

    it('ignores note and plan resolutions after the workspace unmounts', async () => {
        deferNote = true
        const view = await show()
        await act(async () => button(view, 'Select passage').click())
        const invalidate = vi.spyOn(testClient, 'invalidateQueries')
        const setData = vi.spyOn(testClient, 'setQueryData')
        await act(async () => {button(view, 'Save idea').click(); button(view, 'Keep selected').click()})
        await act(async () => root!.unmount())
        root = undefined
        await act(async () => {
            noteResolve?.(new Response(JSON.stringify({id: 'note-late', episode_id: EP1, title: 'Episode', position: 0, text: 'One.', created_at: 'now'}), {status: 201}))
            planResolve?.(new Response(JSON.stringify({id: 'plan-late', want: 'Late', created_at: 'now', episodes: [{episode_id: EP1}]}), {status: 201}))
            await Promise.resolve()
        })
        expect(invalidate).not.toHaveBeenCalled()
        expect(setData).not.toHaveBeenCalled()
    })
})
