import {type FormEvent, useEffect, useState} from 'react'
import {useTranslation} from 'react-i18next'
import {Link, useSearchParams} from 'react-router-dom'
import {useQuery, useQueryClient} from '@tanstack/react-query'
import {BookOpen, Play, Search} from 'lucide-react'
import {Button, buttonVariants} from '../../../components/ui/button'
import {Input} from '../../../components/ui/input'
import {ListenLoading, ListenState} from '../../../components/ListenState'
import {ListenEpisodeRow} from '../../../components/ListenEpisodeRow'
import {companion} from '../../../utils/companion'
import {clock, fetchEpisode, minutes, plainText, playEpisode, type Show} from '../../../utils/listening'
import {podfetch} from '../../shared/podfetch'
import type {PodcastEpisode} from '../../types'
import {excerpt, type Group, highlight, learnUrl, type LibraryResult, mergeGroups, type PodFetchGroup, queryTerms} from './results'
import './search.css'

const NS = 'search'
const PREVIEW_HITS = 3
const PREVIEW_TITLES = 3
const MAX_QUERY = 200
// React escapes what it renders, so i18next must not escape the query or a title a second time.
const RAW = {interpolation: {escapeValue: false}}
const tidy = (text: string) => text.replace(/\s+/g, ' ').trim().slice(0, MAX_QUERY)

async function getJson<T>(path: string): Promise<T> {
    const response = await podfetch(path)
    if (!response.ok) throw new Error(`${response.status} ${path}`)
    return await response.json() as T
}

/** /search?q= — every moment a transcript says it ("Inside episodes"), then other episodes whose title matches ("Episodes"). */
export function SearchPage() {
    const {t} = useTranslation(NS)
    const [params, setParams] = useSearchParams()
    const query = tidy(params.get('q') ?? '')
    const [draft, setDraft] = useState(query)
    useEffect(() => setDraft(query), [query])
    const submit = (event: FormEvent) => {
        event.preventDefault()
        const next = tidy(draft)
        setParams(next ? {q: next} : {})
    }
    return <>
        <div className="page-title"><h1>{t('title')}</h1><p>{t('subtitle')}</p></div>
        <form role="search" className="search-form" onSubmit={submit}>
            <div className="search-box"><Search size={16} aria-hidden="true"/>
                <Input type="search" enterKeyHint="search" aria-label={t('input-label')} placeholder={t('placeholder')}
                    maxLength={MAX_QUERY} autoFocus={!query} value={draft} onChange={event => setDraft(event.target.value)}/></div>
            <Button type="submit">{t('submit')}</Button>
        </form>
        {query ? <Results key={query} query={query}/> : <ListenState title={t('start-title')}>{t('start-body')}</ListenState>}
    </>
}

function Problem({text, retry}: {text: string; retry: () => void}) {
    const {t} = useTranslation(NS)
    return <p role="alert" className="search-problem">{text} <Button variant="link" onClick={retry}>{t('try-again')}</Button></p>
}

function Results({query}: {query: string}) {
    const {t} = useTranslation(NS)
    const terms = queryTerms(query)
    const q = encodeURIComponent(query)
    const titles = useQuery({queryKey: ['search', 'titles', query],
        queryFn: () => getJson<PodcastEpisode[]>(`/api/v1/podcasts/${q}/query`)})
    const inside = useQuery({queryKey: ['search', 'transcripts', query],
        queryFn: () => getJson<PodFetchGroup[]>(`/api/v1/transcripts/search?q=${q}&page=0`)})
    const library = useQuery({queryKey: ['search', 'library', query], queryFn: () => companion<LibraryResult>(`/search?q=${q}`)})
    const shows = useQuery({queryKey: ['search', 'shows'], queryFn: () => getJson<Show[]>('/api/v1/podcasts'), staleTime: 60_000})
    const [allTitles, setAllTitles] = useState(false)
    if (titles.isPending || inside.isPending || library.isPending) return <ListenLoading/>
    if (titles.isError && inside.isError && library.isError) {
        const retry = () => {void titles.refetch(); void inside.refetch(); void library.refetch()}
        return <ListenState title={t('error-title')} retry={retry}>{t('error-body')}</ListenState>
    }
    const showOf = (id?: string | null) => shows.data?.find(show => show.id === id)
    const groups = mergeGroups(inside.data ?? [], library.data?.episodes ?? [], terms, plainText)
    // Title matches that "Inside episodes" already lists (title matches rank first there) show once.
    const listed = new Set(groups.map(group => group.episodeId))
    const found = titles.data ?? [], more = found.filter(episode => !listed.has(episode.episode_id))
    const unmatched = library.data?.unmatched ?? 0
    const titlesProblem = titles.isError && <Problem text={t('titles-failed')} retry={() => void titles.refetch()}/>
    const insideProblems = <>
        {inside.isError && <Problem text={t('podfetch-failed')} retry={() => void inside.refetch()}/>}
        {library.isError && <Problem text={t('library-failed')} retry={() => void library.refetch()}/>}
    </>
    const unmatchedNote = unmatched > 0 && <p className="search-note">{t('unmatched', {count: unmatched})}</p>
    if (!groups.length && !found.length) return <>
        {titlesProblem}{insideProblems}
        <ListenState title={t('empty-title', {query, ...RAW})}>{t('empty-body')}</ListenState>
        {unmatchedNote}
    </>
    return <>
        <p className="search-summary" role="status">{groups.length ? t('summary', {count: groups.length, query, ...RAW})
            : t('summary-titles', {count: found.length, query, ...RAW})}</p>
        <section className="search-section" aria-labelledby="search-inside">
            <h2 id="search-inside">{t('inside')}</h2>
            {insideProblems}
            {groups.length ? groups.map(group => <ResultGroup key={group.episodeId} group={group} terms={terms}
                show={showOf(group.podcastId ?? group.episode?.podcast_id)}/>)
                : <p className="search-none">{t('inside-none', {query, ...RAW})}</p>}
            {unmatchedNote}
        </section>
        {(more.length > 0 || titles.isError) && <section className="search-section" aria-labelledby="search-episodes">
            <h2 id="search-episodes">{t('episodes')}</h2>
            <p className="search-hint">{t('episodes-hint')}</p>
            {titlesProblem}
            {(allTitles ? more : more.slice(0, PREVIEW_TITLES)).map(episode =>
                <ListenEpisodeRow key={episode.episode_id} episode={episode} show={showOf(episode.podcast_id)}/>)}
            {!allTitles && more.length > PREVIEW_TITLES && <Button variant="outline" className="search-more"
                onClick={() => setAllTitles(true)}>{t('show-all-episodes', {count: more.length})}</Button>}
        </section>}
    </>
}

function ResultGroup({group, show, terms}: {group: Group; show?: Show; terms: string[]}) {
    const {t} = useTranslation(NS)
    const cache = useQueryClient()
    const [open, setOpen] = useState(false)
    const [starting, setStarting] = useState<number | null>(null)
    const [failed, setFailed] = useState(false)
    const title = plainText(group.title)
    const hits = open ? group.hits : group.hits.slice(0, PREVIEW_HITS)
    const hidden = group.hits.length - hits.length
    const beyond = group.matches - group.hits.length
    const play = async (start: number) => {
        setStarting(start)
        setFailed(false)
        try {
            // PodFetch's search returns the whole episode; a library hit loads it the way the episode page does.
            const episode = group.episode ?? (await cache.fetchQuery({queryKey: ['listen-episode', group.episodeId],
                queryFn: () => fetchEpisode(group.episodeId)})).podcastEpisode
            await playEpisode(episode, start)
        } catch {
            setFailed(true)
        } finally {
            setStarting(null)
        }
    }
    return <article className="search-group" data-episode={group.episodeId}>
        <img className="episode-cover" src={group.episode?.local_image_url || show?.image_url} alt="" loading="lazy"/>
        <div className="search-group-head">
            {show && <p className="episode-show">{plainText(show.name)}</p>}
            <h3><Link className="episode-title" to={learnUrl(group.episodeId)}>{title}</Link></h3>
            <p className="episode-meta">{minutes(group.duration)} · {t('moments', {count: group.matches})}</p>
        </div>
        <ol className="search-hits">{hits.map((hit, index) => {
            const start = hit.start, time = start === null ? '' : clock(start)
            return <li className="search-hit" key={`${start}-${index}`}>
                <span className="search-time">{start === null ? t('no-time') : time}</span>
                <p>{highlight(excerpt(hit.text, terms), terms).map((part, n) => part.mark ? <mark key={n}>{part.text}</mark> : part.text)}</p>
                <div className="search-hit-actions">
                    {start !== null && <Button size="sm" variant="ghost" disabled={starting !== null} onClick={() => void play(start)}
                        aria-label={t('play-from-label', {time, title, ...RAW})}><Play/>{starting === start ? t('starting') : t('play-from-here')}</Button>}
                    <Link className={buttonVariants({size: 'sm', variant: 'ghost'})} to={learnUrl(group.episodeId, start)}
                        aria-label={start === null ? t('open-untimed-label', {title, ...RAW}) : t('open-label', {time, title, ...RAW})}>
                        <BookOpen/>{start === null ? t('open-transcript') : t('open-transcript-here')}</Link>
                </div>
            </li>
        })}</ol>
        {(hidden > 0 || open || beyond > 0 || failed) && <div className="search-group-foot">
            {(hidden > 0 || open) && <Button variant="link" aria-expanded={open} onClick={() => setOpen(!open)}>
                {open ? t('show-fewer') : t('show-more', {count: hidden})}</Button>}
            {beyond > 0 && <Link className="search-note-link" to={learnUrl(group.episodeId)}>{t('more-in-transcript', {count: beyond})}</Link>}
            {failed && <p role="alert" className="search-problem">{t('play-failed')}</p>}
        </div>}
    </article>
}
