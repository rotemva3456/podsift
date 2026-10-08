// Fixtures shaped like the cuts feature's plans, jobs and cuts, and PodFetch's episode and
// SponsorBlock JSON. Smart Play builds against these until the real API is in.
import type {Cut, CutCheck, Job, Plan, Script} from '../../src/ext/features/cuts/api'
import type {PodcastEpisode} from '../../src/ext/types'

export const episode = (patch: Partial<PodcastEpisode> = {}) => ({
    id: 'internal-1', episode_id: 'public-1', podcast_id: 'show-1', name: 'BGP deep dive', description: '', total_time: 3480,
    local_url: '/podcasts/show/bgp.mp3', local_image_url: '', status: true, url: 'https://example.test/bgp.mp3',
    date_of_recording: '2026-09-01', guid: 'g1', deleted: false, episode_numbering_processed: false, image_url: '', ...patch,
}) as PodcastEpisode

export const transcript = {episode_id: 'public-1', source: 'Publisher', timed: true, text: 'BGP picks routes.', origin: 'generated',
    segments: [{id: '0', start: 0, end: 10, text: 'Welcome.'}, {id: '1', start: 200, end: 260, text: 'BGP picks routes by weight first.'}]}

export const plan = (patch: Partial<Plan> = {}): Plan => ({
    id: 'plan-1', status: 'ready', mode: 'keyword', want: 'BGP route selection', skip: 'history', minutes: 15,
    spans: [
        {id: 's1', episode_id: 'public-1', start: 200, end: 440, text: 'BGP picks routes by weight first.', why: 'matches “BGP”', enabled: true},
        {id: 's2', episode_id: 'public-1', start: 900, end: 1380, text: 'Local preference beats AS path.', why: 'matches “route”', enabled: true},
    ],
    kept_seconds: 720, source_seconds: 3480, omitted: [], needs_timing: [], created_at: '2026-09-24T10:00:00Z', ...patch,
})

export const job = (patch: Partial<Job> = {}): Job => ({id: 'job-1', status: 'running', progress: .4, error: null, cut_id: null, ...patch})

export const cut: Cut = {id: 'cut-1', plan_id: 'plan-1', duration: 720, size_bytes: 7_100_000, title: 'BGP route selection', label: 'timing not checked', index: [
    {cut_start: 0, cut_end: 240, episode_id: 'public-1', source_start: 200, source_end: 440, title: 'BGP deep dive'},
    {cut_start: 240, cut_end: 720, episode_id: 'public-1', source_start: 900, source_end: 1380, title: 'BGP deep dive'},
]}

/** The plan's script: a sponsor read, passage s1 (6 lines), a skipped aside, s2 (a partial first line), the rest. */
export const script = (patch: Partial<Script> = {}): Script => ({
    plan_id: 'plan-1', want: 'BGP route selection', skip: 'history', kept_seconds: 720, source_seconds: 3480, words: true,
    episodes: [{episode_id: 'public-1', title: 'BGP deep dive', duration: 3480, parts: [
        {kind: 'cut', start: 0, end: 200, reason: 'sponsor', chapters: ['Intro'], lines: [{start: 0, end: 12, text: 'This episode is sponsored by Acme.'}]},
        {kind: 'keep', start: 200, end: 440, span_id: 's1', why: 'matches “BGP”', chapters: ['Route selection'], lines: [
            {start: 200, end: 212, text: 'BGP picks routes by weight first.'}, {start: 213, end: 230, text: 'Then local preference.'},
            {start: 231, end: 260, text: 'Then the shortest AS path.'}, {start: 261, end: 300, text: 'Then the origin type.'},
            {start: 301, end: 360, text: 'Then MED.'}, {start: 361, end: 440, text: 'And last, the router ID breaks a tie.'}]},
        {kind: 'cut', start: 440, end: 900, reason: 'skip', chapters: ['History'], lines: [
            {start: 441, end: 470, text: 'Some history of the protocol.'}, {start: 470, end: 520, text: 'Back in 1989 it was new.'}]},
        {kind: 'keep', start: 900, end: 1380, span_id: 's2', why: 'matches “route”', chapters: [], lines: [
            {start: 900, end: 930, text: 'local preference beats AS path.', partial: true}]},
        {kind: 'cut', start: 1380, end: 3480, reason: 'other', chapters: [], lines: [{start: 1381, end: 1400, text: 'Now about OSPF.'}]},
    ]}], ...patch,
})

export const check = (patch: Partial<CutCheck> = {}): CutCheck => ({status: 'passed', reason: null, renders: 1, kept: 1, joins: 1,
    words: {expected: 90, heard: 88}, findings: [], fixes: [], ...patch})

export const sponsorSettings = {enabled: true, skipSponsor: true, skipSelfpromo: true, skipInteraction: false, skipIntro: false,
    skipOutro: false, skipPreview: false, skipFiller: false, skipMusicOfftopic: false}

export const segment = (category: string, start: number, end: number, durationMismatch = false) =>
    ({uuid: `${category}-${start}`, category, actionType: 'skip', startMs: start * 1000, endMs: end * 1000, votes: 3, locked: false, durationMismatch})

type Reply = {status?: number; body?: unknown}
/**
 * Stub fetch: `routes` maps "METHOD path" to replies used in order (the last one repeats).
 * Returns the list of requests made, with their bodies.
 */
export function serve(stub: (name: string, fn: unknown) => void, routes: Record<string, Reply | Reply[]>) {
    const calls: {key: string; body?: unknown}[] = []
    stub('fetch', async (input: string | URL | Request, init?: RequestInit) => {
        const path = typeof input === 'string' ? input : input instanceof URL ? input.pathname + input.search : input.url
        const key = `${init?.method ?? 'GET'} ${path}`
        calls.push({key, body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined})
        const entry = routes[key]
        if (!entry) return new Response(JSON.stringify({detail: 'Not Found'}), {status: 404})
        const reply = Array.isArray(entry) ? (entry.length > 1 ? entry.shift()! : entry[0]!) : entry
        return new Response(reply.body === undefined ? null : JSON.stringify(reply.body), {status: reply.status ?? 200})
    })
    return calls
}
