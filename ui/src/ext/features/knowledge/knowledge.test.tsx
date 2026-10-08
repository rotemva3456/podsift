import {act} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {afterEach, beforeAll, describe, expect, it, vi} from 'vitest'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import {MemoryRouter} from 'react-router-dom'
import i18n from '../../../language/i18n'
import en from './locales/en.json'

vi.mock('../../../utils/http', () => ({client: {}, $api: {}, apiURL: '', uiURL: ''}))
i18n.addResourceBundle('en', 'knowledge', en, true, true)
const {KnowledgePage} = await import('./KnowledgePage')
let root: Root | undefined

const records = [
    {id: 'idea', episode_id: 'public/one', title: '<b>Routing stories</b>', position: 42, start: 40, end: 55,
        text: 'Check the return path first.', quote: 'A broken route can be a one-way problem.', kind: 'highlight', created_at: '2026-10-04T01:00:00Z'},
    {id: 'note', episode_id: 'public-two', title: 'Another source', position: 90,
        text: 'My subnet question.', kind: 'note', created_at: '2026-10-03T01:00:00Z'},
]

beforeAll(() => {(globalThis as {IS_REACT_ACT_ENVIRONMENT?: boolean}).IS_REACT_ACT_ENVIRONMENT = true})
afterEach(() => {act(() => root?.unmount()); root = undefined; document.body.innerHTML = ''; vi.unstubAllGlobals()})

async function show(replies: {status: number; body: unknown}[] = [{status: 200, body: records}]) {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (path: string) => {
        calls.push(path)
        const reply = replies.length > 1 ? replies.shift()! : replies[0]!
        return new Response(JSON.stringify(reply.body), {status: reply.status})
    }))
    const view = document.body.appendChild(document.createElement('div'))
    const client = new QueryClient({defaultOptions: {queries: {retry: false}}})
    root = createRoot(view)
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter><KnowledgePage/></MemoryRouter></QueryClientProvider>))
    return {view, calls}
}

async function until(check: () => boolean) {
    for (let i = 0; i < 100 && !check(); i++) await act(() => new Promise(resolve => setTimeout(resolve, 10)))
    expect(check()).toBe(true)
}

const button = (view: HTMLElement, text: string) => [...view.querySelectorAll('button')].find(item => item.textContent === text)!
async function search(view: HTMLElement, value: string) {
    const input = view.querySelector('input')!
    await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
        input.dispatchEvent(new Event('input', {bubbles: true}))
    })
}

describe('Knowledge source recovery', () => {
    it('shows saved source words and a deep link at the saved range without generating anything', async () => {
        const {view, calls} = await show()
        await until(() => view.textContent!.includes('A broken route'))
        expect(view.textContent).toContain('Check the return path first.')
        expect(view.textContent).toContain('Routing stories')
        expect(view.querySelector('b')).toBeNull()
        expect(view.querySelector('a.knowledge-source')?.getAttribute('href')).toBe('/learn?episode=public%2Fone&at=40')
        expect(calls).toEqual(['/companion/notes'])
    })

    it('searches exact quotes as well as personal notes and can recover from a no-match filter', async () => {
        const {view} = await show()
        await until(() => view.querySelectorAll('.knowledge-idea').length === 2)
        await search(view, 'one-way')
        expect(view.querySelectorAll('.knowledge-idea')).toHaveLength(1)
        expect(view.textContent).not.toContain('My subnet question.')
        await search(view, 'does not appear')
        expect(view.textContent).toContain('No saved ideas match')
        await act(async () => button(view, 'Show all saved material').click())
        expect(view.querySelectorAll('.knowledge-idea')).toHaveLength(2)
    })

    it('distinguishes source highlights from personal notes', async () => {
        const {view} = await show()
        await until(() => view.querySelectorAll('.knowledge-idea').length === 2)
        await act(async () => button(view, 'Saved ideas').click())
        expect(view.querySelectorAll('.knowledge-idea')).toHaveLength(1)
        expect(view.textContent).toContain('A broken route')
        await act(async () => button(view, 'My notes').click())
        expect(view.textContent).toContain('My subnet question.')
        expect(view.textContent).not.toContain('A broken route')
    })

    it('recovers a failed read with the saved records intact', async () => {
        const {view, calls} = await show([{status: 503, body: {detail: 'Unavailable'}}, {status: 200, body: records}])
        await until(() => view.textContent!.includes("Your saved ideas couldn't load"))
        await act(async () => button(view, 'Try again').click())
        await until(() => view.textContent!.includes('My subnet question.'))
        expect(calls).toEqual(['/companion/notes', '/companion/notes'])
    })

    it('keeps an empty library useful without creating generated records', async () => {
        const {view, calls} = await show([{status: 200, body: []}])
        await until(() => view.textContent!.includes('Keep an idea worth returning to'))
        expect(view.querySelector('a')?.getAttribute('href')).toBe('/home/view')
        expect(calls).toEqual(['/companion/notes'])
    })
})
