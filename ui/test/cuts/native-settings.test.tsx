import {afterEach, beforeAll, describe, expect, it, vi} from 'vitest'
import {act} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import {serve, sponsorSettings} from './fixtures'

vi.mock('../../src/utils/http', () => ({$api: {useMutation: () => ({mutate() {}, mutateAsync: async () => ({})})}, client: {}}))
await import('../../src/ext/registry')
const {SponsorSettings} = await import('../../src/ext/features/cuts/SponsorSettings')
let root: Root | undefined
vi.setConfig({testTimeout: 30_000})
beforeAll(() => {(globalThis as {IS_REACT_ACT_ENVIRONMENT?: boolean}).IS_REACT_ACT_ENVIRONMENT = true})
afterEach(() => {act(() => root?.unmount()); root = undefined; document.body.innerHTML = ''; vi.unstubAllGlobals()})

async function show() {
    const container = document.body.appendChild(document.createElement('div'))
    root = createRoot(container)
    await act(async () => root!.render(<QueryClientProvider client={new QueryClient({defaultOptions: {queries: {retry: false}}})}>
        <SponsorSettings/>
    </QueryClientProvider>))
    return container
}
async function until(check: () => boolean) {
    for (let i = 0; i < 400 && !check(); i++) await act(() => new Promise(resolve => setTimeout(resolve, 10)))
    expect(check(), document.body.innerHTML).toBe(true)
}

describe('native skipping settings', () => {
    it('shows saved manual choices and a minimum length alongside all eight categories', async () => {
        serve(vi.stubGlobal, {
            'GET /api/v1/settings/sponsorblock': {body: sponsorSettings},
            'GET /companion/settings/skipping': {body: {manual_categories: ['sponsor'], minimum_seconds: 5}},
        })
        const view = await show()
        await until(() => view.querySelectorAll('.cut-categories [role="checkbox"]').length === 8)
        expect(view.querySelector('button[aria-label="Sponsor skip mode"]')?.textContent).toContain('Ask before skipping')
        expect(view.querySelector('button[aria-labelledby="cut-minimum-label"]')?.textContent).toContain('5 seconds')
        expect(view.textContent).toContain('Unchecked publisher timing always offers a manual choice')
    })

    it('a preference failure shows a retry without displaying invented defaults', async () => {
        serve(vi.stubGlobal, {
            'GET /api/v1/settings/sponsorblock': {body: sponsorSettings},
            'GET /companion/settings/skipping': {status: 503, body: {detail: 'Unavailable'}},
        })
        const view = await show()
        await until(() => !!view.querySelector('[role="alert"]'))
        expect(view.textContent).toContain("Sponsor settings couldn't load")
        expect(view.querySelector('.cut-categories')).toBeNull()
        expect(view.querySelector('button')?.textContent).toContain('Try again')
    })
})
