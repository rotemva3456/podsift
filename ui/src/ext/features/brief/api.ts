// The brief API and the badge batcher. Times are seconds from the start of the episode audio.
import {companion} from '../../../utils/companion'

export type Verdict = 'HEAR' | 'READ' | 'SKIP'
export type BriefStatus = 'ready' | 'not_generated' | 'generating' | 'failed' | 'no_transcript'
export type Citation = {segment_id: string; start: number; end: number}
export type Brief = {
    episode_id: string; status: BriefStatus
    summary: string | null; verdict: Verdict | null; verdict_reason: string | null; who_for: string | null
    chapters: {title: string; start: number}[]; chapters_source: 'ai' | 'publisher' | null
    key_ideas: {text: string; citations: Citation[]}[]
    percent_new: number | null; new_concepts: string[]; heard_count: number | null
    ads_seconds: number | null; duration: number | null
    model: string | null; created_at: string | null; error: string | null
    ai_ready: boolean; estimated_tokens: number | null
}
export type QueueItem = {episode_id: string; status: 'waiting' | 'running' | 'skipped' | BriefStatus
    verdict: Verdict | null; error: string | null}
export type Queue = {status: 'idle' | 'running' | 'done' | 'cancelled'; ai_ready: boolean; cancelled?: boolean
    total?: number; done?: number; current?: string | null; items?: QueueItem[]}
export type EstimateItem = {episode_id: string; title: string | null; input_chars: number
    status: 'will_brief' | 'no_transcript' | 'briefed' | 'unavailable'}
export type Estimate = {count: number; input_chars: number; input_tokens: number; model: string | null; items: EstimateItem[]}

export const QUEUE_MAX = 20
export const briefKey = (episodeId: string) => ['brief', episodeId] as const
export const badgeKey = (episodeId: string) => ['brief-badge', episodeId] as const
export const QUEUE_KEY = ['brief-queue'] as const

const path = (episodeId: string) => `/episodes/${encodeURIComponent(episodeId)}/brief`
const post = (body: unknown): RequestInit => ({method: 'POST', body: JSON.stringify(body)})

export const fetchBrief = (episodeId: string) => companion<Brief>(path(episodeId))
/** Starts making the brief (the answer says "generating"), or returns the cached one. Never blocks on AI. */
export const makeBrief = (episodeId: string, regenerate = false) => companion<Brief>(path(episodeId), post({regenerate}))
/** Stored briefs only: this never makes one. */
export const cachedBriefs = (episodeIds: string[]) => companion<Brief[]>(`/briefs?ids=${episodeIds.map(encodeURIComponent).join(',')}`)
export const fetchQueue = () => companion<Queue>('/briefs/queue')
export const estimateQueue = (episodeIds: string[]) => companion<Estimate>('/briefs/queue', post({episode_ids: episodeIds, dry_run: true}))
export const startQueue = (episodeIds: string[]) => companion<Queue>('/briefs/queue', post({episode_ids: episodeIds}))
export const stopQueue = () => companion<Queue>('/briefs/queue', {method: 'DELETE'})

/** Episode rows ask for their badge one by one; the ids asked within BATCH_MS go out as one request. */
export const BATCH_MS = 50
const BATCH_MAX = 100
type Waiter = {resolve: (brief: Brief | null) => void; reject: (error: unknown) => void}
let waiting = new Map<string, Waiter[]>()
let timer: ReturnType<typeof setTimeout> | undefined

export function cachedBrief(episodeId: string): Promise<Brief | null> {
    return new Promise((resolve, reject) => {
        const id = episodeId.toLowerCase()
        waiting.set(id, [...(waiting.get(id) ?? []), {resolve, reject}])
        timer ??= setTimeout(() => void flush(), BATCH_MS)
    })
}

async function flush() {
    const batch = waiting
    waiting = new Map()
    timer = undefined
    const ids = [...batch.keys()]
    for (let i = 0; i < ids.length; i += BATCH_MAX) {
        const part = ids.slice(i, i + BATCH_MAX)
        try {
            const found = new Map((await cachedBriefs(part)).map(brief => [brief.episode_id.toLowerCase(), brief]))
            part.forEach(id => batch.get(id)?.forEach(waiter => waiter.resolve(found.get(id) ?? null)))
        } catch (error) {
            part.forEach(id => batch.get(id)?.forEach(waiter => waiter.reject(error)))
        }
    }
}
