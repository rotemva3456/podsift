// Flashcards UI: the Cards tab's states, the review loop with grading, the due badge, and
// "replay what I forget" reusing Smart Play helpers.
import {afterEach, beforeAll, describe, expect, it, vi} from 'vitest'
import {act, type ReactNode} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {MemoryRouter} from 'react-router-dom'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import i18n from '../../../language/i18n'
import en from './locales/en.json'
import shared from '../../shared/locales/en.json'
import type {PodcastEpisode} from '../../types'
import type {Plan} from '../cuts/api'
import type {Card, CardsResponse, Due} from './api'

vi.mock('../../../utils/http', () => ({client: {GET: async () => ({data: []})}, $api: {}, apiURL: '', uiURL: ''}))
i18n.addResourceBundle('en', 'review', en, true, true)
i18n.addResourceBundle('en', 'shared', shared, true, true)
const {CardsPanel} = await import('./CardsPanel')
const {ReviewPage} = await import('./ReviewPage')
const {DueBadge} = await import('./DueBadge')
const {Replay} = await import('./Replay')

const episode = {id: 'internal-1', episode_id: 'public-1', podcast_id: 'show-1', name: 'An episode', status: true,
    total_time: 900} as unknown as PodcastEpisode
const CARDS = 'GET /companion/episodes/public-1/cards'
const MAKE = 'POST /companion/episodes/public-1/cards'
const DUE = 'GET /companion/review/due?limit=50'

const card = (change: Partial<Card> = {}): Card => ({id: 'c1', episode_id: 'public-1', episode_title: 'An episode',
    question: 'What stops a Layer 2 loop?', answer: 'Spanning tree protocol blocks a redundant port.',
    citations: [{segment_id: 's3', start: 42, end: 50}], due: 0, interval: 0, ease: 2.5, reps: 0, lapses: 0,
    model: 'llama-3.3-70b', created_at: '2026-09-24T10:00:00+00:00', ...change})
const cardsResponse = (change: Partial<CardsResponse> = {}): CardsResponse =>
    ({episode_id: 'public-1', cards: [], ai_ready: true, has_transcript: true, ...change})

type Reply = {status: number; body?: unknown}
let root: Root | undefined

beforeAll(() => {(globalThis as {IS_REACT_ACT_ENVIRONMENT?: boolean}).IS_REACT_ACT_ENVIRONMENT = true})
afterEach(() => {act(() => root?.unmount()); root = undefined; document.body.innerHTML = ''; vi.unstubAllGlobals()})

/** Answer each request from `routes` ("METHOD path" -> replies in order; the last one repeats). */
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

async function show(ui: ReactNode) {
    const container = document.body.appendChild(document.createElement('div'))
    const client = new QueryClient({defaultOptions: {queries: {retry: false}}})
    root = createRoot(container)
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>))
    return container
}

async function until(check: () => boolean) {
    for (let i = 0; i < 100 && !check(); i++) await act(() => new Promise(resolve => setTimeout(resolve, 10)))
    expect(check()).toBe(true)
}

const button = (view: HTMLElement, name: string) => [...view.querySelectorAll('button')].find(b => b.textContent?.includes(name))
const panel = () => <CardsPanel episodeId="public-1" episode={episode} position={0} seek={vi.fn()}/>

describe('cards tab', () => {
    it('no transcript: offers the shared "Make a transcript" button, no Make cards button', async () => {
        serve({[CARDS]: [{status: 200, body: cardsResponse({has_transcript: false})}],
            'GET /api/v1/podcasts/episodes/internal-1/transcripts': [{status: 200, body: []}]})
        const view = await show(panel())
        await until(() => !!button(view, 'Make a transcript'))
        expect(view.textContent).toContain('Cards need a transcript of this episode.')
        expect(button(view, 'Make cards')).toBeUndefined()
    })

    it('no AI: points to Settings -> AI, offers no Make cards button', async () => {
        serve({[CARDS]: [{status: 200, body: cardsResponse({ai_ready: false})}]})
        const view = await show(panel())
        await until(() => view.textContent!.includes("AI isn't connected yet."))
        expect(view.querySelector('a[href="/settings/ai"]')?.textContent).toBe('Connect AI in Settings → AI')
        expect(button(view, 'Make cards')).toBeUndefined()
    })

    it('Make cards: one POST, then shows the cards', async () => {
        const made = cardsResponse({cards: [card(), card({id: 'c2', question: 'What is a BPDU?', answer: 'A bridge protocol data unit.'})]})
        const calls = serve({[CARDS]: [{status: 200, body: cardsResponse()}], [MAKE]: [{status: 200, body: made}]})
        const view = await show(panel())
        await until(() => !!button(view, 'Make cards'))
        await act(async () => button(view, 'Make cards')!.click())
        await until(() => view.textContent!.includes('What stops a Layer 2 loop?'))
        expect(view.textContent).toContain('A bridge protocol data unit.')
        expect(calls.filter(c => c === MAKE)).toHaveLength(1)
        expect(!!button(view, 'Make new cards')).toBe(true)
    })

    it('a failed Make shows the AI error and stays retryable', async () => {
        serve({[CARDS]: [{status: 200, body: cardsResponse()}],
            [MAKE]: [{status: 502, body: {detail: "The AI's cards didn't check out, even after one repair."}}]})
        const view = await show(panel())
        await until(() => !!button(view, 'Make cards'))
        await act(async () => button(view, 'Make cards')!.click())
        await until(() => view.textContent!.includes("didn't check out"))
    })
})

describe('due badge', () => {
    it('renders nothing at zero', async () => {
        serve({[DUE]: [{status: 200, body: {count: 0, cards: []} satisfies Due}]})
        const view = await show(<DueBadge/>)
        await new Promise(resolve => setTimeout(resolve, 20))
        expect(view.querySelector('.nav-count')).toBeNull()
    })

    it('shows the due count', async () => {
        serve({[DUE]: [{status: 200, body: {count: 3, cards: []} satisfies Due}]})
        const view = await show(<DueBadge/>)
        await until(() => view.querySelector('.nav-count')?.textContent === '3')
    })
})

describe('review page', () => {
    it('empty: no due cards', async () => {
        serve({[DUE]: [{status: 200, body: {count: 0, cards: []} satisfies Due}]})
        const view = await show(<ReviewPage/>)
        await until(() => view.textContent!.includes('Nothing due right now'))
    })

    it('shows a due card, reveals the answer, and grading moves to the next one', async () => {
        const first = card({id: 'c1', question: 'Q1'})
        const second = card({id: 'c2', question: 'Q2'})
        const calls = serve({
            [DUE]: [{status: 200, body: {count: 2, cards: [first, second]} satisfies Due},
                    {status: 200, body: {count: 1, cards: [second]} satisfies Due}],
            'POST /companion/review/c1/grade': [{status: 200, body: {...first, interval: 1, due: 100}}],
        })
        const view = await show(<ReviewPage/>)
        await until(() => view.textContent!.includes('Q1'))
        expect(button(view, 'Again')).toBeUndefined()          // hidden until "Show answer"
        await act(async () => button(view, 'Show answer')!.click())
        expect(view.textContent).toContain('Spanning tree protocol blocks a redundant port.')
        await act(async () => button(view, 'Good')!.click())
        await until(() => view.textContent!.includes('Q2'))
        expect(calls).toContain('POST /companion/review/c1/grade')
    })
})

describe('replay what I forget', () => {
    const plan = (change: Partial<Plan> = {}): Plan => ({id: 'p1', status: 'ready', mode: 'keyword', want: 'Replay what I forget',
        skip: '', minutes: null, spans: [{id: 'r1', episode_id: 'public-1', start: 42, end: 50,
            text: 'Spanning tree protocol blocks a redundant port.', why: 'Card: What stops a Layer 2 loop?', enabled: true, title: 'An episode'}],
        kept_seconds: 8, source_seconds: 8, omitted: [], needs_timing: [], created_at: '2026-09-24T10:00:00+00:00', ...change})

    it('nothing to replay yet: shows the message', async () => {
        serve({'POST /companion/review/replay': [{status: 409, body: {detail: 'No cards need replay yet.'}}]})
        const view = await show(<Replay/>)
        await act(async () => button(view, 'Find what to replay')!.click())
        await until(() => view.textContent!.includes('No cards need replay yet.'))
    })

    it('finds a plan and offers Smart Play and an export, built from the cited spans alone', async () => {
        serve({'POST /companion/review/replay': [{status: 200, body: plan()}]})
        const view = await show(<Replay/>)
        await act(async () => button(view, 'Find what to replay')!.click())
        await until(() => !!button(view, 'Smart Play'))
        expect(view.textContent).toContain('1 passage(s)')
        expect(!!button(view, 'Export MP3')).toBe(true)
    })

    it('needs_timing episodes are reported, not silently dropped', async () => {
        serve({'POST /companion/review/replay': [{status: 200, body: plan({spans: [], status: 'needs_timing',
            needs_timing: [{episode_id: 'public-1', reason: "This transcript doesn't match your audio file."}]})}]})
        const view = await show(<Replay/>)
        await act(async () => button(view, 'Find what to replay')!.click())
        await until(() => view.textContent!.includes("isn't ready to cut yet"))
        expect(button(view, 'Smart Play')).toBeUndefined()
    })
})
