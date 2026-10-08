// Smart Play: the normal player plays a plan and skips the rest. Other features can call
// `startSmartPlay(skipPlans(plan, label))` with any cut plan.
import useAudioPlayer, {type SkipPlan, type TimeRange} from '../../../store/AudioPlayerSlice'
import {normalizeRanges} from '../../../utils/audioPlayer'
import {fetchEpisode, playEpisode} from '../../../utils/listening'
import {podfetch} from '../../shared/podfetch'
import type {Plan} from './api'

/** One skip plan per episode (its enabled spans), in the order the episodes first appear in the plan. */
export function skipPlans(plan: Pick<Plan, 'spans'>, label: string): SkipPlan[] {
    const keep = new Map<string, TimeRange[]>()
    for (const span of plan.spans) if (span.enabled) keep.set(span.episode_id, [...keep.get(span.episode_id) ?? [], [span.start, span.end]])
    return [...keep].map(([episodeId, ranges]) => ({episodeId, keep: normalizeRanges(ranges), label})).filter(p => p.keep.length > 0)
}

/** Minutes for "12 of 58 min": one decimal under 10 minutes. */
export const shortMinutes = (seconds: number) => seconds < 600 ? Math.round(seconds / 6) / 10 : Math.round(seconds / 60)

/** Seconds that a set of skip plans keeps. */
export const keptSeconds = (plans: SkipPlan[]) => plans.reduce((sum, p) => sum + p.keep.reduce((s, [a, b]) => s + b - a, 0), 0)

/**
 * Play the first plan's episode from its first kept part; the other plans follow in order. The
 * episode must be downloaded: skips are only exact on the file.
 */
export async function startSmartPlay(plans: SkipPlan[]): Promise<void> {
    const [first, ...rest] = plans
    if (!first?.keep[0]) return
    const {podcastEpisode} = await fetchEpisode(first.episodeId)
    if (!podcastEpisode.status) throw new Error('download-first')
    useAudioPlayer.getState().setSkipPlan(first, rest)
    await playEpisode(podcastEpisode, first.keep[0][0])
}

export const DOWNLOAD_POLL_MS = 3000
const DOWNLOAD_LIMIT_MS = 30 * 60 * 1000
const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

async function isDownloaded(episodeId: string): Promise<boolean> {
    const response = await podfetch(`/api/v1/episodes/${encodeURIComponent(episodeId)}`)
    if (!response.ok) throw new Error('download-error')
    return Boolean((await response.json())?.podcastEpisode?.status)
}

/**
 * Ask PodFetch to download an episode and wait until its file is there. PodFetch lets only admins
 * and uploaders download: anyone else gets 'not-allowed'. `alive` stops the wait (unmount).
 */
export async function downloadEpisode(episodeId: string, alive: () => boolean = () => true): Promise<'done' | 'not-allowed'> {
    if (await isDownloaded(episodeId)) return 'done'
    // Despite the path, PodFetch wants the episode's episode_id here.
    const response = await podfetch(`/api/v1/podcasts/${encodeURIComponent(episodeId)}/episodes/download`, {method: 'PUT'})
    if (response.status === 401 || response.status === 403) return 'not-allowed'
    if (!response.ok) throw new Error('download-error')
    const until = Date.now() + DOWNLOAD_LIMIT_MS
    while (!(await isDownloaded(episodeId))) {
        if (!alive() || Date.now() > until) throw new Error('download-error')
        await sleep(DOWNLOAD_POLL_MS)
    }
    return 'done'
}
