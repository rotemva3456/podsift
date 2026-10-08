import type {components} from '../../../../schema'
import {companion, type Transcript} from '../../../utils/companion'
import {podfetch} from '../../shared/podfetch'
import type {LearningMode} from '../cuts/api'

export type Subject = {title: string; start: number}
export type CachedBrief = {episode_id: string; chapters: Subject[]; chapters_source: 'ai' | 'publisher' | null}
export type PublicPlan = {
    id: string; want: string; created_at: string; learning_mode?: LearningMode
    episodes?: {episode_id: string}[]
    spans?: {episode_id: string}[]
}
export type AgentRange = {start_id: string; end_id: string; why: string; relevance: 3}
export type AgentPlanRequest = {
    episode_ids: string[]; want: string; skip_ads: boolean; mode: 'agent'; learning_mode?: LearningMode
    selections: {episode_id: string; transcript_digest: string; keep: AgentRange[]; skip: AgentRange[]}[]
}

export const cachedSubjects = async (episodeId: string) => {
    const briefs = await companion<CachedBrief[]>(`/briefs?ids=${encodeURIComponent(episodeId)}`)
    return briefs[0]?.chapters ?? []
}

export async function publisherSubjects(internalEpisodeId: string): Promise<Subject[]> {
    const response = await podfetch(`/api/v1/podcasts/episodes/${encodeURIComponent(internalEpisodeId)}/chapters`)
    if (!response.ok) return []
    const chapters = await response.json() as components['schemas']['PodcastChapterDto'][]
    return chapters.map(chapter => ({title: chapter.title, start: chapter.startTime}))
}

export const listEpisodePlans = (episodeId: string) =>
    companion<PublicPlan[]>(`/plans?episode_id=${encodeURIComponent(episodeId)}&limit=20`)

export const createAgentPlan = (request: AgentPlanRequest, signal?: AbortSignal) =>
    companion<PublicPlan>('/plans', {method: 'POST', body: JSON.stringify(request), signal})

export function planHasEpisode(plan: PublicPlan, episodeId: string): boolean {
    const ids = plan.episodes?.map(item => item.episode_id) ?? plan.spans?.map(item => item.episode_id) ?? []
    return ids.includes(episodeId)
}

export function exactRanges(transcript: Transcript, selected: Set<number>, picked: boolean, why: string): AgentRange[] {
    const indexes = transcript.segments.map((_, index) => index).filter(index => selected.has(index) === picked)
    const ranges: AgentRange[] = []
    for (const index of indexes) {
        const segment = transcript.segments[index]
        if (!segment?.id) continue
        const previous = ranges.at(-1)
        const previousIndex = index - 1
        if (previous && previous.end_id === transcript.segments[previousIndex]?.id) previous.end_id = segment.id
        else ranges.push({start_id: segment.id, end_id: segment.id, why, relevance: 3})
    }
    return ranges
}
