// Search inside every episode: merging, highlighting, and every state of the /search page.
import {afterEach, beforeAll, describe, expect, it, vi} from 'vitest'
import {act} from 'react'
import {createRoot, type Root} from 'react-dom/client'
import {MemoryRouter, Route, Routes, useLocation} from 'react-router-dom'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import i18n from '../../../language/i18n'
import en from './locales/en.json'
import {excerpt, highlight, type LibraryEpisode, learnUrl, mergeGroups, type PodFetchGroup, queryTerms} from './results'
import type {PodcastEpisode} from '../../types'

const mocks = vi.hoisted(() => ({play: vi.fn(async (_episode: unknown, _position?: number) => {})}))
vi.mock('../../../utils/http', () => ({client: {GET: async () => ({data: []})}, $api: {}, apiURL: '', uiURL: ''}))
vi.mock('../../registry', () => ({rowBadges: []}))
vi.mock('../../../utils/listening', async importOriginal => ({
    ...await importOriginal<typeof import('../../../utils/listening')>(), playEpisode: mocks.play}))
const {SearchPage} = await import('./SearchPage')

const PODCAST = 'show-1'
const dto = (n: number, name = `Episode ${n}`) => ({id: `internal-${n}`, episode_id: `public-${n}`, podcast_id: PODCAST,
    name, description: '', total_time: 600, local_url: `/proxy/podcast?episodeId=public-${n}`,
    local_image_url: `/img/${n}.jpg`, image_url: '', url: `https://example.test/${n}.mp3`, status: true,
    date_of_recording: '2026-09-01'}) as unknown as PodcastEpisode
const podfetchGroup = (n: number, hits: [number | null, string, string?][]): PodFetchGroup => ({episodeId: `internal-${n}`,
    episode: dto(n), hits: hits.map(([seconds, snippet, transcriptId = 't-feed']) =>
        ({transcriptId, startMs: seconds === null ? null : seconds * 1000, snippet, rank: 1}))})
const libraryEpisode = (n: number, hits: [number | null, string][], matches = hits.length, title = `Episode ${n}`): LibraryEpisode =>
    ({episode_id: `public-${n}`, id: `internal-${n}`, podcast_id: PODCAST, title, duration: 600, matches,
        hits: hits.map(([start, text], i) => ({segment_id: `seg-${i}`, start, end: start === null ? null : start + 5, text}))})

describe('search results', () => {
    it('reads the search words the way the companion does', () => {
        expect(queryTerms('What is a Spanning-Tree?')).toEqual(['spanning', 'tree'])
        expect(queryTerms('802.1Q trunk, c++')).toEqual(['802.1q', 'trunk', 'c++'])
        expect(queryTerms('what is it')).toEqual(['what', 'is', 'it'])
        expect(queryTerms('über café über')).toEqual(['über', 'café'])
    })

    it('highlights whole words that start with a search word, and nothing inside a word', () => {
        const marked = (text: string, terms: string[]) => highlight(text, terms).filter(p => p.mark).map(p => p.text)
        expect(marked('Spanning-tree and TREES, not a subtree.', ['spanning', 'tree'])).toEqual(['Spanning', 'tree', 'TREES'])
        expect(marked('I like C++ a lot', ['c++'])).toEqual(['C++'])
        expect(highlight('A <b>tag</b> stays text.', ['tag']).map(p => p.text).join('')).toBe('A <b>tag</b> stays text.')
        expect(highlight('Nothing to mark.', [])).toEqual([{text: 'Nothing to mark.', mark: false}])
    })

    it('cuts a long passage around its first match', () => {
        const text = 'word '.repeat(80) + 'the spanning tree protocol ' + 'more '.repeat(80)
        const cut = excerpt(text, ['spanning'], 120)
        expect(cut).toContain('spanning tree')
        expect(cut.startsWith('…') && cut.endsWith('…')).toBe(true)
        expect(cut.length).toBeLessThanOrEqual(122)
        expect(excerpt('  Short   text. ', ['short'])).toBe('Short text.')
    })

    it('groups both searches by episode, once each, with every moment in time order', () => {
        const groups = mergeGroups([
            podfetchGroup(1, [[10.5, 'The <b>spanning</b> tree, again.'], [70, 'A <b>spanning</b> tree later.']]),
            podfetchGroup(2, [[5, 'One', 't-feed'], [5.5, 'One', 't-whisper'], [90, 'Two', 't-feed'], [91, 'Two more', 't-feed']]),
        ], [
            libraryEpisode(1, [[40, 'Spanning tree in the middle.'], [10, 'The spanning tree, first.']], 12),
            libraryEpisode(3, [[20, 'Spanning tree.']], 1, 'Spanning Tree explained'),
        ], ['spanning', 'tree'])
        expect(groups.map(g => [g.episodeId, g.matches, g.titleMatch])).toEqual([
            ['public-3', 1, true],      // the title names every search word
            ['public-1', 13, false],    // 12 in the library, plus one PodFetch moment the library didn't have
            ['public-2', 3, false]])    // the same moment in PodFetch's feed and Whisper transcripts counts once
        const one = groups[1]!
        expect(one.hits.map(h => [h.start, h.text])).toEqual([
            [10, 'The spanning tree, first.'], [40, 'Spanning tree in the middle.'], [70, 'A spanning tree later.']])
        expect(one.episode?.episode_id).toBe('public-1')   // PodFetch's episode, ready to play
        expect(groups[0]!.episode).toBeNull()               // library only: the page loads it on Play
        expect(groups[2]!.hits.map(h => h.start)).toEqual([5, 90, 91])
        expect(mergeGroups([podfetchGroup(4, [[null, 'Untimed <b>text</b>.']])], [libraryEpisode(4, [[null, 'Untimed text.']])], ['text'])[0]!.hits)
            .toEqual([{start: null, text: 'Untimed text.'}])
    })

    it('links to the episode page at the moment', () => {
        expect(learnUrl('public 1', 30.66)).toBe('/learn?episode=public%201&at=30.66')
        expect(learnUrl('public-1', null)).toBe('/learn?episode=public-1')
    })
})

// ── the page ──────────────────────────────────────────────────────────────────

type Reply = {status: number; body?: unknown}
let root: Root | undefined
let location = ''

beforeAll(() => {
    (globalThis as {IS_REACT_ACT_ENVIRONMENT?: boolean}).IS_REACT_ACT_ENVIRONMENT = true
    i18n.addResourceBundle('en', 'search', en, true, true)
})
afterEach(() => {act(() => root?.unmount()); root = undefined; document.body.innerHTML = ''; vi.unstubAllGlobals(); mocks.play.mockClear()})

/** Answer each request from `routes` (path → replies used in order; the last one repeats). */
function serve(routes: Record<string, Reply[]>) {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: string) => {
        const path = String(input)
        calls.push(path)
        const replies = routes[path]
        if (!replies) throw new Error('unexpected request ' + path)
        const reply = replies.length > 1 ? replies.shift()! : replies[0]!
        return new Response(JSON.stringify(reply.body ?? null), {status: reply.status})
    }))
    return calls
}

const q = (text: string) => encodeURIComponent(text)
function searchRoutes(query: string, {titles = [] as unknown, podfetch = [] as unknown, library = {} as object} = {}) {
    return {
        [`/api/v1/podcasts/${q(query)}/query`]: [{status: 200, body: titles}],
        [`/api/v1/transcripts/search?q=${q(query)}&page=0`]: [{status: 200, body: podfetch}],
        [`/companion/search?q=${q(query)}`]: [{status: 200, body: {query, terms: queryTerms(query), library: true,
            unmatched: 0, episodes: [], ...library}}],
        '/api/v1/podcasts': [{status: 200, body: [{id: PODCAST, name: 'Networking Show', image_url: '/show.jpg'}]}],
    } as Record<string, Reply[]>
}

function Where() {location = useLocation().search; return null}
async function show(at: string) {
    const container = document.body.appendChild(document.createElement('div'))
    const client = new QueryClient({defaultOptions: {queries: {retry: false}}})
    root = createRoot(container)
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[at]}><Where/>
        <Routes><Route path="/search" element={<SearchPage/>}/></Routes></MemoryRouter></QueryClientProvider>))
    return container
}
async function until(check: () => boolean) {
    for (let i = 0; i < 200 && !check(); i++) await act(() => new Promise(resolve => setTimeout(resolve, 10)))
    expect(check()).toBe(true)
}
const byText = (root: ParentNode, selector: string, text: string) =>
    [...root.querySelectorAll<HTMLElement>(selector)].find(el => el.textContent?.includes(text))
const click = async (el: HTMLElement | undefined) => {expect(el).toBeTruthy(); await act(async () => el!.click())}

describe('the search page', () => {
    it('before a search: explains what it does, and asks nothing of the servers', async () => {
        const calls = serve({})
        const view = await show('/search')
        expect(view.querySelector('h1')?.textContent).toBe('Search')
        expect(view.textContent).toContain('Search inside every episode')
        expect(view.querySelector<HTMLInputElement>('input[type=search]')?.getAttribute('aria-label')).toBe('Search every episode')
        expect(calls).toEqual([])
    })

    it('Enter runs the search: the words go into the address', async () => {
        const calls = serve(searchRoutes('bgp'))
        const view = await show('/search')
        const input = view.querySelector<HTMLInputElement>('input[type=search]')!
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '  bgp ')
            input.dispatchEvent(new Event('input', {bubbles: true}))
        })
        await act(async () => view.querySelector('form')!.requestSubmit())
        expect(location).toBe('?q=bgp')
        await until(() => calls.includes('/companion/search?q=bgp'))
    })

    it('groups moments by episode, highlights the words, and plays or opens each one', async () => {
        const routes = searchRoutes('spanning tree', {
            podfetch: [podfetchGroup(1, [[10.5, 'The <b>spanning</b> <b>tree</b> again.'], [70, 'A <b>spanning</b> <b>tree</b> later.']])],
            library: {episodes: [libraryEpisode(1, [[10, 'The spanning tree, first.']], 1),
                libraryEpisode(2, [[30.66, 'We continue with Spanning Tree.']], 1)]},
            titles: [dto(1), dto(5, 'Spanning Tree Part 1'), dto(6, 'Spanning Tree Part 2'), dto(7, 'Part 3'), dto(8, 'Part 4')],
        })
        routes['/api/v1/episodes/public-2'] = [{status: 200, body: {podcastEpisode: dto(2), podcastHistoryItem: null}}]
        const calls = serve(routes)
        const view = await show('/search?q=spanning%20tree')
        await until(() => !!view.querySelector('.search-group'))
        expect(view.querySelector('[role=status]')?.textContent).toBe('2 episodes mention “spanning tree”')
        expect([...view.querySelectorAll('h2')].map(h => h.textContent)).toEqual(['Inside episodes', 'Episodes'])
        expect(view.querySelectorAll('.episode-row')).toHaveLength(3)
        expect(view.querySelector('.episode-row[data-episode="internal-1"]')).toBeNull()   // already under "Inside episodes"
        await click(byText(view, 'button', 'Show all 4 episodes'))
        expect(view.querySelectorAll('.episode-row')).toHaveLength(4)

        const groups = [...view.querySelectorAll<HTMLElement>('.search-group')]
        expect(groups.map(g => g.dataset.episode)).toEqual(['public-1', 'public-2'])   // one group per episode
        const first = groups[0]!
        expect(first.querySelector('.episode-show')?.textContent).toBe('Networking Show')
        expect(first.querySelector('h3')?.textContent).toBe('Episode 1')
        expect([...first.querySelectorAll('.search-time')].map(s => s.textContent)).toEqual(['0:10', '1:10'])
        expect([...first.querySelectorAll('mark')].map(m => m.textContent)).toEqual(['spanning', 'tree', 'spanning', 'tree'])
        expect(first.querySelector('.search-hit p')?.innerHTML).toBe('The <mark>spanning</mark> <mark>tree</mark>, first.')

        const open = [...groups[1]!.querySelectorAll<HTMLAnchorElement>('a')].find(a => a.textContent === 'Open transcript here')
        expect(open?.getAttribute('href')).toBe('/learn?episode=public-2&at=30.66')
        expect(open?.getAttribute('aria-label')).toBe('Open transcript here: 0:30 in Episode 2')

        await click(byText(first, 'button', 'Play from here'))
        expect(mocks.play).toHaveBeenLastCalledWith(expect.objectContaining({episode_id: 'public-1'}), 10)
        await click(byText(groups[1]!, 'button', 'Play from here'))                   // library only: load, then play
        await until(() => mocks.play.mock.calls.length === 2)
        expect(calls).toContain('/api/v1/episodes/public-2')
        expect(mocks.play).toHaveBeenLastCalledWith(expect.objectContaining({episode_id: 'public-2', local_url: '/proxy/podcast?episodeId=public-2'}), 30.66)
    })

    it('shows at most 3 moments per episode until asked, and says how many more the transcript has', async () => {
        const hits: [number, string][] = [0, 1, 2, 3, 4].map(n => [n * 60, `Subnet number ${n}.`])
        serve(searchRoutes('subnet', {library: {episodes: [libraryEpisode(1, hits, 9)]}}))
        const view = await show('/search?q=subnet')
        await until(() => !!view.querySelector('.search-group'))
        expect(view.querySelectorAll('.search-hit')).toHaveLength(3)
        expect(view.querySelector('.episode-meta')?.textContent).toContain('9 moments')
        expect(byText(view, 'a', '4 more in the transcript')?.getAttribute('href')).toBe('/learn?episode=public-1')
        await click(byText(view, 'button', 'Show 2 more'))
        expect(view.querySelectorAll('.search-hit')).toHaveLength(5)
        expect(byText(view, 'button', 'Show fewer')).toBeTruthy()
    })

    it('empty: says nothing was found, and how to get more', async () => {
        serve(searchRoutes('zzqx', {library: {unmatched: 2}}))
        const view = await show('/search?q=zzqx')
        await until(() => view.textContent!.includes('Nothing found for “zzqx”'))
        expect(view.textContent).toContain('Only episodes with a transcript can be searched inside')
        expect(view.textContent).toContain('2 more episodes in your transcript library match, but they aren\'t in your podcasts.')
        expect(view.querySelector('.search-group')).toBeNull()
    })

    it('error: when every search fails it says so, and Try again runs them again', async () => {
        const routes = searchRoutes('vlan', {library: {episodes: [libraryEpisode(1, [[12, 'A VLAN tag.']])]}})
        for (const path of Object.keys(routes).filter(path => path !== '/api/v1/podcasts')) routes[path]!.unshift({status: 503})
        serve(routes)
        const view = await show('/search?q=vlan')
        await until(() => view.textContent!.includes('Search isn\'t working right now'))
        expect(view.querySelector('[role=alert]')?.textContent).toContain('Check your connection and try again.')
        await click(byText(view, 'button', 'Try again'))
        await until(() => !!view.querySelector('.search-group'))
        expect(view.querySelector('mark')?.textContent).toBe('VLAN')
    })

    it('partial error: shows what was found and offers to retry the part that failed', async () => {
        const routes = searchRoutes('vlan', {library: {episodes: [libraryEpisode(1, [[12, 'A VLAN tag.']])]}})
        routes['/api/v1/transcripts/search?q=vlan&page=0']!.unshift({status: 500})
        const calls = serve(routes)
        const view = await show('/search?q=vlan')
        await until(() => !!view.querySelector('.search-group'))
        const alert = view.querySelector('[role=alert]')
        expect(alert?.textContent).toContain('Some transcripts couldn\'t be searched.')
        await click(byText(alert!, 'button', 'Try again'))
        await until(() => !view.querySelector('[role=alert]'))
        expect(calls.filter(path => path.startsWith('/api/v1/transcripts/search'))).toHaveLength(2)
    })

    it('a moment that fails to start says so', async () => {
        const routes = searchRoutes('vlan', {library: {episodes: [libraryEpisode(2, [[12, 'A VLAN tag.']])]}})
        routes['/api/v1/episodes/public-2'] = [{status: 404}]
        serve(routes)
        const view = await show('/search?q=vlan')
        await until(() => !!view.querySelector('.search-group'))
        await click(byText(view, 'button', 'Play from here'))
        await until(() => view.textContent!.includes('This episode couldn\'t play. Try again.'))
        expect(mocks.play).not.toHaveBeenCalled()
    })
})
