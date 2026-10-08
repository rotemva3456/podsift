// Worth hearing UI: the nav/home counters, the page's HEAR/other split, and the settings form.
import {afterEach, beforeAll, describe, expect, it, vi} from 'vitest'
import {act, type ReactNode} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {MemoryRouter} from 'react-router-dom'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import i18n from '../../../language/i18n'
import en from './locales/en.json'
import shared from '../../shared/locales/en.json'
import briefEn from '../brief/locales/en.json'
import type {WorthList, WorthSettings as Saved} from './api'

vi.mock('../../../utils/http', () => ({client: {GET: async () => ({data: []})}, $api: {}, apiURL: '', uiURL: ''}))
i18n.addResourceBundle('en', 'worth', en, true, true)
i18n.addResourceBundle('en', 'shared', shared, true, true)
i18n.addResourceBundle('en', 'brief', briefEn, true, true)
const {WorthBadge} = await import('./WorthBadge')
const {WorthHome} = await import('./WorthHome')
const {WorthHearingPage} = await import('./WorthHearingPage')
const {WorthSettings} = await import('./WorthSettings')

const WORTH = 'GET /companion/worth-hearing'
const SETTINGS = 'GET /companion/settings/worth-hearing'
const SAVE = 'PUT /companion/settings/worth-hearing'
const ESTIMATE = 'POST /companion/settings/worth-hearing/estimate'
const SHOWS = 'GET /api/v1/podcasts'

const item = (change: Partial<WorthList['hear'][number]> = {}) => ({episode_id: 'ep-1', title: 'An episode',
    verdict: 'HEAR' as const, summary: 'A short summary.', verdict_reason: 'Worth it.', percent_new: 40,
    duration: 1200, briefed_on: '2026-09-21', ...change})
const settings = (change: Partial<Saved> = {}): Saved => ({enabled: false, show_ids: [], daily_cap: 5, updated_at: null,
    login_blocks_auto: false, ...change})

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

function setNumber(input: HTMLInputElement, value: string) {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!
    setter.call(input, value)
    input.dispatchEvent(new Event('input', {bubbles: true}))
}

const button = (view: HTMLElement, name: string) => [...view.querySelectorAll('button')].find(b => b.textContent?.includes(name))

describe('WorthBadge', () => {
    it('renders nothing when there is nothing to hear', async () => {
        serve({[WORTH]: [{status: 200, body: {hear: [], other: []}}]})
        const view = await show(<WorthBadge/>)
        await until(() => view.querySelector('.nav-count') === null && document.querySelectorAll('*').length > 0)
        expect(view.querySelector('.nav-count')).toBeNull()
    })

    it('shows the HEAR count', async () => {
        serve({[WORTH]: [{status: 200, body: {hear: [item(), item({episode_id: 'ep-2'})], other: []}}]})
        const view = await show(<WorthBadge/>)
        await until(() => view.querySelector('.nav-count')?.textContent === '2')
    })
})

describe('WorthHome', () => {
    it('renders nothing while there is nothing new', async () => {
        serve({[WORTH]: [{status: 200, body: {hear: [], other: []}}]})
        const view = await show(<WorthHome/>)
        await new Promise(resolve => setTimeout(resolve, 20))
        expect(view.querySelector('.worth-home-card')).toBeNull()
    })

    it('links to the page with a count', async () => {
        serve({[WORTH]: [{status: 200, body: {hear: [item()], other: []}}]})
        const view = await show(<WorthHome/>)
        await until(() => view.querySelector('.worth-home-card') !== null)
        expect(view.querySelector('.worth-home-card')?.textContent).toContain('1 new episode')
    })
})

describe('WorthHearingPage', () => {
    it('shows an error with retry when the page fails to load', async () => {
        serve({[WORTH]: [{status: 500}]})
        const view = await show(<WorthHearingPage/>)
        await until(() => view.textContent?.includes("couldn't load") === true)
    })

    it('shows the empty state with a link to settings', async () => {
        serve({[WORTH]: [{status: 200, body: {hear: [], other: [], login_blocks_auto: false}}]})
        const view = await show(<WorthHearingPage/>)
        await until(() => view.textContent?.includes('Nothing briefed yet') === true)
        expect(view.querySelector('a[href="/settings/worth-hearing"]')).not.toBeNull()
    })

    it('explains why instead of linking to settings when PodFetch needs a login', async () => {
        serve({[WORTH]: [{status: 200, body: {hear: [], other: [], login_blocks_auto: true}}]})
        const view = await show(<WorthHearingPage/>)
        await until(() => view.textContent?.includes("can't sign in by itself") === true)
        expect(view.querySelector('a[href="/settings/worth-hearing"]')).toBeNull()
    })

    it('shows HEAR expanded and collapses everything else', async () => {
        serve({[WORTH]: [{status: 200, body: {hear: [item({title: 'Hear me'})],
            other: [item({episode_id: 'ep-2', verdict: 'SKIP', title: 'Skip me'})]}}]})
        const view = await show(<WorthHearingPage/>)
        await until(() => view.textContent?.includes('Hear me') === true)
        expect(view.querySelector('.worth-collapsed summary')?.textContent).toContain('1 more checked')
        const details = view.querySelector('details') as HTMLDetailsElement
        expect(details.open).toBe(false)   // collapsed: "Skip me" is in the DOM but not shown until opened
        expect(view.textContent).toContain('Skip me')
        details.open = true
    })
})

describe('WorthSettings', () => {
    it('shows an error with retry when settings fail to load', async () => {
        serve({[SETTINGS]: [{status: 500}]})
        const view = await show(<WorthSettings/>)
        await until(() => button(view, 'Try again') !== undefined)
    })

    it('off by default: no show list, Save disabled', async () => {
        serve({[SETTINGS]: [{status: 200, body: settings()}], [SHOWS]: [{status: 200, body: []}]})
        const view = await show(<WorthSettings/>)
        await until(() => view.querySelector('.worth-toggle') !== null)
        expect(view.querySelector('.worth-show-list')).toBeNull()
        expect(button(view, 'Save')?.hasAttribute('disabled')).toBe(true)
    })

    it('disables the switch and explains why when PodFetch needs a login', async () => {
        serve({[SETTINGS]: [{status: 200, body: settings({enabled: true, show_ids: ['show-1'], login_blocks_auto: true})}],
            [SHOWS]: [{status: 200, body: [{id: 'show-1', name: 'A show'}]}]})
        const view = await show(<WorthSettings/>)
        await until(() => view.textContent?.includes("can't sign in by itself") === true)
        const toggle = view.querySelector('.worth-toggle [data-slot="checkbox"]')
        expect(toggle?.getAttribute('data-disabled')).not.toBeNull()
        expect(view.querySelector('.worth-show-list')).toBeNull()   // nothing to configure while it can't run
        expect(button(view, 'Save')).toBeUndefined()
    })

    it('computes the daily estimate on its own as the cap changes, and keeps it visible through saving', async () => {
        const calls = serve({
            [SETTINGS]: [{status: 200, body: settings({enabled: true, show_ids: ['show-1'], daily_cap: 5})}],
            [SHOWS]: [{status: 200, body: [{id: 'show-1', name: 'A show'}]}],
            [ESTIMATE]: [{status: 200, body: {count: 2, input_chars: 8000, input_tokens: 12000, model: 'llama'}},
                        {status: 200, body: {count: 3, input_chars: 15000, input_tokens: 20000, model: 'llama'}}],
            [SAVE]: [{status: 200, body: settings({enabled: true, show_ids: ['show-1'], daily_cap: 9})}],
        })
        const view = await show(<WorthSettings/>)
        // No click needed: the estimate for the settings as loaded appears on its own.
        await until(() => view.textContent?.includes('About 12,000 input tokens a day') === true)
        expect(calls.filter(c => c === ESTIMATE).length).toBe(1)
        expect(button(view, 'Save')?.hasAttribute('disabled')).toBe(true)   // nothing changed yet

        const cap = view.querySelector('input[type="number"]') as HTMLInputElement
        await act(async () => setNumber(cap, '9'))
        expect(button(view, 'Save')?.hasAttribute('disabled')).toBe(false)
        // Debounced: it recomputes for the new cap without another click, replacing the old number.
        await until(() => view.textContent?.includes('About 20,000 input tokens a day') === true)
        expect(view.textContent).not.toContain('About 12,000 input tokens a day')

        await act(async () => button(view, 'Save')!.click())
        await until(() => view.textContent?.includes('Saved.') === true)
        expect(view.textContent).toContain('About 20,000 input tokens a day')   // stays visible through and after saving
        expect(calls).toContain(SAVE)
        const saveCall = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls
            .find((call: unknown[]) => call[0] === '/companion/settings/worth-hearing' && (call[1] as RequestInit)?.method === 'PUT')
        const sent = JSON.parse((saveCall![1] as RequestInit).body as string)
        expect(sent).toEqual({enabled: true, show_ids: ['show-1'], daily_cap: 9})
    })

    it('rejects an out-of-range cap without calling the server or showing a stale estimate', async () => {
        serve({
            [SETTINGS]: [{status: 200, body: settings({enabled: true, show_ids: ['show-1'], daily_cap: 5})}],
            [SHOWS]: [{status: 200, body: [{id: 'show-1', name: 'A show'}]}],
            [ESTIMATE]: [{status: 200, body: {count: 2, input_chars: 8000, input_tokens: 12000, model: 'llama'}}],
        })
        const view = await show(<WorthSettings/>)
        await until(() => view.textContent?.includes('About 12,000 input tokens a day') === true)
        const cap = view.querySelector('input[type="number"]') as HTMLInputElement
        await act(async () => setNumber(cap, '0'))
        await until(() => view.textContent?.includes('must be a whole number') === true)
        expect(button(view, 'Save')?.hasAttribute('disabled')).toBe(true)
        // Once the debounce settles on the invalid cap, the (now stale) estimate goes away too.
        await until(() => view.textContent?.includes('input tokens a day') === false)
    })
})
