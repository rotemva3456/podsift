// Episode brief UI: every state of the header panel, "play from here", and row badges batched into one request.
import {afterEach, beforeAll, describe, expect, it, vi} from 'vitest'
import {act, type ReactNode} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {MemoryRouter} from 'react-router-dom'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import i18n from '../../../language/i18n'
import en from './locales/en.json'
import shared from '../../shared/locales/en.json'
import type {PodcastEpisode} from '../../types'
import type {Brief} from './api'

vi.mock('../../../utils/http', () => ({client: {GET: async () => ({data: []})}, $api: {}, apiURL: '', uiURL: ''}))
i18n.addResourceBundle('en', 'brief', en, true, true)
i18n.addResourceBundle('en', 'shared', shared, true, true)
const {BriefPanel, POLL_MS} = await import('./BriefPanel')
const {BriefBadge} = await import('./Verdict')

const episode = {id: 'internal-1', episode_id: 'public-1', podcast_id: 'show-1', name: 'An episode', status: true,
    total_time: 3120} as unknown as PodcastEpisode
const BRIEF = 'GET /companion/episodes/public-1/brief'
const MAKE = 'POST /companion/episodes/public-1/brief'
const brief = (change: Partial<Brief> = {}): Brief => ({episode_id: 'public-1', status: 'not_generated', summary: null,
    verdict: null, verdict_reason: null, who_for: null, chapters: [], chapters_source: null, key_ideas: [], percent_new: 58,
    new_concepts: ['bgp', 'route reflector'], heard_count: 2, ads_seconds: 30, duration: 3120, model: null, created_at: null,
    error: null, ai_ready: true, estimated_tokens: 13400, ...change})
const ready = brief({status: 'ready', summary: 'Loop prevention and BGP scaling.', verdict: 'HEAR',
    verdict_reason: 'The design reasoning is best heard.', who_for: 'Network engineers.', chapters_source: 'ai',
    chapters: [{title: 'Loops', start: 0}, {title: 'Route reflectors', start: 754}], model: 'llama-3.3-70b',
    key_ideas: [{text: 'Reflectors remove the iBGP full mesh.', citations: [{segment_id: 's9', start: 812, end: 820}]}],
    created_at: '2026-09-24T10:00:00+00:00', estimated_tokens: null})

type Reply = {status: number; body?: unknown}
let root: Root | undefined

beforeAll(() => {(globalThis as {IS_REACT_ACT_ENVIRONMENT?: boolean}).IS_REACT_ACT_ENVIRONMENT = true})
afterEach(() => {act(() => root?.unmount()); root = undefined; document.body.innerHTML = ''; vi.useRealTimers(); vi.unstubAllGlobals()})

/** Answer each request from `routes` ("METHOD path" → replies in order; the last one repeats). */
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
const panel = (seek = vi.fn()) => <BriefPanel episodeId="public-1" episode={episode} position={0} seek={seek}/>

describe('brief panel', () => {
    it('without AI: shows the facts, points to Settings → AI, and offers no Generate button', async () => {
        const calls = serve({[BRIEF]: [{status: 200, body: brief({ai_ready: false, estimated_tokens: null})}]})
        const view = await show(panel())
        await until(() => view.textContent!.includes('58% new to you'))
        expect(view.textContent).toContain('compared with 2 episodes of this show you finished')
        expect(view.textContent).toContain('0:30 of sponsor reads')
        expect(view.textContent).toContain('52 min')
        expect(view.textContent).toContain('New to you: bgp, route reflector')
        expect(view.querySelector('a[href="/settings/ai"]')?.textContent).toBe('Settings → AI')
        expect(button(view, 'Generate brief')).toBeUndefined()
        expect(calls).toEqual([BRIEF])
    })

    it('Generate: one POST, polls while it is made, then shows the brief; chapters and ideas play from their time', async () => {
        const calls = serve({[BRIEF]: [{status: 200, body: brief()}, {status: 200, body: brief({status: 'generating'})},
            {status: 200, body: ready}], [MAKE]: [{status: 202, body: brief({status: 'generating'})}]})
        const seek = vi.fn()
        const view = await show(panel(seek))
        await until(() => !!button(view, 'Generate brief'))
        expect(view.textContent).toContain('Sends about 13,400 tokens')
        vi.useFakeTimers({toFake: ['setTimeout', 'setInterval', 'clearTimeout', 'clearInterval']})
        await act(async () => button(view, 'Generate brief')!.click())
        await act(async () => {await vi.advanceTimersByTimeAsync(10)})
        expect(view.querySelector('[role=status]')?.textContent).toContain('Making the brief…')
        await act(async () => {await vi.advanceTimersByTimeAsync(2 * POLL_MS + 10)})
        expect(view.textContent).toContain('The design reasoning is best heard.')
        expect(view.querySelector('.brief-chip')?.textContent).toBe('Verdict: Hear')
        expect(view.textContent).toContain('Loop prevention and BGP scaling.')
        expect(view.textContent).toContain('Made with llama-3.3-70b')
        expect(calls.filter(call => call === MAKE)).toHaveLength(1)
        const polls = calls.filter(call => call === BRIEF).length
        await act(async () => {await vi.advanceTimersByTimeAsync(POLL_MS * 3)})
        expect(calls.filter(call => call === BRIEF)).toHaveLength(polls)    // ready: polling stopped
        await act(async () => view.querySelector<HTMLButtonElement>('button[aria-label="Play from 12:34: Route reflectors"]')!.click())
        expect(seek).toHaveBeenLastCalledWith(754)
        await act(async () => view.querySelector<HTMLButtonElement>('button[aria-label^="Play from 13:32"]')!.click())
        expect(seek).toHaveBeenLastCalledWith(812)
    })

    it('a failed poll while the brief is made keeps it on screen, and polling goes on', async () => {
        const calls = serve({[BRIEF]: [{status: 200, body: brief({status: 'generating'})},
            {status: 503, body: {detail: 'Down.'}}, {status: 200, body: ready}]})
        vi.useFakeTimers({toFake: ['setTimeout', 'setInterval', 'clearTimeout', 'clearInterval']})
        const view = await show(panel())
        await act(async () => {await vi.advanceTimersByTimeAsync(10)})
        expect(view.textContent).toContain('Making the brief…')
        await act(async () => {await vi.advanceTimersByTimeAsync(POLL_MS + 10)})
        expect(calls.filter(call => call === BRIEF)).toHaveLength(2)          // the poll that failed
        expect(view.textContent).toContain('Making the brief…')
        expect(view.textContent).not.toContain("The brief couldn't load.")
        await act(async () => {await vi.advanceTimersByTimeAsync(POLL_MS + 10)})
        expect(view.textContent).toContain('Loop prevention and BGP scaling.')
    })

    it('failed: shows why and tries again', async () => {
        const calls = serve({[BRIEF]: [{status: 200, body: brief({status: 'failed', error: 'The key was rejected.'})}],
            [MAKE]: [{status: 202, body: brief({status: 'generating'})}]})
        const view = await show(panel())
        await until(() => view.textContent!.includes("The brief couldn't be made. The key was rejected."))
        await act(async () => button(view, 'Try again')!.click())
        await until(() => calls.includes(MAKE))
    })

    it('no transcript: offers the shared "Make a transcript" button', async () => {
        serve({[BRIEF]: [{status: 200, body: brief({status: 'no_transcript', percent_new: null, heard_count: null,
            ads_seconds: null, new_concepts: []})}], 'GET /api/v1/podcasts/episodes/internal-1/transcripts': [{status: 200, body: []}]})
        const view = await show(panel())
        await until(() => !!button(view, 'Make a transcript'))
        expect(view.textContent).toContain('A brief needs a transcript of this episode.')
        expect(button(view, 'Generate brief')).toBeUndefined()
    })

    it('a brief that could not load offers a retry', async () => {
        serve({[BRIEF]: [{status: 503, body: {detail: 'The podcast library is unavailable. Please try again.'}}, {status: 200, body: ready}]})
        const view = await show(panel())
        await until(() => view.textContent!.includes("The brief couldn't load."))
        await act(async () => button(view, 'Try again')!.click())
        await until(() => view.textContent!.includes('Loop prevention and BGP scaling.'))
    })
})

describe('row badges', () => {
    it('ask for every row in one request and never make a brief', async () => {
        const calls = serve({'GET /companion/briefs?ids=public-1,public-2,public-3': [{status: 200,
            body: [ready, {...ready, episode_id: 'public-3', verdict: 'SKIP'}]}]})
        const view = await show(<>{['public-1', 'public-2', 'public-3'].map(id => <p key={id} id={id}><BriefBadge episodeId={id}/></p>)}</>)
        await until(() => view.querySelectorAll('.brief-chip').length === 2)
        expect(view.querySelector('#public-1')?.textContent).toBe('Verdict: Hear')
        expect(view.querySelector('#public-2')?.textContent).toBe('')
        expect(view.querySelector('#public-3')?.textContent).toBe('Verdict: Skip')
        expect(calls).toEqual(['GET /companion/briefs?ids=public-1,public-2,public-3'])
    })
})
