// The welcome home card: hidden once the library has a show, optional preferences, the AI setup's
// connected state, and saving/removing topics through companion/routes/profile.py.
import {afterEach, beforeAll, describe, expect, it, vi} from 'vitest'
import {act} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {MemoryRouter} from 'react-router-dom'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import i18n from '../../../language/i18n'
import en from './locales/en.json'

vi.mock('../../../utils/http', () => ({client: {GET: async () => ({data: []})}, $api: {}, apiURL: '', uiURL: ''}))
i18n.addResourceBundle('en', 'welcome', en, true, true)
const {WelcomeHome} = await import('./WelcomeHome')

const SHOWS = 'GET /api/v1/podcasts'
const AI = 'GET /companion/settings/ai'
const PROFILE = 'GET /companion/profile'
const SAVE_PROFILE = 'PUT /companion/profile'

type Reply = {status: number; body?: unknown}
let root: Root | undefined

beforeAll(() => {(globalThis as {IS_REACT_ACT_ENVIRONMENT?: boolean}).IS_REACT_ACT_ENVIRONMENT = true})
afterEach(() => {act(() => root?.unmount()); root = undefined; document.body.innerHTML = ''; vi.unstubAllGlobals()})

/** Answer each request from `routes` ("METHOD path" -> replies in order; the last one repeats). */
function serve(routes: Record<string, Reply[]>) {
    const calls: {key: string; body: Record<string, unknown> | null}[] = []
    vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
        const key = `${init?.method ?? 'GET'} ${path}`
        calls.push({key, body: typeof init?.body === 'string' ? JSON.parse(init.body) : null})
        const replies = routes[key]
        if (!replies) throw new Error('unexpected request ' + key)
        const reply = replies.length > 1 ? replies.shift()! : replies[0]!
        return new Response(reply.body === undefined ? null : JSON.stringify(reply.body), {status: reply.status})
    }))
    return calls
}

async function show() {
    const container = document.body.appendChild(document.createElement('div'))
    const client = new QueryClient({defaultOptions: {queries: {retry: false}}})
    root = createRoot(container)
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter><WelcomeHome/></MemoryRouter></QueryClientProvider>))
    return container
}

async function until(check: () => boolean) {
    for (let i = 0; i < 100 && !check(); i++) await act(() => new Promise(resolve => setTimeout(resolve, 10)))
    expect(check()).toBe(true)
}

async function type(input: HTMLInputElement, value: string) {
    await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
        input.dispatchEvent(new Event('input', {bubbles: true}))
    })
}
const enter = (input: HTMLInputElement) => act(async () => {
    input.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true, cancelable: true}))
})
const click = (element: Element) => act(async () => (element as HTMLElement).click())

describe('WelcomeHome', () => {
    it('renders nothing while the library already has a show', async () => {
        serve({[SHOWS]: [{status: 200, body: [{id: 'show-1', name: 'A show'}]}]})
        const view = await show()
        await new Promise(resolve => setTimeout(resolve, 20))
        expect(view.querySelector('.welcome-card')).toBeNull()
    })

    it('renders nothing while shows are still loading, and nothing on an error', async () => {
        serve({[SHOWS]: [{status: 500}]})
        const view = await show()
        await new Promise(resolve => setTimeout(resolve, 20))
        expect(view.querySelector('.welcome-card')).toBeNull()
    })

    it('shows one welcome action and optional preferences when the library is empty', async () => {
        serve({[SHOWS]: [{status: 200, body: []}], [AI]: [{status: 200, body: {configured: false, provider: 'groq'}}],
            [PROFILE]: [{status: 200, body: {topics: []}}]})
        const view = await show()
        await until(() => view.querySelector('.welcome-card') !== null)
        expect(view.textContent).toContain('Add a show')
        expect(view.textContent).toContain('Pick your topics')
        expect(view.querySelector('a[href="/discover"]')).not.toBeNull()
        await until(() => view.querySelector('a[href="/settings/ai"]') !== null)
    })

    it('hides the connect-AI link and shows the provider once AI is already configured', async () => {
        serve({[SHOWS]: [{status: 200, body: []}], [AI]: [{status: 200, body: {configured: true, provider: 'groq'}}],
            [PROFILE]: [{status: 200, body: {topics: []}}]})
        const view = await show()
        await until(() => view.textContent?.includes('Connected: Groq.') === true)
        expect(view.querySelector('a[href="/settings/ai"]')).toBeNull()
    })

    it('shows saved topics as chips', async () => {
        serve({[SHOWS]: [{status: 200, body: []}], [AI]: [{status: 200, body: {configured: false, provider: 'groq'}}],
            [PROFILE]: [{status: 200, body: {topics: ['Bash', 'Cooking']}}]})
        const view = await show()
        await until(() => view.querySelectorAll('.welcome-topic-chip').length === 2)
        expect(view.textContent).toContain('Bash')
        expect(view.textContent).toContain('Cooking')
    })

    it('adding a topic saves it, and removing it saves the shorter list', async () => {
        const calls = serve({[SHOWS]: [{status: 200, body: []}], [AI]: [{status: 200, body: {configured: false, provider: 'groq'}}],
            [PROFILE]: [{status: 200, body: {topics: []}}],
            [SAVE_PROFILE]: [{status: 200, body: {topics: ['Bash']}}, {status: 200, body: {topics: []}}]})
        const view = await show()
        await until(() => view.querySelector('.welcome-topics input') !== null)
        const input = view.querySelector('.welcome-topics input') as HTMLInputElement
        await type(input, 'Bash')
        await enter(input)
        await until(() => calls.some(c => c.key === SAVE_PROFILE))
        expect(calls.find(c => c.key === SAVE_PROFILE)?.body).toEqual({topics: ['Bash']})
        await until(() => view.querySelectorAll('.welcome-topic-chip').length === 1)

        await click(view.querySelector('.welcome-topic-chip button')!)
        await until(() => calls.filter(c => c.key === SAVE_PROFILE).length === 2)
        expect(calls.filter(c => c.key === SAVE_PROFILE)[1]?.body).toEqual({topics: []})
    })

    it('a blank or duplicate entry does not call save', async () => {
        const calls = serve({[SHOWS]: [{status: 200, body: []}], [AI]: [{status: 200, body: {configured: false, provider: 'groq'}}],
            [PROFILE]: [{status: 200, body: {topics: ['Bash']}}]})
        const view = await show()
        await until(() => view.querySelector('.welcome-topics input') !== null)
        const input = view.querySelector('.welcome-topics input') as HTMLInputElement
        await enter(input)                 // blank
        await type(input, 'bash')          // same word, different case
        await enter(input)
        await new Promise(resolve => setTimeout(resolve, 20))
        expect(calls.some(c => c.key === SAVE_PROFILE)).toBe(false)
    })
})
