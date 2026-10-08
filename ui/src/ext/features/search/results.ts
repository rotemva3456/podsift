// Search results from two places, merged per episode: PodFetch's own transcript search and the
// companion's transcript library (GET /companion/search). Times are seconds from the start of the
// original episode audio; null means the transcript has no timing.
import type {components} from '../../../../schema'
import type {PodcastEpisode} from '../../types'

/** PodFetch, GET /api/v1/transcripts/search: at most 3 hits per episode, snippets mark words with <b>. */
export type PodFetchGroup = components['schemas']['TranscriptSearchGroupDto']
/** The companion, GET /companion/search (companion/routes/search.py). */
export type LibraryHit = {segment_id: string | null; start: number | null; end: number | null; text: string}
export type LibraryEpisode = {episode_id: string; id: string | null; podcast_id: string | null; title: string
    duration: number; matches: number; hits: LibraryHit[]}
export type LibraryResult = {query: string; terms: string[]; library: boolean; unmatched: number; episodes: LibraryEpisode[]}

export type Hit = {start: number | null; text: string}
/** One episode in "Inside episodes". `episode` is PodFetch's full episode when PodFetch found it. */
export type Group = {episodeId: string; title: string; podcastId: string | null; episode: PodcastEpisode | null
    duration: number; hits: Hit[]; matches: number; titleMatch: boolean}

// The same stop words as the engine (companion/engine/select.py), so both searches agree.
const STOP = new Set(('the a an and or of to in for on with is are was were be been it this that what how why when ' +
    'i you we they at as by from do does can will not no').split(' '))
const WORD = /[\p{L}\p{N}_+#.]+/gu
const LETTER = '[\\p{L}\\p{N}_]'
/** A PodFetch hit this close to a library hit is the same moment in another transcript. */
export const SAME_MOMENT_SECONDS = 3

/** The search words, as the companion reads them: lowercased, stop words dropped unless nothing else is left. */
export function queryTerms(query: string): string[] {
    const words = [...new Set((query.toLowerCase().match(WORD) ?? []).map(word => word.replace(/^\.+|\.+$/g, ''))
        .filter(word => word.length >= 2))]
    const kept = words.filter(word => !STOP.has(word))
    return kept.length ? kept : words
}

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
/** Whole words that start with a search word, like PodFetch's prefix search ("tree" marks "trees"). */
function wordPattern(terms: string[]): RegExp | null {
    if (!terms.length) return null
    const alternatives = [...terms].sort((a, b) => b.length - a.length).map(escape).join('|')
    return new RegExp(`(?<!${LETTER})(?:${alternatives})${LETTER}*`, 'giu')
}

export type Part = {text: string; mark: boolean}
/** Split text into plain and marked parts. Rendered as text, so a transcript can never inject markup. */
export function highlight(text: string, terms: string[]): Part[] {
    const pattern = wordPattern(terms)
    if (!pattern) return text ? [{text, mark: false}] : []
    const parts: Part[] = []
    let last = 0
    for (const match of text.matchAll(pattern)) {
        const at = match.index ?? 0
        if (!match[0]) continue
        if (at > last) parts.push({text: text.slice(last, at), mark: false})
        parts.push({text: match[0], mark: true})
        last = at + match[0].length
    }
    if (last < text.length) parts.push({text: text.slice(last), mark: false})
    return parts
}

/** At most `max` characters around the first match, cut at spaces, with an ellipsis where text was cut. */
export function excerpt(text: string, terms: string[], max = 220): string {
    const clean = text.replace(/\s+/g, ' ').trim()
    if (clean.length <= max) return clean
    const first = wordPattern(terms)?.exec(clean)?.index ?? 0
    let start = Math.max(0, Math.min(first - Math.floor(max / 3), clean.length - max))
    let end = start + max
    if (start > 0) {const space = clean.indexOf(' ', start); if (space !== -1 && space < first) start = space + 1}
    if (end < clean.length) {const space = clean.lastIndexOf(' ', end); if (space > first) end = space}
    return (start > 0 ? '…' : '') + clean.slice(start, end) + (end < clean.length ? '…' : '')
}

/** PodFetch marks matches with plain <b>…</b>; we highlight ourselves, so drop them. */
export const stripMarks = (snippet: string) => snippet.replace(/<\/?b>/g, '')

const byTime = (a: Hit, b: Hit) => (a.start ?? Infinity) - (b.start ?? Infinity)
const sameText = (a: string, b: string) => a.replace(/\W+/g, ' ').trim().toLowerCase() === b.replace(/\W+/g, ' ').trim().toLowerCase()
const containsAll = (text: string, terms: string[]) => terms.length > 0 && terms.every(term => text.toLowerCase().includes(term))

type Sourced = Hit & {transcript: string}
const sameMoment = (a: Hit, b: Hit) => a.start === null || b.start === null
    ? sameText(a.text, b.text) : Math.abs(a.start - b.start) < SAME_MOMENT_SECONDS

/**
 * One group per episode (keyed by its public `episode_id`), whichever search found it. The same
 * moment found in two transcripts of one episode shows once: library hits stay (they have exact
 * segment starts and ends), and a PodFetch hit is dropped when another transcript already has
 * that moment. Best first: the title names every search word, then the most matches.
 */
export function mergeGroups(podfetch: PodFetchGroup[], library: LibraryEpisode[], terms: string[],
                            title: (html: string) => string = text => text): Group[] {
    const groups = new Map<string, Omit<Group, 'hits' | 'titleMatch'> & {hits: Sourced[]}>()
    for (const found of library) {
        groups.set(found.episode_id, {episodeId: found.episode_id, title: found.title, podcastId: found.podcast_id,
            episode: null, duration: found.duration, matches: Math.max(found.matches, found.hits.length),
            hits: found.hits.map(hit => ({start: hit.start, text: hit.text, transcript: 'library'}))})
    }
    for (const found of podfetch) {
        const episode = found.episode, id = episode.episode_id
        const group = groups.get(id) ?? {episodeId: id, title: episode.name, podcastId: episode.podcast_id, episode,
            duration: episode.total_time, matches: 0, hits: []}
        group.episode = episode
        for (const hit of found.hits) {
            const moment = {start: hit.startMs == null ? null : hit.startMs / 1000, text: stripMarks(hit.snippet),
                transcript: hit.transcriptId}
            if (group.hits.some(other => other.transcript !== moment.transcript && sameMoment(other, moment))) continue
            group.hits.push(moment)
            group.matches += 1
        }
        groups.set(id, group)
    }
    return [...groups.values()]
        .map(group => ({...group, hits: group.hits.map(({start, text}) => ({start, text})).sort(byTime),
            titleMatch: containsAll(title(group.title), terms)}))
        .sort((a, b) => Number(b.titleMatch) - Number(a.titleMatch) || b.matches - a.matches
            || a.title.localeCompare(b.title))
}

/** The episode page, at a moment when there is one. */
export const learnUrl = (episodeId: string, start?: number | null) =>
    `/learn?episode=${encodeURIComponent(episodeId)}${start == null ? '' : `&at=${start}`}`
