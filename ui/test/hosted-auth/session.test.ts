import {beforeEach, describe, expect, it, vi} from 'vitest'
import {QueryClient} from '@tanstack/react-query'
import {
    acceptAccount, clearPrivateAccountState, detectRuntimeMode, hostedFetch, hostedLogin, hostedLogout,
    resetHostedAuthForTests, setRuntimeMode,
} from '../../src/ext/features/hosted-auth/session'

beforeEach(() => {
    resetHostedAuthForTests()
    localStorage.clear(); sessionStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals()
})

describe('hosted cookie sessions', () => {
    it('detects hosted mode without reading protected config', async () => {
        const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({mode: 'hosted'}), {status: 200}))
        await expect(detectRuntimeMode(fetcher)).resolves.toBe('hosted')
        expect(fetcher).toHaveBeenCalledWith('/health', expect.objectContaining({credentials: 'same-origin'}))
    })

    it('refreshes one failed read once and never replays a write', async () => {
        setRuntimeMode('hosted')
        const fetcher = vi.fn()
            .mockResolvedValueOnce(new Response('', {status: 401}))
            .mockResolvedValueOnce(new Response('{}', {status: 200}))
            .mockResolvedValueOnce(new Response('{"ok":true}', {status: 200}))
        vi.stubGlobal('fetch', fetcher)
        expect((await hostedFetch('/api/v1/podcasts')).status).toBe(200)
        expect(fetcher).toHaveBeenCalledTimes(3)
        expect((fetcher.mock.calls[1]![0] as string)).toBe('/auth/refresh')

        fetcher.mockClear(); fetcher.mockResolvedValue(new Response('', {status: 401}))
        expect((await hostedFetch('/companion/notes', {method: 'POST'})).status).toBe(401)
        expect(fetcher).toHaveBeenCalledTimes(1)
    })

    it('signs in with cookies without storing the password or tokens', async () => {
        setRuntimeMode('hosted')
        const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = []
        vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
            calls.push([input, init])
            const path = input instanceof Request ? new URL(input.url).pathname : String(input)
            if (path === '/auth/login') return new Response(JSON.stringify({status: 'signed_in', user: {
                user_id: 'alice', tenant_id: 'personal', role: 'owner',
            }}), {status: 200})
            return new Response(JSON.stringify({username: 'ps_alice', role: 'user'}), {status: 200})
        }))
        await expect(hostedLogin('alice@example.test', 'private-password')).resolves.toMatchObject({user_id: 'alice'})
        expect(calls[0]![1]).toMatchObject({credentials: 'same-origin'})
        expect(JSON.stringify(localStorage) + JSON.stringify(sessionStorage)).not.toContain('private-password')
        expect(localStorage.getItem('auth')).toBeNull()
        expect(sessionStorage.getItem('auth')).toBeNull()
    })

    it('does not claim logout on owner failure', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"status":"revocation_failed"}', {status: 503})))
        await expect(hostedLogout()).rejects.toMatchObject({status: 503})
    })

    it('clears account queries while preserving public playback position', () => {
        const client = new QueryClient()
        localStorage.setItem('video-position:public-episode', '42')
        acceptAccount('alice', client)
        client.setQueryData(['private', 'notes'], ['secret'])
        acceptAccount('bob', client)
        expect(client.getQueryCache().getAll()).toHaveLength(0)
        expect(localStorage.getItem('video-position:public-episode')).toBe('42')
        client.setQueryData(['private'], 'again')
        clearPrivateAccountState(client)
        expect(client.getQueryCache().getAll()).toHaveLength(0)
        expect(localStorage.getItem('video-position:public-episode')).toBe('42')
    })
})
