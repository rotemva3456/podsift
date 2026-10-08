import {act} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {MemoryRouter} from 'react-router-dom'
import {afterEach, beforeAll, beforeEach, describe, expect, it, vi} from 'vitest'
import {Login} from '../../src/pages/Login'
import {resetHostedAuthForTests, setRuntimeMode} from '../../src/ext/features/hosted-auth/session'

let root: Root | undefined
beforeAll(() => {(globalThis as {IS_REACT_ACT_ENVIRONMENT?: boolean}).IS_REACT_ACT_ENVIRONMENT = true})
beforeEach(() => { resetHostedAuthForTests(); setRuntimeMode('hosted'); document.body.innerHTML = '' })
afterEach(() => {act(() => root?.unmount()); root = undefined; vi.restoreAllMocks(); vi.unstubAllGlobals()})

async function renderLogin() {
    const view = document.body.appendChild(document.createElement('div'))
    root = createRoot(view)
    await act(async () => root!.render(<MemoryRouter initialEntries={['/login']}><Login/></MemoryRouter>))
    return view
}

async function type(input: HTMLInputElement, value: string) {
    await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
        input.dispatchEvent(new Event('input', {bubbles: true}))
    })
}

describe('hosted login page', () => {
    it('submits email/password to the cookie endpoint and shows credential errors', async () => {
        const fetcher = vi.fn().mockResolvedValue(new Response('{}', {status: 401}))
        vi.stubGlobal('fetch', fetcher)
        const view = await renderLogin()
        expect(view.textContent).toContain('Sign in to Podsift')
        await type(view.querySelector('#hosted-email')!, 'alice@example.test')
        await type(view.querySelector('#hosted-password')!, 'wrong')
        await act(async () => view.querySelector<HTMLButtonElement>('button[type="submit"]')!.click())
        for (let i = 0; i < 20 && !view.querySelector('[role="alert"]'); i++)
            await act(() => new Promise(resolve => setTimeout(resolve, 5)))
        expect(view.querySelector('[role="alert"]')?.textContent).toContain('email or password')
        expect(fetcher).toHaveBeenCalledWith('/auth/login', expect.objectContaining({credentials: 'same-origin'}))
    })
})
