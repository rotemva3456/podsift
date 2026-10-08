import {act, type ReactNode} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {MemoryRouter} from 'react-router-dom'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import {afterEach, beforeAll, beforeEach, describe, expect, it, vi} from 'vitest'
import '../../src/ext/registry'
import {CutWorkspace, forgetWorkspaces, type CutWorkspaceProps} from '../../src/ext/features/cuts/CutWorkspace'
import {episode, plan, script, serve} from './fixtures'

let root: Root | undefined
let container: HTMLElement
let client: QueryClient

vi.setConfig({testTimeout: 30_000})
beforeAll(() => {(globalThis as {IS_REACT_ACT_ENVIRONMENT?: boolean}).IS_REACT_ACT_ENVIRONMENT = true})
beforeEach(() => {
    document.body.innerHTML = '<audio id="audio-player"></audio>'
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
    vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
    forgetWorkspaces()
    client = new QueryClient({defaultOptions: {queries: {retry: false}}})
    container = document.body.appendChild(document.createElement('div'))
    root = createRoot(container)
})
afterEach(() => {act(() => root?.unmount()); root = undefined; vi.restoreAllMocks(); vi.unstubAllGlobals()})

const LIST = 'GET /companion/plans?episode_id=public-1&limit=20'
const props = (patch: Partial<CutWorkspaceProps> = {}): CutWorkspaceProps => ({
    target: {kind: 'episode', episodeId: 'public-1'}, onPlay: vi.fn(), ...patch,
})
const agentPlan = (id = 'agent-1') => plan({id, mode: 'agent', want: 'Agent-selected routing lessons',
    episodes: [{episode_id: 'public-1', title: 'BGP deep dive', origin: 'generated', timing: 'ok'}]})

async function render(ui: ReactNode) {
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>))
}
async function until(check: () => boolean, tries = 500) {
    for (let i = 0; i < tries && !check(); i++) await act(() => new Promise(resolve => setTimeout(resolve, 10)))
    expect(check(), document.body.textContent ?? '').toBe(true)
}
const button = (name: string) => [...document.querySelectorAll('button')].find(item => item.textContent === name)

describe('shared cut plans', () => {
    it('reopens a saved Focus listen with its persisted effort and distinguishes Chill choices', async () => {
        const focused = {...agentPlan(), learning_mode: 'focus' as const}
        const chilled = {...agentPlan('chill-plan'), learning_mode: 'chill' as const}
        serve(vi.stubGlobal, {[LIST]: {body: [focused, chilled]}, 'GET /companion/plans/agent-1': {body: focused},
            'GET /companion/plans/agent-1/script': {body: script({plan_id: 'agent-1'})}})
        await render(<CutWorkspace {...props({initialPlanId: 'agent-1'})}/>)
        await until(() => document.querySelector('.cut-plan-head')?.textContent?.includes('Focus') === true)
        expect(button('Focus')?.getAttribute('aria-pressed')).toBe('true')
        expect(document.querySelector('button[aria-label="Saved listen"]')?.textContent).toContain('Focus')
    })

    it('opens an external agent plan and keeps its exact script, reasons, controls and origin', async () => {
        const exactScript = script({plan_id: 'agent-1', want: 'Agent-selected routing lessons'})
        const explicitOmission = exactScript.episodes[0]!.parts.find(part => part.kind === 'cut' && part.reason === 'skip')!
        explicitOmission.why = 'Already covered in the routing book, chapter 12'
        serve(vi.stubGlobal, {
            [LIST]: {body: [agentPlan()]},
            'GET /companion/plans/agent-1': {body: agentPlan()},
            'GET /companion/plans/agent-1/script': {body: exactScript},
            'GET /api/v1/episodes/public-1': {body: {podcastEpisode: episode(), podcastHistoryItem: null}},
            'GET /api/v1/users/me': {body: {role: 'admin'}},
        })
        await render(<CutWorkspace {...props({initialPlanId: 'agent-1'})}/>)
        await until(() => document.body.textContent!.includes('12 of 58 min'))
        expect(document.body.textContent).toContain('Selected passages')
        expect(document.body.textContent).toContain('BGP picks routes by weight first.')
        expect(document.body.textContent).toContain('matches “BGP”')
        expect(document.body.textContent).toContain('Already covered in the routing book, chapter 12')
        expect(document.body.textContent).not.toContain('Says “”, which you skip')
        expect(button('Smart Play')).toBeTruthy()
        expect(button('Approve and export MP3')).toBeTruthy()
        expect(button('Remove')).toBeTruthy()
        const planning = document.querySelector<HTMLDetailsElement>('.cut-planning-disclosure')!
        expect(planning.open).toBe(false)
        expect(planning.contains(document.querySelector('.cut-plan'))).toBe(false)
    })

    it('opens a changed input plan ID after mount without echoing onPlanChange', async () => {
        const first = agentPlan('agent-1'), second = agentPlan('agent-2')
        second.want = 'Second saved listen'
        const calls = serve(vi.stubGlobal, {
            [LIST]: {body: [first, second]},
            'GET /companion/plans/agent-1': {body: first},
            'GET /companion/plans/agent-2': {body: second},
            'GET /companion/plans/agent-1/script': {body: script({plan_id: 'agent-1'})},
            'GET /companion/plans/agent-2/script': {body: script({plan_id: 'agent-2', want: second.want})},
            'GET /api/v1/episodes/public-1': {body: {podcastEpisode: episode(), podcastHistoryItem: null}},
            'GET /api/v1/users/me': {body: {role: 'admin'}},
        })
        const changed = vi.fn()
        await render(<CutWorkspace {...props({initialPlanId: 'agent-1', onPlanChange: changed})}/>)
        await until(() => calls.some(call => call.key === 'GET /companion/plans/agent-1'))
        await render(<CutWorkspace {...props({initialPlanId: 'agent-2', onPlanChange: changed})}/>)
        await until(() => calls.some(call => call.key === 'GET /companion/plans/agent-2'))
        expect(changed).not.toHaveBeenCalled()
        await until(() => document.body.textContent!.includes('Second saved listen'))
    })

    it('opens an explicitly selected saved plan and reports the ID for URL persistence', async () => {
        const saved = agentPlan()
        const calls = serve(vi.stubGlobal, {
            [LIST]: {body: [saved]},
            'GET /companion/plans/agent-1': {body: saved},
            'GET /companion/plans/agent-1/script': {body: script({plan_id: 'agent-1'})},
            'GET /api/v1/episodes/public-1': {body: {podcastEpisode: episode(), podcastHistoryItem: null}},
            'GET /api/v1/users/me': {body: {role: 'admin'}},
        })
        const changed = vi.fn()
        await render(<CutWorkspace {...props({onPlanChange: changed})}/>)
        await until(() => !!document.querySelector('button[aria-label="Saved listen"]'))
        await act(async () => {
            const trigger = document.querySelector<HTMLButtonElement>('button[aria-label="Saved listen"]')!
            trigger.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}))
            trigger.click()
        })
        await until(() => [...document.querySelectorAll('[data-slot="select-item"]')].some(option => option.textContent?.includes('Agent-selected routing lessons')))
        const option = [...document.querySelectorAll<HTMLElement>('[data-slot="select-item"]')].find(item => item.textContent?.includes('Agent-selected routing lessons'))!
        await act(async () => option.click())
        await until(() => button('Open plan')?.disabled === false)
        await act(async () => button('Open plan')!.click())
        await until(() => calls.some(call => call.key === 'GET /companion/plans/agent-1'))
        expect(changed).toHaveBeenCalledTimes(1)
        expect(changed).toHaveBeenCalledWith('agent-1')
    })

    it.each([
        ['an unknown source', agent({id: 'unsafe-unknown', status: 'ready', episodes: undefined, spans: [], omitted: [], needs_timing: []}),
            'belongs to a different episode'],
        ['another episode', agent({id: 'unsafe-other', episodes: [{episode_id: 'public-2', title: 'Other'}], spans: plan().spans.map(span => ({...span, episode_id: 'public-2'}))}),
            'belongs to a different episode'],
        ['multiple episodes', agent({id: 'unsafe-many', episodes: [{episode_id: 'public-1'}, {episode_id: 'public-2'}],
            spans: [...plan().spans, {...plan().spans[0]!, id: 'other', episode_id: 'public-2'}]}), 'uses multiple episodes'],
    ])('rejects a plan from %s before playback or export', async (_name, unsafe, message) => {
        serve(vi.stubGlobal, {[LIST]: {body: [unsafe]}, [`GET /companion/plans/${unsafe.id}`]: {body: unsafe}})
        await render(<CutWorkspace {...props({initialPlanId: unsafe.id})}/>)
        await until(() => document.body.textContent!.includes(message))
        await until(() => document.querySelector<HTMLDetailsElement>('.cut-planning-disclosure')?.open === true)
        expect(button('Smart Play')).toBeUndefined()
        expect(button('Approve and export MP3')).toBeUndefined()
    })

    it('shows a source-less empty legacy result because it has nothing playable or exportable', async () => {
        const empty = plan({id: 'legacy-empty', status: 'empty', spans: [], kept_seconds: 0, want: 'quantum gravity',
            episodes: undefined, omitted: [], needs_timing: []})
        serve(vi.stubGlobal, {[LIST]: {body: [empty]}, 'GET /companion/plans/legacy-empty': {body: empty}})
        await render(<CutWorkspace {...props({initialPlanId: 'legacy-empty'})}/>)
        await until(() => document.body.textContent!.includes('Nothing matched'))
        expect(document.body.textContent).toContain('Nothing in this episode matched “quantum gravity”.')
        expect(document.body.textContent).not.toContain('belongs to a different episode')
        expect(button('Approve and export MP3')).toBeUndefined()
    })

    it('shows empty and error discovery states with a retry control', async () => {
        serve(vi.stubGlobal, {[LIST]: [{status: 500, body: {detail: 'offline'}}, {body: []}]})
        await render(<CutWorkspace {...props()}/>)
        expect(document.querySelector<HTMLDetailsElement>('.cut-planning-disclosure')?.open).toBe(true)
        await until(() => document.body.textContent!.includes("Saved listens couldn't load."))
        await act(async () => button('Try again')!.click())
        await until(() => document.body.textContent!.includes('No saved listens for this episode yet.'))
    })

    it('contains denied clipboard errors and keeps the workspace usable', async () => {
        serve(vi.stubGlobal, {[LIST]: {body: []}})
        const original = Object.getOwnPropertyDescriptor(navigator, 'clipboard')
        Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: vi.fn().mockRejectedValue(new Error('denied'))}})
        try {
            await render(<CutWorkspace {...props()}/>)
            await until(() => !!button('Copy agent instructions'))
            await act(async () => button('Copy agent instructions')!.click())
            await until(() => document.body.textContent!.includes("The instructions couldn't be copied."))
            expect(button('Copy agent instructions')?.disabled).toBe(false)
            expect(container.querySelector('#cut-want-episode\\:public-1')).toBeTruthy()
        } finally {
            if (original) Object.defineProperty(navigator, 'clipboard', original)
            else Reflect.deleteProperty(navigator, 'clipboard')
        }
    })

    it('leaves queue planning compatible and does not request episode plan discovery', async () => {
        const made = plan({status: 'empty', spans: [], kept_seconds: 0, episodes: []})
        const calls = serve(vi.stubGlobal, {'POST /companion/plans': {body: made}})
        const changed = vi.fn()
        await render(<CutWorkspace target={{kind: 'queue', episodeIds: ['public-1']}} titles={{'public-1': 'BGP deep dive'}}
            onPlay={vi.fn()} onPlanChange={changed}/>)
        const want = container.querySelector<HTMLInputElement>('#cut-want-queue')!
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(want, 'BGP')
            want.dispatchEvent(new Event('input', {bubbles: true}))
        })
        await act(async () => button('Find the parts')!.click())
        await until(() => calls.some(call => call.key === 'POST /companion/plans'))
        expect(calls.some(call => call.key.includes('/companion/plans?'))).toBe(false)
        expect(changed).toHaveBeenCalledWith('plan-1')
    })
})

function agent(patch: Parameters<typeof plan>[0]) {
    return plan({mode: 'agent', ...patch})
}
