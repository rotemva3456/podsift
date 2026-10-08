// Settings → AI: paste a key, load models, Test, Save; env-locked fields; errors; the Ask panel's link here.
import {afterEach, beforeAll, describe, expect, it, vi} from 'vitest'
import {act, type ReactElement} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {MemoryRouter} from 'react-router-dom'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import i18n from '../../../language/i18n'
import en from './locales/en.json'
import type {AiSettings as Saved, Preset} from './api'

vi.mock('../../../utils/listening', () => ({clock: (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`}))
i18n.addResourceBundle('en', 'ai-settings', en, true, true)
const {AiSettings} = await import('./AiSettings')
const {AskEpisode} = await import('../../../components/AskEpisode')

const KEY = 'gsk_UITESTKEY_0123456789abcdefSECRET'
const PRESETS: Preset[] = [
    {id: 'openai', label: 'OpenAI', base_url: 'https://api.openai.com/v1', needs_key: true, key_url: 'https://platform.openai.com/api-keys', max_input_chars: 60000, suggested_models: []},
    {id: 'groq', label: 'Groq', base_url: 'https://api.groq.com/openai/v1', needs_key: true, key_url: 'https://console.groq.com/keys', max_input_chars: 20000, suggested_models: ['openai/gpt-oss-120b']},
    {id: 'openrouter', label: 'OpenRouter', base_url: 'https://openrouter.ai/api/v1', needs_key: true, key_url: null, max_input_chars: 60000, suggested_models: []},
    {id: 'ollama', label: 'Ollama', base_url: 'http://ollama:11434/v1', needs_key: false, key_url: null, max_input_chars: 12000, suggested_models: []},
    {id: 'custom', label: 'Custom', base_url: '', needs_key: false, key_url: null, max_input_chars: 60000, suggested_models: []},
]
const settings = (change: Partial<Saved> = {}): Saved => ({provider: 'groq', base_url: 'https://api.groq.com/openai/v1',
    model: '', max_input_chars: 20000, key_set: false, key_hint: null, configured: false, from_env: [], problem: null,
    saved: false, presets: PRESETS, ...change})

type Reply = {status: number; body?: unknown}
let root: Root | undefined
beforeAll(() => {(globalThis as {IS_REACT_ACT_ENVIRONMENT?: boolean}).IS_REACT_ACT_ENVIRONMENT = true})
afterEach(() => {act(() => root?.unmount()); root = undefined; document.body.innerHTML = ''; vi.unstubAllGlobals()})

/** Answer "METHOD path" from `routes` (replies in order; the last repeats) and record each request body. */
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

const field = (view: HTMLElement, label: string) => {
    const id = [...view.querySelectorAll('label')].find(l => l.textContent === label)?.htmlFor
    return (id ? view.ownerDocument.getElementById(id) : null) as HTMLInputElement | null
}
const button = (view: HTMLElement, name: string) => [...view.querySelectorAll('button')].find(b => b.textContent === name)!
// Test and Save stay focusable while unavailable (aria-disabled), so keyboard focus never drops to the page.
const unavailable = (b: HTMLButtonElement) => b.disabled || b.getAttribute('aria-disabled') === 'true'
async function type(input: HTMLInputElement, value: string) {
    await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
        input.dispatchEvent(new Event('input', {bubbles: true}))
    })
}
const enter = (input: HTMLInputElement) => act(async () => {input.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}))})
const click = (element: HTMLElement) => act(async () => element.click())

describe('Settings → AI', () => {
    it('paste a key: the models load, the suggested one is picked, Test is green and Save keeps the key write-only', async () => {
        const saved = settings({model: 'openai/gpt-oss-120b', key_set: true, key_hint: 'CRET', configured: true, saved: true})
        const calls = serve({
            'GET /companion/settings/ai': [{status: 200, body: settings()}],
            'POST /companion/settings/ai/models': [{status: 200, body: {models: [{id: 'openai/gpt-oss-120b', context: 131072},
                {id: 'openai/gpt-oss-20b', context: 131072}], suggested: 'openai/gpt-oss-120b'}}],
            'POST /companion/settings/ai/test': [{status: 200, body: {ok: true, needs: null, seconds: 0.8,
                message: 'Connected. openai/gpt-oss-120b answered in 0.8 s.'}}],
            'PUT /companion/settings/ai': [{status: 200, body: saved}],
        })
        const view = await show(<AiSettings/>)
        await until(() => view.textContent!.includes('Not connected yet.'))
        expect(view.textContent).toContain('Paste your key to see the models.')
        expect(view.querySelector('a[href="https://console.groq.com/keys"]')?.textContent).toContain('Get a Groq key')
        const key = field(view, 'API key')!
        expect(key.type).toBe('password')
        await type(key, KEY)
        await enter(key)
        await until(() => field(view, 'Model')!.value === 'openai/gpt-oss-120b')
        expect(view.textContent).toContain('2 models available.')
        expect(view.querySelectorAll('datalist option')).toHaveLength(2)
        expect(calls.find(c => c.key === 'POST /companion/settings/ai/models')?.body).toEqual({provider: 'groq',
            base_url: 'https://api.groq.com/openai/v1', model: '', max_input_chars: 20000, api_key: KEY})

        await click(button(view, 'Test'))
        await until(() => view.textContent!.includes('Connected. openai/gpt-oss-120b answered in 0.8 s.'))
        expect(calls.find(c => c.key === 'POST /companion/settings/ai/test')?.body).toMatchObject({api_key: KEY, model: 'openai/gpt-oss-120b'})

        await click(button(view, 'Save'))
        await until(() => view.textContent!.includes('Saved.'))
        expect(calls.find(c => c.key === 'PUT /companion/settings/ai')?.body).toEqual({provider: 'groq',
            base_url: 'https://api.groq.com/openai/v1', model: 'openai/gpt-oss-120b', max_input_chars: 20000, api_key: KEY})
        expect(view.textContent).toContain('Connected: Groq · openai/gpt-oss-120b')
        expect(field(view, 'API key')!.value).toBe('')
        expect(field(view, 'API key')!.placeholder).toBe('Saved key ending in CRET')
        expect(view.textContent).toContain('Paste a new key to replace it.')
        expect(document.body.innerHTML).not.toContain(KEY)
        expect(unavailable(button(view, 'Save'))).toBe(true)
    })

    it('choosing Ollama needs no key, and a new provider starts from its own address and limit', async () => {
        serve({'GET /companion/settings/ai': [{status: 200, body: settings()}],
            'POST /companion/settings/ai/models': [{status: 200, body: {models: [{id: 'llama3.2', context: null}], suggested: null}}]})
        const view = await show(<AiSettings/>)
        await until(() => !!field(view, 'API key'))
        const ollama = view.querySelector<HTMLInputElement>('input[type=radio][value=ollama]')!
        await click(ollama)
        expect(field(view, 'API address (base URL)')!.value).toBe('http://ollama:11434/v1')
        expect(field(view, 'Longest text per request (characters)')!.value).toBe('12000')
        expect(field(view, 'API key (optional)')).toBeTruthy()
        await until(() => view.textContent!.includes('1 model available.'))
    })

    it('settings from the server env are read-only and never sent back', async () => {
        const calls = serve({'GET /companion/settings/ai': [{status: 200, body: settings({from_env: ['api_key', 'base_url'],
            key_set: true, key_hint: 'CRET', model: 'openai/gpt-oss-20b', configured: true, saved: true})}],
            'POST /companion/settings/ai/models': [{status: 200, body: {models: [{id: 'openai/gpt-oss-20b', context: null}], suggested: null}}],
            'PUT /companion/settings/ai': [{status: 200, body: settings({from_env: ['api_key', 'base_url'], key_set: true,
                key_hint: 'CRET', model: 'qwen/qwen3.8-27b', configured: true, saved: true})}]})
        const view = await show(<AiSettings/>)
        await until(() => view.textContent!.includes('Set on the server with LLM_API_KEY in .env. It ends in CRET.'))
        expect(field(view, 'API key')).toBeNull()
        expect(field(view, 'API address (base URL)')!.disabled).toBe(true)
        expect(view.querySelector<HTMLFieldSetElement>('fieldset')!.disabled).toBe(true)
        await type(field(view, 'Model')!, 'qwen/qwen3.8-27b')
        await click(button(view, 'Save'))
        await until(() => view.textContent!.includes('Saved.'))
        expect(calls.find(c => c.key === 'PUT /companion/settings/ai')?.body).toEqual({model: 'qwen/qwen3.8-27b', max_input_chars: 20000})
    })

    it('shows a failed test, a save error, an invalid limit and a load error with retry', async () => {
        const calls = serve({'GET /companion/settings/ai': [{status: 500, body: {detail: 'Down.'}}, {status: 200, body: settings({key_set: true,
                key_hint: 'CRET', model: 'm', configured: true, saved: true})}],
            'POST /companion/settings/ai/models': [{status: 502, body: {detail: 'The key was rejected.'}}],
            'POST /companion/settings/ai/test': [{status: 200, body: {ok: false, needs: null, message: 'The key was rejected.'}}],
            'PUT /companion/settings/ai': [{status: 422, body: {detail: 'That address is blocked: metadata.google.internal is a cloud metadata address.'}}]})
        const view = await show(<AiSettings/>)
        await until(() => view.querySelector('[role=alert]')?.textContent?.includes("The AI settings couldn't load.") ?? false)
        await click(button(view, 'Try again'))
        await until(() => view.textContent!.includes('Connected: Groq · m'))
        await until(() => view.textContent!.includes("The models couldn't load: The key was rejected."))
        await click(button(view, 'Test'))
        await until(() => view.querySelector('.ai-outcome[data-tone=bad]')?.textContent === 'The key was rejected.')
        await type(field(view, 'API address (base URL)')!, 'http://metadata.google.internal/v1')
        await click(button(view, 'Save'))
        await until(() => view.textContent!.includes('cloud metadata address'))
        await type(field(view, 'Longest text per request (characters)')!, '10')
        expect(view.textContent).toContain('Use a whole number from 1,000 to 2,000,000.')
        expect(unavailable(button(view, 'Save'))).toBe(true)
        const puts = calls.filter(c => c.key === 'PUT /companion/settings/ai').length
        await click(button(view, 'Save'))
        expect(calls.filter(c => c.key === 'PUT /companion/settings/ai')).toHaveLength(puts)
        await click(button(view, 'Forget the saved key'))
        expect(view.textContent).toContain('The saved key will be removed when you save.')
    })
})

describe('Ask panel', () => {
    it('points to Settings → AI when AI answers are off', async () => {
        serve({'GET /companion/answers/status': [{status: 200, body: {configured: false}}]})
        const view = await show(<AskEpisode episodeId="public-1" position={30} onSeek={() => {}}/>)
        await until(() => !!view.querySelector('a[href="/settings/ai"]'))
        expect(view.querySelector('a[href="/settings/ai"]')!.textContent).toBe('Connect AI in Settings → AI')
        expect(view.textContent).toContain('You can still find and play source passages.')
    })
})
