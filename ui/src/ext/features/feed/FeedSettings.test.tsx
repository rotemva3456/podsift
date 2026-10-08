// Settings → Podcast app feeds: create a link, copy it, pick a show for its own link.
import {afterEach, beforeAll, describe, expect, it, vi} from 'vitest'
import {act, type ReactElement} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {MemoryRouter} from 'react-router-dom'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import i18n from '../../../language/i18n'
import en from './locales/en.json'
import type {FeedLinks, FeedStatus} from './api'

i18n.addResourceBundle('en', 'feed', en, true, true)
const {FeedSettings} = await import('./FeedSettings')

const LINKS: FeedLinks = {
    token: 'test-token-abc',
    shows_url_template: 'http://host/companion/feed/test-token-abc/shows/{podcast_id}.xml',
    cuts_url: 'http://host/companion/feed/test-token-abc/cuts.xml',
}
const SHOWS = [{id: 'show-1', name: 'Certified CompTIA Network+'}, {id: 'show-2', name: 'Another show'}]

type Reply = {status: number; body?: unknown}
let root: Root | undefined
beforeAll(() => {(globalThis as {IS_REACT_ACT_ENVIRONMENT?: boolean}).IS_REACT_ACT_ENVIRONMENT = true})
afterEach(() => {act(() => root?.unmount()); root = undefined; document.body.innerHTML = ''; vi.unstubAllGlobals()})

/** Answer "METHOD path" from `routes` (replies in order; the last repeats). */
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

async function show(element: ReactElement) {
    const container = document.body.appendChild(document.createElement('div'))
    const client = new QueryClient({defaultOptions: {queries: {retry: false}}})
    root = createRoot(container)
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter>{element}</MemoryRouter></QueryClientProvider>))
    return container
}

async function until(check: () => boolean) {
    for (let i = 0; i < 100 && !check(); i++) await act(() => new Promise(resolve => setTimeout(resolve, 10)))
    expect(check()).toBe(true)
}

const button = (view: HTMLElement, name: string) => [...view.querySelectorAll('button')].find(b => b.textContent?.includes(name))!
const click = (element: HTMLElement) => act(async () => element.click())

describe('Settings → Podcast app feeds', () => {
    it('not configured yet: "Create feed link" makes a token and shows the cuts link and a show picker', async () => {
        const calls = serve({
            'GET /companion/settings/feed': [{status: 200, body: {configured: false, created_at: null} satisfies FeedStatus}],
            'POST /companion/settings/feed/token': [{status: 200, body: LINKS}],
            'GET /api/v1/podcasts': [{status: 200, body: SHOWS}],
        })
        const view = await show(<FeedSettings/>)
        await until(() => !!button(view, 'Create feed link'))
        await click(button(view, 'Create feed link'))
        await until(() => !!view.querySelector('.feed-link'))
        expect(calls).toContain('POST /companion/settings/feed/token')
        const link = view.querySelector<HTMLInputElement>('.feed-link')!
        expect(link.value).toBe(LINKS.cuts_url)
        expect(view.querySelector('.feed-qr svg')).toBeTruthy()

        // Picking a show reveals its own feed link, built from the template.
        await until(() => !!view.querySelector('option[value="show-2"]'))
        const select = view.querySelector('select')!
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(select, 'show-2')
            select.dispatchEvent(new Event('change', {bubbles: true}))
        })
        await until(() => view.querySelectorAll('.feed-link').length === 2)
        const links = [...view.querySelectorAll<HTMLInputElement>('.feed-link')].map(el => el.value)
        expect(links).toContain('http://host/companion/feed/test-token-abc/shows/show-2.xml')
    })

    it('already configured: shows the "can\'t show it again" hint and a "New link" button, never the old token', async () => {
        serve({'GET /companion/settings/feed': [{status: 200, body: {configured: true, created_at: '2026-09-24T00:00:00Z'} satisfies FeedStatus}]})
        const view = await show(<FeedSettings/>)
        await until(() => !!button(view, 'New link'))
        expect(view.textContent).toContain('already made a feed link')
        expect(view.querySelector('.feed-link')).toBeNull()
    })

    it('copy writes the link to the clipboard', async () => {
        serve({
            'GET /companion/settings/feed': [{status: 200, body: {configured: false, created_at: null} satisfies FeedStatus}],
            'POST /companion/settings/feed/token': [{status: 200, body: LINKS}],
            'GET /api/v1/podcasts': [{status: 200, body: []}],
        })
        const written: string[] = []
        Object.assign(navigator, {clipboard: {writeText: async (text: string) => {written.push(text)}}})
        const view = await show(<FeedSettings/>)
        await until(() => !!button(view, 'Create feed link'))
        await click(button(view, 'Create feed link'))
        await until(() => !!button(view, 'Copy'))
        await click(button(view, 'Copy'))
        await until(() => written.length === 1)
        expect(written[0]).toBe(LINKS.cuts_url)
    })

    it('a load error offers a retry', async () => {
        const calls = serve({'GET /companion/settings/feed': [{status: 503}, {status: 200, body: {configured: false, created_at: null} satisfies FeedStatus}]})
        const view = await show(<FeedSettings/>)
        await until(() => !!button(view, 'Try again'))
        await click(button(view, 'Try again'))
        await until(() => calls.filter(c => c === 'GET /companion/settings/feed').length === 2)
        await until(() => !!button(view, 'Create feed link'))
    })
})
