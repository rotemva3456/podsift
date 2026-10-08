// Check the actual installed UI, including its lazy chunks, through the public proxy.
const assert = require('assert/strict')
const origin = new URL(process.argv[2] || 'http://127.0.0.1:8080')
const ui = new URL('/ui/', origin)
const pending = new Set()
const visited = new Set()
let companionFound = false

function asset(reference, from) {
    // Vite's dependency map uses assets/x.js; ES module imports use ./x.js.
    if (!/^(?:\.\/|\/ui\/assets\/|assets\/)/.test(reference)) return
    const url = new URL(reference.startsWith('assets/') ? '/ui/' + reference : reference, from)
    if (url.origin === ui.origin && url.pathname.startsWith('/ui/assets/') && /\.(js|css)$/.test(url.pathname)) {
        pending.add(url.href)
    }
}

async function read(url) {
    const response = await fetch(url, {signal: AbortSignal.timeout(15_000)})
    assert(response.ok, `${new URL(url).pathname}: HTTP ${response.status}`)
    assert(response.url.startsWith(ui.origin + '/'), 'Asset redirects must stay on the installed origin')
    return response.text()
}

;(async () => {
    const html = await read(ui.href)
    for (const [, reference] of html.matchAll(/(?:src|href)=["']([^"']+)["']/g)) asset(reference, ui)
    assert([...pending].some(url => /\/index-[^/]+\.js$/.test(url)), 'The installed page must reference its built entry module')
    assert([...pending].some(url => url.endsWith('.css')), 'The installed page must reference its stylesheet')
    for (const url of pending) {
        if (visited.has(url)) continue
        assert(visited.size < 256, 'Unexpectedly large installed asset graph')
        visited.add(url)
        const content = await read(url)
        assert(!/^\s*(?:<!doctype|<html)/i.test(content), `${new URL(url).pathname} returned a page instead of an asset`)
        if (url.endsWith('.js')) {
            companionFound ||= content.includes('/companion')
            for (const [, reference] of content.matchAll(/["']([^"'\s]+\.(?:js|css))["']/g)) asset(reference, url)
        }
    }
    assert(companionFound, 'The installed modules must include the companion client')
    console.log(JSON.stringify({status: 'PASS', assets: visited.size, companion_client: companionFound}))
})().catch(error => {console.error(error.message); process.exitCode = 1})
