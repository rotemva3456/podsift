import {getConfigFromHtmlFile} from '../../utils/config'

/**
 * The Authorization header for the stored PodFetch login: Basic or Bearer, depending on how
 * PodFetch is configured, from local or session storage. undefined when nobody is logged in.
 * The one copy of this rule: the PodFetch client (utils/http.ts, which re-exports it), podfetch()
 * below and companion() (utils/companion.ts) all use it. It lives here, not in http.ts, because
 * importing http.ts sets up the whole PodFetch client, which fails outside the app's /ui/ pages.
 */
export function authHeader(config = getConfigFromHtmlFile()): string | undefined {
    const auth = localStorage.getItem('auth') || sessionStorage.getItem('auth')
    if (auth && config?.basicAuth) return 'Basic ' + auth
    if (auth && config?.oidcConfigured) return 'Bearer ' + auth
    return undefined
}

/**
 * fetch() for PodFetch's own API that returns the raw Response, so callers can act on the status
 * code. It sends the same login header as the PodFetch client in utils/http.ts, without that
 * client's error toasts.
 */
export function podfetch(path: string, init: RequestInit = {}): Promise<Response> {
    const headers = new Headers(init.headers)
    const authorization = authHeader()
    if (authorization) headers.set('Authorization', authorization)
    return fetch(path, {...init, headers})
}
