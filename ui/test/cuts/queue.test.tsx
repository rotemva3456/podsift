// "Cut my queue": Listen next opens /queue/cut, which plans across the queue.
import {afterEach, beforeAll, beforeEach, describe, expect, it, vi} from 'vitest'
import {act, type ReactNode} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {MemoryRouter} from 'react-router-dom'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import {episode, plan, serve} from './fixtures'

const first = episode(), second = episode({id: 'internal-2', episode_id: 'public-2', name: 'OSPF areas', local_url: '/podcasts/show/ospf.mp3'})
const queue = [{id: 'pl-1', name: 'Listen next', items: [{podcastEpisode: first}, {podcastEpisode: second}]}]
vi.mock('../../src/utils/http', () => ({$api: {}, client: {GET: async () => ({data: queue}), PUT: async () => ({}), POST: async () => ({})}}))

await import('../../src/ext/registry')
const {QueueCut} = await import('../../src/ext/features/cuts/QueueCut')
const {ListenQueue} = await import('../../src/pages/ListenQueue')
const useAudioPlayer = (await import('../../src/store/AudioPlayerSlice')).default
const {forgetWorkspaces} = await import('../../src/ext/features/cuts/CutWorkspace')

let root: Root | undefined
vi.setConfig({testTimeout: 30_000})
beforeAll(() => {(globalThis as {IS_REACT_ACT_ENVIRONMENT?: boolean}).IS_REACT_ACT_ENVIRONMENT = true})
beforeEach(() => {
    document.body.innerHTML = '<audio id="audio-player"></audio>'
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
    vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
    useAudioPlayer.setState({skipPlan: null, skipNext: [], loadedPodcastEpisode: undefined, pendingSeek: undefined})
    forgetWorkspaces()
})
afterEach(() => {act(() => root?.unmount()); root = undefined; vi.restoreAllMocks(); vi.unstubAllGlobals()})

async function show(ui: ReactNode) {
    const container = document.body.appendChild(document.createElement('div'))
    root = createRoot(container)
    await act(async () => root!.render(<QueryClientProvider client={new QueryClient({defaultOptions: {queries: {retry: false}}})}>
        <MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>))
    return container
}
async function until(check: () => boolean, tries = 500) {
    for (let i = 0; i < tries && !check(); i++) await act(() => new Promise(resolve => setTimeout(resolve, 10)))
    expect(check(), document.body.textContent ?? '').toBe(true)
}
const button = (view: HTMLElement, name: string) => [...view.querySelectorAll('button')].find(b => b.textContent === name)

describe('Cut my queue', () => {
    it('Listen next has a "Cut my queue" button that opens /queue/cut', async () => {
        const view = await show(<ListenQueue/>)
        await until(() => !!view.querySelector('a[href="/queue/cut"]'))
        expect(view.querySelector('a[href="/queue/cut"]')!.textContent).toContain('Cut my queue')
    })

    it('plans across the queue, lists passages by episode, and Smart Plays the episodes in order', async () => {
        const spans = [
            {id: 's1', episode_id: 'public-1', start: 200, end: 300, text: 'BGP weight.', why: 'Mentions: bgp', enabled: true},
            {id: 's2', episode_id: 'public-2', start: 50, end: 120, text: 'OSPF areas and BGP.', why: 'Mentions: bgp', enabled: true},
        ]
        const calls = serve(vi.stubGlobal, {
            'POST /companion/plans': {body: plan({spans, kept_seconds: 170, source_seconds: 5400})},
            'GET /api/v1/episodes/public-1': {body: {podcastEpisode: first, podcastHistoryItem: null}},
            'GET /api/v1/episodes/public-2': {body: {podcastEpisode: second, podcastHistoryItem: null}},
            'GET /api/v1/users/me': {body: {role: 'admin'}},
            'GET /companion/episodes/public-1/transcript': {body: {episode_id: 'public-1', source: null, timed: true, text: 'x', segments: []}},
            'GET /companion/episodes/public-2/transcript': {body: {episode_id: 'public-2', source: null, timed: true, text: 'x', segments: []}},
        })
        const view = await show(<QueueCut/>)
        await until(() => view.textContent!.includes('One plan across your Listen next queue: 2 episodes, 116 min.'))
        const want = view.querySelector<HTMLInputElement>('#cut-want-queue')!
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(want, 'BGP')
            want.dispatchEvent(new Event('input', {bubbles: true}))
        })
        await act(async () => button(view, 'Find the parts')!.click())
        await until(() => view.textContent!.includes('2.8 of 90 min'))
        expect(calls.find(c => c.key === 'POST /companion/plans')?.body).toMatchObject({source: 'queue', want: 'BGP', minutes: 15})
        expect([...view.querySelectorAll('.cut-episode')].map(e => e.textContent)).toEqual(['BGP deep dive', 'OSPF areas'])
        await until(() => button(view, 'Smart Play')?.disabled === false)
        await act(async () => button(view, 'Smart Play')!.click())
        await until(() => !!useAudioPlayer.getState().skipPlan)
        expect(useAudioPlayer.getState().skipPlan).toEqual({episodeId: 'public-1', keep: [[200, 300]], label: 'BGP route selection'})
        expect(useAudioPlayer.getState().skipNext).toEqual([{episodeId: 'public-2', keep: [[50, 120]], label: 'BGP route selection'}])
    })
})
