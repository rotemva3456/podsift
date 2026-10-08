// Highlights and recap: saving a highlight from the player (click and the `h`
// key, ignored while typing), the episode Recap tab's states, the Listen now card, and the
// weekly Markdown export.
import {afterEach, beforeAll, describe, expect, it, vi} from 'vitest'
import {act, type ReactNode} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {MemoryRouter} from 'react-router-dom'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import i18n from '../../../language/i18n'
import en from './locales/en.json'
import shared from '../../shared/locales/en.json'
import type {PodcastEpisode} from '../../types'
import type {EpisodeRecap, WeekRecap} from './api'

i18n.addResourceBundle('en', 'recap', en, true, true)
i18n.addResourceBundle('en', 'shared', shared, true, true)
const {SaveHighlight, quoteFor} = await import('./SaveHighlight')
const {RecapTab} = await import('./RecapTab')
const {HomeRecapCard} = await import('./HomeRecapCard')
const {toMarkdown, WeekRecapPage} = await import('./WeekRecapPage')

type Reply = {status: number; body?: unknown}
type Call = {key: string; body: unknown}
let root: Root | undefined

beforeAll(() => {(globalThis as {IS_REACT_ACT_ENVIRONMENT?: boolean}).IS_REACT_ACT_ENVIRONMENT = true})
afterEach(() => {act(() => root?.unmount()); root = undefined; document.body.innerHTML = ''; vi.unstubAllGlobals()})

/** Answers each request from `routes` ("METHOD path" -> replies in order; the last one repeats),
 * and records the parsed body of every call. */
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
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>))
    return container
}

async function until(check: () => boolean) {
    for (let i = 0; i < 200 && !check(); i++) await act(() => new Promise(resolve => setTimeout(resolve, 10)))
    expect(check()).toBe(true)
}

const button = (view: HTMLElement, name: string) => [...view.querySelectorAll('button')].find(b => b.textContent?.includes(name) || b.getAttribute('aria-label')?.includes(name))
const notePost = (calls: Call[]) => calls.find(call => call.key === 'POST /companion/notes')?.body as Record<string, unknown> | undefined

const episode = {id: 'internal-1', episode_id: 'public-1', podcast_id: 'show-1', name: 'An episode',
    status: true, total_time: 3120} as unknown as PodcastEpisode
const TRANSCRIPT = 'GET /companion/episodes/public-1/transcript'
const transcript = (segments: {id: string; start: number; end: number; text: string}[] = []) =>
    ({episode_id: 'public-1', source: 'x', timed: segments.length > 0, text: '', segments})
const SEGMENTS = [{id: 's1', start: 0, end: 20, text: 'Subnetting splits a network.'},
    {id: 's2', start: 20, end: 50, text: 'A prefix length sets the subnet size.'}]

describe('quoteFor', () => {
    it('joins every segment that touches the range', () => {
        expect(quoteFor(SEGMENTS, 10, 50)).toBe('Subnetting splits a network. A prefix length sets the subnet size.')
        expect(quoteFor(SEGMENTS, 25, 40)).toBe('A prefix length sets the subnet size.')
        expect(quoteFor([], 0, 30)).toBe('')
    })
})

describe('save highlight (player action)', () => {
    it('is disabled with no timed transcript', async () => {
        serve({[TRANSCRIPT]: [{status: 200, body: transcript([])}]})
        const view = await show(<SaveHighlight episodeId="public-1" position={40}/>)
        await until(() => button(view, 'Save the last')?.hasAttribute('disabled') === true)
    })

    it('saves the last 30 seconds as a highlight with the transcript quote, on click', async () => {
        const calls = serve({
            [TRANSCRIPT]: [{status: 200, body: transcript(SEGMENTS)}],
            'POST /companion/notes': [{status: 201, body: {id: 'h1', episode_id: 'public-1', title: 'x',
                position: 10, text: 'q', created_at: 'now', kind: 'highlight', start: 10, end: 40, quote: 'q'}}],
        })
        const view = await show(<SaveHighlight episodeId="public-1" position={40}/>)
        await until(() => button(view, 'Save the last')?.hasAttribute('disabled') === false)
        await act(async () => button(view, 'Save the last')!.click())
        await until(() => !!notePost(calls))
        const saved = notePost(calls)!
        expect(saved).toMatchObject({episode_id: 'public-1', kind: 'highlight', position: 10, start: 10, end: 40,
            quote: 'Subnetting splits a network. A prefix length sets the subnet size.'})
    })

    it('saves on the h key, but not while typing in an input', async () => {
        const calls = serve({
            [TRANSCRIPT]: [{status: 200, body: transcript(SEGMENTS)}],
            'POST /companion/notes': [{status: 201, body: {id: 'h2', episode_id: 'public-1', title: 'x',
                position: 20, text: 'q', created_at: 'now', kind: 'highlight', start: 20, end: 50, quote: 'q'}}],
        })
        const view = await show(<><input aria-label="somewhere else"/><SaveHighlight episodeId="public-1" position={50}/></>)
        await until(() => button(view, 'Save the last')?.hasAttribute('disabled') === false)
        const input = view.querySelector('input')!
        input.focus()
        await act(async () => input.dispatchEvent(new KeyboardEvent('keydown', {key: 'h', bubbles: true})))
        expect(notePost(calls)).toBeUndefined()
        input.blur()
        await act(async () => document.dispatchEvent(new KeyboardEvent('keydown', {key: 'h', bubbles: true})))
        await until(() => !!notePost(calls))
        expect(notePost(calls)).toMatchObject({start: 20, end: 50})
    })
})

describe('recap tab', () => {
    const RECAP = 'GET /companion/episodes/public-1/recap'
    const base: EpisodeRecap = {episode_id: 'public-1', heard: {heard: false, position: null, total: null, percent: null, at: null},
        key_ideas: [], highlights: [], notes: [], learned: {status: 'no_transcript', text: null, citations: [], model: null, created_at: null, error: null}}

    it('shows the empty state when there is nothing to recap yet', async () => {
        serve({[RECAP]: [{status: 200, body: base}]})
        const view = await show(<RecapTab episodeId="public-1" episode={episode} position={0} seek={vi.fn()}/>)
        await until(() => view.textContent!.includes('Nothing to recap yet'))
    })

    it('shows heard status, key ideas, highlights and notes, and plays from a citation', async () => {
        const seek = vi.fn()
        serve({[RECAP]: [{status: 200, body: {...base,
            heard: {heard: true, position: 190, total: 200, percent: 95, at: 'now'},
            key_ideas: [{text: 'BGP scales with route reflectors.', citations: [{segment_id: 's3', start: 80, end: 90}]}],
            highlights: [{id: 'h1', episode_id: 'public-1', title: 'x', position: 10, text: 'q', created_at: 'now',
                kind: 'highlight', start: 10, end: 40, quote: 'A quoted passage.'}],
            notes: [{id: 'n1', episode_id: 'public-1', title: 'x', position: 5, text: 'My own note', created_at: 'now',
                kind: 'note', start: null, end: null, quote: null}],
            learned: {status: 'no_ai', text: null, citations: [], model: null, created_at: null, error: null}}}]})
        const view = await show(<RecapTab episodeId="public-1" episode={episode} position={0} seek={seek}/>)
        await until(() => view.textContent!.includes('BGP scales with route reflectors.'))
        expect(view.textContent).toContain('A quoted passage.')
        expect(view.textContent).toContain('My own note')
        expect(view.textContent).toContain("Connect AI")
        await act(async () => button(view, '1:20')!.click())
        expect(seek).toHaveBeenCalledWith(80)
    })

    it('offers "What did I learn?" and shows the paragraph once made', async () => {
        serve({
            [RECAP]: [{status: 200, body: {...base, learned: {status: 'not_generated', text: null, citations: [],
                model: null, created_at: null, error: null}}}],
            'POST /companion/episodes/public-1/recap/learned': [{status: 200, body: {status: 'ready',
                text: 'You heard how subnetting works.', citations: [{segment_id: 's1', start: 0, end: 20}],
                model: 'm', created_at: 'now', error: null}}],
        })
        const view = await show(<RecapTab episodeId="public-1" episode={episode} position={0} seek={vi.fn()}/>)
        await until(() => !!button(view, 'What did I learn?'))
        await act(async () => button(view, 'What did I learn?')!.click())
        await until(() => view.textContent!.includes('You heard how subnetting works.'))
    })
})

describe('home recap card', () => {
    const WEEK = 'GET /companion/recap/week'
    const week = (change: Partial<WeekRecap> = {}): WeekRecap => ({since: 's', until: 'u', minutes: 30,
        episodes: [], key_ideas: [], highlights: [], ...change})

    it('renders nothing when there is nothing to show', async () => {
        serve({[WEEK]: [{status: 200, body: week()}]})
        const view = await show(<HomeRecapCard/>)
        await new Promise(resolve => setTimeout(resolve, 20))
        expect(view.textContent).toBe('')
    })

    it('links to /recap with a count of episodes and highlights', async () => {
        serve({[WEEK]: [{status: 200, body: week({episodes: [{episode_id: 'e1', title: 't', podcast_name: 'p',
            heard_seconds: 100, total_seconds: 200, at: 'now'}], highlights: [{id: 'h1', episode_id: 'e1', title: 't',
            position: 1, text: 'q', created_at: 'now', kind: 'highlight', start: 1, end: 2, quote: 'q'}]})}]})
        const view = await show(<HomeRecapCard/>)
        await until(() => !!view.querySelector('a'))
        expect(view.querySelector('a')?.getAttribute('href')).toBe('/recap')
        expect(view.textContent).toContain('1 episode')
        expect(view.textContent).toContain('1 highlight')
    })
})

describe('toMarkdown', () => {
    it('renders episodes, key ideas and highlights as plain Markdown', () => {
        const md = toMarkdown({since: 's', until: 'u', minutes: 42,
            episodes: [{episode_id: 'e1', title: 'BGP basics', podcast_name: 'Net show', heard_seconds: 100, total_seconds: 200, at: 'now'}],
            key_ideas: [{text: 'Route reflectors remove the full mesh.', citations: [], episode_id: 'e1', title: 'BGP basics'}],
            highlights: [{id: 'h1', episode_id: 'e1', title: 'BGP basics', position: 10, text: 'q', created_at: 'now',
                kind: 'highlight', start: 10, end: 40, quote: 'A quoted passage.'}]})
        expect(md).toContain('# This week')
        expect(md).toContain('1 episode · 42 min · 1 highlight')
        expect(md).toContain('- BGP basics (Net show)')
        expect(md).toContain('- Route reflectors remove the full mesh. — *BGP basics*')
        expect(md).toContain('- "A quoted passage."')
    })
})

describe('week recap page', () => {
    it('shows the empty state when there is nothing at all', async () => {
        serve({'GET /companion/recap/week': [{status: 200, body: {since: 's', until: 'u', minutes: 0, episodes: [], key_ideas: [], highlights: []}}]})
        const view = await show(<WeekRecapPage/>)
        await until(() => view.textContent!.includes('Nothing to recap yet'))
    })

    it('shows "0 min", not "Duration unavailable", when nothing has finished yet but a highlight exists', async () => {
        serve({'GET /companion/recap/week': [{status: 200, body: {since: 's', until: 'u', minutes: 0, episodes: [],
            key_ideas: [], highlights: [{id: 'h1', episode_id: 'e1', title: 't', position: 1, text: 'q',
                created_at: 'now', kind: 'highlight', start: 1, end: 2, quote: 'q'}]}}]})
        const view = await show(<WeekRecapPage/>)
        await until(() => view.textContent!.includes('0 episodes'))
        expect(view.textContent).toContain('0 min')
        expect(view.textContent).not.toContain('Duration unavailable')
    })
})
