import type {QueryClient} from '@tanstack/react-query'

export type RuntimeMode = 'hosted' | 'self-hosted' | 'unavailable'
export type HostedUser = {user_id: string; tenant_id: string; role: 'owner' | 'operator'; product?: string}
export type HostedProfile = {username: string; role: string; id?: unknown; locale?: string}

let runtimeMode: RuntimeMode = 'unavailable'
let refreshInFlight: Promise<boolean> | undefined
let activeAccount: string | undefined

export function setRuntimeMode(mode: RuntimeMode) { runtimeMode = mode }
export function getRuntimeMode() { return runtimeMode }
export function isHostedRuntime() { return runtimeMode === 'hosted' }

export async function detectRuntimeMode(fetcher: typeof fetch = fetch): Promise<RuntimeMode> {
    try {
        const response = await fetcher('/health', {credentials: 'same-origin', cache: 'no-store'})
        if (response.status === 404) return 'self-hosted'
        if (!response.ok) return 'unavailable'
        const body = await response.json() as {mode?: string}
        return body.mode === 'hosted' ? 'hosted' : 'self-hosted'
    } catch {
        return 'unavailable'
    }
}

async function refresh(fetcher: typeof fetch): Promise<boolean> {
    if (!refreshInFlight) {
        refreshInFlight = fetcher('/auth/refresh', {
            method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
        }).then(response => response.ok).catch(() => false).finally(() => { refreshInFlight = undefined })
    }
    return refreshInFlight
}

export async function hostedFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
    const source = typeof input === 'string' ? new URL(input, window.location.origin) : input
    const request = new Request(source, {...init, credentials: 'same-origin'})
    const response = await fetch(request.clone())
    const method = request.method.toUpperCase()
    const path = new URL(request.url, window.location.origin).pathname
    if (runtimeMode !== 'hosted' || response.status !== 401 || !['GET', 'HEAD'].includes(method)
        || path.startsWith('/auth/')) return response
    if (!await refresh(fetch)) return response
    return fetch(request.clone())
}

export async function hostedProfile(): Promise<HostedProfile> {
    const response = await hostedFetch('/api/v1/users/me')
    if (!response.ok) throw Object.assign(new Error('profile'), {status: response.status})
    const profile = await response.json() as HostedProfile
    if (!profile.username) throw Object.assign(new Error('profile'), {status: 503})
    return profile
}

export async function hostedLogin(email: string, password: string): Promise<HostedUser> {
    const response = await fetch('/auth/login', {
        method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({email, password}),
    })
    if (!response.ok) throw Object.assign(new Error('login'), {status: response.status})
    const body = await response.json() as {user?: HostedUser}
    if (!body.user || !body.user.user_id) throw Object.assign(new Error('login'), {status: 503})
    await hostedProfile()
    return body.user
}

export async function hostedLogout(): Promise<void> {
    const response = await fetch('/auth/logout', {
        method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
    })
    if (!response.ok) throw Object.assign(new Error('logout'), {status: response.status})
}

export function acceptAccount(accountId: string, client: QueryClient): void {
    if (activeAccount && activeAccount !== accountId) client.clear()
    activeAccount = accountId
}

export function clearPrivateAccountState(client: QueryClient): void {
    client.clear()
    activeAccount = undefined
}

export function resetHostedAuthForTests(): void {
    runtimeMode = 'unavailable'; refreshInFlight = undefined; activeAccount = undefined
}
