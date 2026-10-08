// The Cut tab, against fixtures of the cuts API.
import {afterEach, beforeAll, beforeEach, describe, expect, it, vi} from 'vitest'
import {act} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {MemoryRouter} from 'react-router-dom'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import '../../src/ext/registry'
import {CutPanel} from '../../src/ext/features/cuts/CutPanel'
import {forgetWorkspaces} from '../../src/ext/features/cuts/CutWorkspace'
import useAudioPlayer from '../../src/store/AudioPlayerSlice'
import type {PodcastEpisode} from '../../src/ext/types'
import {check, cut, episode, job, plan, script, serve, transcript} from './fixtures'

let root: Root | undefined
vi.setConfig({testTimeout: 30_000})  // a busy machine makes these slow; each wait below still has its own limit
beforeAll(() => {(globalThis as {IS_REACT_ACT_ENVIRONMENT?: boolean}).IS_REACT_ACT_ENVIRONMENT = true})
beforeEach(() => {
    document.body.innerHTML = '<audio id="audio-player"></audio>'
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
    vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
    useAudioPlayer.setState({skipPlan: null, skipNext: [], loadedPodcastEpisode: undefined, pendingSeek: undefined, metadata: undefined})
    forgetWorkspaces()
})
afterEach(() => {act(() => root?.unmount()); root = undefined; vi.restoreAllMocks(); vi.unstubAllGlobals()})

const EPISODE = 'GET /api/v1/episodes/public-1'
const TRANSCRIPT = 'GET /companion/episodes/public-1/transcript'
const PLANS = 'POST /companion/plans'
const SAVED_PLANS = 'GET /companion/plans?episode_id=public-1&limit=20'
const base = (patch: Record<string, unknown> = {}) => ({
    [TRANSCRIPT]: {body: transcript},
    [EPISODE]: {body: {podcastEpisode: episode(), podcastHistoryItem: null}},
    [SAVED_PLANS]: {body: []},
    'GET /api/v1/users/me': {body: {role: 'admin'}},
    ...patch,
})

async function show(seek = vi.fn(), current: PodcastEpisode = episode()) {
    const container = document.body.appendChild(document.createElement('div'))
    const client = new QueryClient({defaultOptions: {queries: {retry: false}}})
    root = createRoot(container)
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter>
        <CutPanel episodeId="public-1" episode={current} position={0} seek={seek}/></MemoryRouter></QueryClientProvider>))
    return container
}
async function until(check: () => boolean, tries = 500) {
    for (let i = 0; i < tries && !check(); i++) await act(() => new Promise(resolve => setTimeout(resolve, 10)))
    expect(check(), document.body.textContent ?? '').toBe(true)
}
const button = (view: HTMLElement, name: string | RegExp) => [...view.querySelectorAll('button')].find(b =>
    typeof name === 'string' ? b.textContent === name || b.getAttribute('aria-label') === name : name.test(b.textContent + ' ' + (b.getAttribute('aria-label') ?? '')))
async function click(view: HTMLElement, name: string | RegExp) {
    await until(() => button(view, name)?.disabled === false)
    await act(async () => button(view, name)!.click())
}
async function type(input: HTMLInputElement, value: string) {
    await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
        input.dispatchEvent(new Event('input', {bubbles: true}))
    })
}
async function findParts(view: HTMLElement, want = 'BGP route selection') {
    await until(() => !!view.querySelector('#cut-want-episode\\:public-1'))
    await type(view.querySelector<HTMLInputElement>('#cut-want-episode\\:public-1')!, want)
    await type(view.querySelector<HTMLInputElement>('#cut-skip-episode\\:public-1')!, 'history')
    await click(view, 'Find the parts')
}

describe('Cut panel', () => {
    it.each(['focus', 'chill'] as const)('uses AI selection for %s and preserves the effort label', async effort => {
        const made = plan({mode: 'ai', learning_mode: effort})
        const calls = serve(vi.stubGlobal, base({'GET /companion/settings/ai': {body: {configured: true}}, [PLANS]: {body: made}}))
        const view = await show()
        await until(() => view.querySelector<HTMLInputElement>('input[value="ai"]')?.disabled === false)
        await click(view, effort === 'focus' ? 'Focus' : 'Chill')
        expect(view.querySelector('input[value="keyword"]')).toBeNull()
        await findParts(view)
        await until(() => view.textContent!.includes('12 of 58 min'))
        expect(calls.find(c => c.key === PLANS)?.body).toMatchObject({mode: 'ai', learning_mode: effort, minutes: 15})
        expect([...view.querySelectorAll('.cut-badge')].map(item => item.textContent)).toContain(effort === 'focus' ? 'Focus' : 'Chill')
    })

    it('keeps effort selection useful without AI and restores keyword planning on Any effort', async () => {
        const calls = serve(vi.stubGlobal, base({[PLANS]: {body: plan()}}))
        const view = await show()
        await until(() => view.textContent!.includes('AI needs a connection first.'))
        await click(view, 'Chill')
        await type(view.querySelector<HTMLInputElement>('#cut-want-episode\\:public-1')!, 'BGP')
        expect(button(view, 'Find the parts')!.disabled).toBe(true)
        expect(view.textContent).toContain('Copy a request for your connected agent')
        const clipboard = vi.fn().mockResolvedValue(undefined)
        const original = Object.getOwnPropertyDescriptor(navigator, 'clipboard')
        Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: clipboard}})
        try {
            await click(view, 'Copy agent instructions')
            expect(clipboard).toHaveBeenCalledWith(expect.stringContaining('learning_mode=chill'))
            expect(clipboard).toHaveBeenCalledWith(expect.stringContaining('My goal: BGP'))
            expect(calls.some(call => call.key === PLANS)).toBe(false)
        } finally {
            if (original) Object.defineProperty(navigator, 'clipboard', original)
            else Reflect.deleteProperty(navigator, 'clipboard')
        }
        await click(view, 'Any effort')
        await click(view, 'Find the parts')
        await until(() => calls.some(call => call.key === PLANS))
        expect(calls.find(call => call.key === PLANS)?.body).toMatchObject({mode: 'keyword', want: 'BGP'})
    })

    it('does not replace an unavailable Chill result with harder material', async () => {
        const made = plan({mode: 'ai', learning_mode: 'chill', status: 'empty', spans: [], kept_seconds: 0})
        serve(vi.stubGlobal, base({'GET /companion/settings/ai': {body: {configured: true}}, [PLANS]: {body: made}}))
        const view = await show()
        await until(() => view.querySelector<HTMLInputElement>('input[value="ai"]')?.disabled === false)
        await click(view, 'Chill')
        await findParts(view)
        await until(() => view.textContent!.includes('No passages fit Chill'))
        expect(button(view, 'Smart Play')).toBeUndefined()
        expect(view.textContent).toContain('Your original episode is still available.')
    })

    it('no transcript: offers "Make a transcript"', async () => {
        serve(vi.stubGlobal, base({[TRANSCRIPT]: {body: {...transcript, text: '', segments: []}},
            'GET /api/v1/podcasts/episodes/internal-1/transcripts': {body: []}}))
        const view = await show()
        await until(() => view.textContent!.includes('No transcript for this episode yet'))
        await until(() => !!button(view, 'Make a transcript'))
    })

    it('keyword plan: sends the form, previews "12 of 58 min", plays and removes passages', async () => {
        const seek = vi.fn()
        const calls = serve(vi.stubGlobal, base({[PLANS]: {body: plan()},
            'PATCH /companion/plans/plan-1': {body: plan({kept_seconds: 480, spans: plan().spans.map(s => s.id === 's1' ? {...s, enabled: false} : s)})}}))
        const view = await show(seek)
        await findParts(view)
        await until(() => view.textContent!.includes('12 of 58 min'))
        expect(calls.find(c => c.key === PLANS)?.body).toEqual({episode_ids: ['public-1'], want: 'BGP route selection', skip: 'history',
            minutes: 15, skip_ads: true, mode: 'keyword'})
        expect(view.textContent).toContain('2 passages')
        expect(view.textContent).toContain('BGP picks routes by weight first.')
        expect(view.textContent).toContain('matches “BGP”')
        await click(view, 'Play passage at 3:20')
        expect(seek).toHaveBeenCalledWith(200)
        await click(view, 'Remove passage at 3:20')
        await until(() => !!button(view, 'Put back passage at 3:20'))
        expect(calls.find(c => c.key === 'PATCH /companion/plans/plan-1')?.body).toEqual({spans: [{id: 's1', enabled: false}]})
        expect(view.textContent).toContain('8 of 58 min')
        expect(view.textContent).toContain('1 passage')
    })

    it('AI mode is off with an explanation until AI is connected', async () => {
        serve(vi.stubGlobal, base())
        const view = await show()
        await until(() => view.textContent!.includes('AI needs a connection first.'))
        expect(view.querySelector<HTMLInputElement>('input[value="ai"]')!.disabled).toBe(true)
        expect(view.querySelector('a[href="/settings/ai"]')?.textContent).toBe('Connect AI in Settings → AI')
        expect(view.querySelector<HTMLInputElement>('input[value="keyword"]')!.checked).toBe(true)
    })

    it('AI mode stays off while AI settings says the provider is not configured', async () => {
        serve(vi.stubGlobal, base({'GET /companion/settings/ai': {body: {provider: 'groq', base_url: 'https://api.groq.com/openai/v1',
            model: '', max_input_chars: 60000, key_set: true, key_hint: 'ab12', configured: false}}}))
        const view = await show()
        await until(() => view.textContent!.includes('AI needs a connection first.'))
        expect(view.querySelector<HTMLInputElement>('input[value="ai"]')!.disabled).toBe(true)
    })

    it('AI mode, once connected, sends mode "ai"', async () => {
        const calls = serve(vi.stubGlobal, base({'GET /companion/settings/ai': {body: {provider: 'groq', base_url: 'https://api.groq.com/openai/v1',
            model: 'llama-3.3-70b', max_input_chars: 60000, key_set: true, key_hint: 'ab12', configured: true}}, [PLANS]: {body: plan({mode: 'ai'})}}))
        const view = await show()
        await until(() => view.querySelector<HTMLInputElement>('input[value="ai"]')?.disabled === false)
        expect(view.textContent).not.toContain('AI needs a connection first.')
        await act(async () => view.querySelector<HTMLInputElement>('input[value="ai"]')!.click())
        await findParts(view)
        await until(() => calls.some(c => c.key === PLANS))
        expect((calls.find(c => c.key === PLANS)!.body as {mode: string}).mode).toBe('ai')
    })

    it('nothing matched', async () => {
        serve(vi.stubGlobal, base({[PLANS]: {body: plan({status: 'empty', spans: [], kept_seconds: 0, want: 'quantum gravity'})}}))
        const view = await show()
        await findParts(view, 'quantum gravity')
        await until(() => view.textContent!.includes('Nothing matched'))
        expect(view.textContent).toContain('Nothing in this episode matched “quantum gravity”.')
    })

    it('over budget: says how many matching passages did not fit', async () => {
        const omitted = [1, 2, 3].map(n => ({episode_id: 'public-1', start: n * 100, end: n * 100 + 50, reason: 'over the time budget'}))
        serve(vi.stubGlobal, base({[PLANS]: {body: plan({omitted})}}))
        const view = await show()
        await findParts(view)
        await until(() => view.textContent!.includes("3 more passages matched but didn't fit in 15 min."))
    })

    it('over budget with nothing left: says the matches are longer than the time, not "nothing matched"', async () => {
        const omitted = [{episode_id: 'public-1', start: 24.8, end: 364.8, reason: 'over the time budget'},
            {episode_id: 'public-1', start: 606.4, end: 851.8, reason: 'over the time budget'}]
        serve(vi.stubGlobal, base({[PLANS]: {body: plan({status: 'empty', spans: [], kept_seconds: 0, minutes: 1.5, omitted})}}))
        const view = await show()
        await findParts(view)
        await until(() => view.textContent!.includes('Nothing fits in 1.5 min'))
        expect(view.textContent).toContain('2 passages matched, but each is longer than that.')
        expect(view.textContent).not.toContain('Nothing matched')
    })

    it('needs timing: explains, and makes a transcript from the file, then finds the parts again', async () => {
        const needs = plan({status: 'needs_timing', spans: [], kept_seconds: 0,
            needs_timing: [{episode_id: 'public-1', reason: 'The transcript runs past the end of the file.'}]})
        const calls = serve(vi.stubGlobal, base({[PLANS]: [{body: needs}, {body: plan()}],
            'POST /api/v1/podcasts/episodes/internal-1/transcribe': {body: null},
            'GET /api/v1/podcasts/episodes/internal-1/transcripts': {body: [{source: 'feed', status: 'parsed'}, {source: 'generated', status: 'parsed'}]}}))
        const view = await show()
        await findParts(view)
        await until(() => view.textContent!.includes('Needs timing'))
        expect(view.textContent).toContain('The transcript runs past the end of the file.')
        await click(view, 'Make a transcript from the file')
        await until(() => calls.some(c => c.key === 'POST /api/v1/podcasts/episodes/internal-1/transcribe'))
        await until(() => calls.filter(c => c.key === PLANS).length === 2)
        await until(() => view.textContent!.includes('12 of 58 min'))
    })

    it('shows "Timing not checked" when the plan could not check the timing against the file', async () => {
        serve(vi.stubGlobal, base({[PLANS]: [{body: plan({episodes: [{episode_id: 'public-1', title: 'BGP deep dive', origin: 'feed', timing: 'unverified'}]})},
            {body: plan({episodes: [{episode_id: 'public-1', title: 'BGP deep dive', origin: 'generated', timing: 'ok'}]})}]}))
        const view = await show()
        await findParts(view)
        await until(() => view.textContent!.includes('Timing not checked'))
        await click(view, 'Find the parts')
        await until(() => !view.textContent!.includes('Timing not checked'))
        expect(view.textContent).toContain('12 of 58 min')
    })

    it('without timing news from the plan, a publisher transcript counts as not checked', async () => {
        serve(vi.stubGlobal, base({[TRANSCRIPT]: {body: {...transcript, origin: 'feed'}}, [PLANS]: {body: plan()}}))
        const view = await show()
        await findParts(view)
        await until(() => view.textContent!.includes('Timing not checked'))
    })

    it('Smart Play on a downloaded episode: the player gets the kept ranges and starts at the first one', async () => {
        serve(vi.stubGlobal, base({[PLANS]: {body: plan()}}))
        const view = await show()
        await findParts(view)
        await click(view, 'Smart Play')
        await until(() => !!useAudioPlayer.getState().skipPlan)
        expect(useAudioPlayer.getState().skipPlan).toEqual({episodeId: 'public-1', keep: [[200, 440], [900, 1380]], label: 'BGP route selection'})
        expect(useAudioPlayer.getState().loadedPodcastEpisode?.podcastEpisode.episode_id).toBe('public-1')
        expect(useAudioPlayer.getState().pendingSeek).toBe(200)
        await click(view, 'Stop Smart Play')
        expect(useAudioPlayer.getState().skipPlan).toBeNull()
    })

    it('Smart Play on a streamed episode: "Download it first", then Download makes it possible', async () => {
        const streamed = {body: {podcastEpisode: episode({status: false, local_url: '/proxy/podcast?episodeId=public-1'}), podcastHistoryItem: null}}
        const calls = serve(vi.stubGlobal, base({[PLANS]: {body: plan()},
            [EPISODE]: [streamed, streamed, {body: {podcastEpisode: episode(), podcastHistoryItem: null}}],
            'PUT /api/v1/podcasts/public-1/episodes/download': {body: null}}))
        const view = await show()
        await findParts(view)
        await until(() => view.textContent!.includes('Download it first, so the skips land in the right place.'))
        expect(button(view, 'Smart Play')!.disabled).toBe(true)
        await click(view, 'Download')
        await until(() => calls.some(c => c.key === 'PUT /api/v1/podcasts/public-1/episodes/download'))
        await until(() => button(view, 'Smart Play')?.disabled === false, 600)
        expect(view.textContent).not.toContain('Download it first')
    })

    it('Smart Play on a streamed episode, for a user who may not download: says who can', async () => {
        serve(vi.stubGlobal, base({[PLANS]: {body: plan()}, 'GET /api/v1/users/me': {body: {role: 'user'}},
            [EPISODE]: {body: {podcastEpisode: episode({status: false}), podcastHistoryItem: null}}}))
        const view = await show()
        await findParts(view)
        await until(() => view.textContent!.includes('Only an admin or an uploader can download episodes.'))
        expect(button(view, 'Download')).toBeUndefined()
    })

    it('Export MP3: says what the export is doing, shows why it failed, and can be cancelled', async () => {
        const calls = serve(vi.stubGlobal, base({[PLANS]: {body: plan()},
            'POST /companion/plans/plan-1/render': [{status: 202, body: {job_id: 'job-1'}}, {status: 202, body: {job_id: 'job-2'}}],
            'GET /companion/jobs/job-1': [{body: job({stage: 'timing'})},
                {body: job({status: 'failed', stage: 'timing', error: "This transcript doesn't match your audio file.", detail: 'Offsets differ: 0 s and 31 s.'})}],
            'GET /companion/jobs/job-2': {body: job({id: 'job-2', stage: 'audio', progress: .1})},
            'DELETE /companion/jobs/job-2': {body: {deleted: true}}}))
        const view = await show()
        await findParts(view)
        await click(view, 'Approve and export MP3')
        await until(() => view.textContent!.includes("Checking the transcript's timing against your file… 40%"))
        await until(() => view.textContent!.includes("The export failed. This transcript doesn't match your audio file."), 600)
        expect(view.textContent).toContain('Offsets differ: 0 s and 31 s.')
        await click(view, 'Try again')
        await until(() => view.textContent!.includes("Getting the episode's audio… 10%"))
        await click(view, 'Cancel')
        await until(() => calls.some(c => c.key === 'DELETE /companion/jobs/job-2'))
        await until(() => !!button(view, 'Approve and export MP3'))
    })

    it('AI planning is patient and can be cancelled', async () => {
        let aborted = false
        vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
            const key = `${init?.method ?? 'GET'} ${path}`
            if (key === PLANS) return new Promise((_, reject) => init!.signal!.addEventListener('abort', () => {
                aborted = true
                reject(new DOMException('The operation was aborted.', 'AbortError'))
            }))
            const replies: Record<string, unknown> = {[TRANSCRIPT]: transcript, [EPISODE]: {podcastEpisode: episode(), podcastHistoryItem: null},
                [SAVED_PLANS]: [],
                'GET /companion/settings/ai': {provider: 'groq', base_url: 'x', model: 'm', max_input_chars: 60000, key_set: true, key_hint: null, configured: true}}
            return key in replies ? new Response(JSON.stringify(replies[key])) : new Response('{"detail":"Not Found"}', {status: 404})
        }))
        const view = await show()
        await until(() => view.querySelector<HTMLInputElement>('input[value="ai"]')?.disabled === false)
        await act(async () => view.querySelector<HTMLInputElement>('input[value="ai"]')!.click())
        await findParts(view)
        await until(() => view.textContent!.includes('Planning with AI… On a free AI plan this can take a few minutes.'))
        await click(view, 'Cancel')
        await until(() => view.textContent!.includes('Planning was cancelled.'))
        expect(aborted).toBe(true)
        expect(view.querySelector('[role=alert]')).toBeNull()
        expect(button(view, 'Find the parts')?.disabled).toBe(false)
    })

    it('Export MP3: progress, a failure with retry, then Download and Show index', async () => {
        const calls = serve(vi.stubGlobal, base({[PLANS]: {body: plan()},
            'POST /companion/plans/plan-1/render': [{status: 202, body: {job_id: 'job-1'}}, {status: 202, body: {job_id: 'job-2'}}],
            'GET /companion/jobs/job-1': [{body: job()}, {body: job({status: 'failed', error: 'ffmpeg stopped.'})}],
            'GET /companion/jobs/job-2': {body: job({id: 'job-2', status: 'done', progress: 1, cut_id: 'cut-1'})},
            'GET /companion/cuts/cut-1': {body: cut}}))
        const view = await show()
        await findParts(view)
        await click(view, 'Approve and export MP3')
        await until(() => view.textContent!.includes('Making your MP3… 40%'))
        expect(view.querySelector('[role=progressbar]')?.getAttribute('aria-valuenow')).toBe('40')
        await until(() => view.textContent!.includes('The export failed. ffmpeg stopped.'), 600)
        await click(view, 'Try again')
        await until(() => view.textContent!.includes('Your MP3 is ready: 12:00, 7.1 MB.'), 600)
        expect(view.textContent).toContain('Its timing was not checked against your audio file.')
        expect(calls.filter(c => c.key === 'POST /companion/plans/plan-1/render')).toHaveLength(2)
        await click(view, 'Show index')
        expect(view.textContent).toContain('0:00–4:00 in the MP3 · 3:20–7:20 in the episode')
        expect(button(view, 'Play the original at 15:00')).toBeTruthy()
        expect(button(view, 'Download MP3')).toBeTruthy()
    })

    it('the script shows the exact words it keeps and cuts, with times, why and chapters, before anything is cut', async () => {
        const seek = vi.fn()
        const removed = script()
        removed.episodes[0].parts = [{kind: 'cut', start: 0, end: 900, reason: 'removed', chapters: [], lines: script().episodes[0].parts[1].lines},
            ...script().episodes[0].parts.slice(3)]
        const calls = serve(vi.stubGlobal, base({[PLANS]: {body: plan()},
            'GET /companion/plans/plan-1/script': [{body: script()}, {body: removed}],
            'PATCH /companion/plans/plan-1': {body: plan({kept_seconds: 480, spans: plan().spans.map(s => s.id === 's1' ? {...s, enabled: false} : s)})}}))
        const view = await show(seek)
        await findParts(view)
        await until(() => view.textContent!.includes('Sponsor read'))               // the script replaced the list
        // a kept passage: its first and last lines, where the cuts fall, then every line
        expect(view.textContent).toContain('BGP picks routes by weight first.')
        expect(view.textContent).toContain('And last, the router ID breaks a tie.')
        expect(view.textContent).not.toContain('Then the origin type.')
        await click(view, 'Show 2 more lines')
        expect(view.textContent).toContain('Then the origin type.')
        expect(view.textContent).toContain('In: Route selection')
        expect(view.textContent).toContain('(part of a sentence)')
        // what is cut, and why
        expect(view.textContent).toContain('Sponsor read')
        expect(view.textContent).toContain('Says “history”, which you skip')
        expect(view.textContent).toContain('Not about “BGP route selection”')
        expect(view.textContent).toContain('“Some history of the protocol.”')
        expect(view.textContent).not.toContain('Back in 1989 it was new.')
        await click(view, 'Show the 2 lines it cuts')
        expect(view.textContent).toContain('Back in 1989 it was new.')
        await click(view, 'Play from 3:33')
        expect(seek).toHaveBeenCalledWith(213)
        // removing a passage turns it into a cut part that can be put back
        await click(view, 'Remove passage at 3:20')
        await until(() => view.textContent!.includes('You removed it'))
        expect(calls.find(c => c.key === 'PATCH /companion/plans/plan-1')?.body).toEqual({spans: [{id: 's1', enabled: false}]})
        expect(button(view, 'Put back passage at 3:20')).toBeTruthy()
        expect(button(view, 'Approve and export MP3')).toBeTruthy()
        expect(view.textContent).toContain('Export cuts exactly the words above, then listens to the MP3 to check every cut.')
    })

    it('after the export: listening, then what the listen-back check found', async () => {
        const seek = vi.fn()
        const problem = {kind: 'clipped_start', severity: 'error' as const, piece: 2, edge: 'start' as const, cut_time: 245, words: ['going', 'to', 'be'],
            seconds: 0, heard: 'doing another show', message: 'x', source_time: 905, episode_id: 'public-1', stuck: true}
        const note = {...problem, kind: 'stt_missed', severity: 'warning' as const, piece: 1, cut_time: 3, words: ['so'], stuck: false}
        serve(vi.stubGlobal, base({[PLANS]: {body: plan()},
            'POST /companion/plans/plan-1/render': [{status: 202, body: {job_id: 'job-1'}}, {status: 202, body: {job_id: 'job-2'}}],
            'GET /companion/jobs/job-1': [{body: job({stage: 'listen', progress: .7})},
                {body: job({status: 'done', progress: 1, cut_id: 'cut-1'})}],
            'GET /companion/jobs/job-2': {body: job({id: 'job-2', status: 'done', progress: 1, cut_id: 'cut-2'})},
            'GET /companion/cuts/cut-1': {body: {...cut, label: null, check: check({status: 'fixed', renders: 2, kept: 2,
                fixes: [{render: 1, piece: 2, edge: 'start', episode_id: 'public-1', from: 900.7, to: 900.1, why: 'clipped_start'}],
                findings: [note]})}},
            'GET /companion/cuts/cut-2': {body: {...cut, id: 'cut-2', label: null, check: check({status: 'problems', renders: 3, findings: [problem]})}}}))
        const view = await show(seek)
        await findParts(view)
        await click(view, 'Approve and export MP3')
        await until(() => view.textContent!.includes('Listening to your MP3 to check every cut… 70%'))
        await until(() => view.textContent!.includes('One cut was off, so it was cut again from the original'), 600)
        expect(view.textContent).toContain('Passage 2 started too late; its start moved 0.6 s earlier.')
        expect(view.textContent).toContain('Speech-to-text heard 88 of the 90 planned words')
        expect(view.textContent).toContain('1 note')
        expect(view.querySelector('[role=alert]')).toBeNull()
        // the same plan exported again, where one cut stays wrong: the problem, and the original to play
        await act(async () => {forgetWorkspaces()})
        root!.unmount()
        const again = await show(seek)
        await findParts(again)
        await click(again, 'Approve and export MP3')
        await until(() => again.textContent!.includes("Listening to the MP3 found a problem it couldn't fix:"), 600)
        expect(again.textContent).toContain('Passage 2 starts too late at 4:05: “going to be” is cut off.')
        expect(again.textContent).toContain("There's no clean place to cut here")
        expect(again.querySelector('[role=alert]')?.textContent).toContain("couldn't fix")
        await click(again, 'Play the original at 15:05')
        expect(seek).toHaveBeenCalledWith(905)
        expect(button(again, 'Download MP3')).toBeTruthy()
    })
})
