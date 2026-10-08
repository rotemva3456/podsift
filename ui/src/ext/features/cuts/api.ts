// The cut API and the PodFetch calls Smart Play needs.
// Times are seconds from the start of the episode's downloaded file.
import {companion, type Transcript} from '../../../utils/companion'
import {podfetch} from '../../shared/podfetch'

export type Mode = 'keyword' | 'ai'
export type LearningMode = 'balanced' | 'focus' | 'chill'
export type Span = {id: string; episode_id: string; start: number; end: number; text: string; why: string; enabled: boolean; title?: string}
export type Plan = {
    id: string; status: 'ready' | 'needs_timing' | 'empty'; mode: Mode | 'agent'; learning_mode?: LearningMode; want: string; skip: string | null; minutes: number | null
    spans: Span[]; kept_seconds: number; source_seconds: number
    omitted: {episode_id: string; start: number; end: number; reason: string}[]
    needs_timing: {episode_id: string; reason: string}[]; created_at: string
    /** Adds each episode's title and whether its timing was checked against the file ('ok'). */
    episodes?: {episode_id: string; title?: string; origin?: string | null; timing?: 'ok' | 'mismatch' | 'unverified' | null}[]
}
type PlanRequestBase = {want: string; minutes?: number; skip_ads: boolean; learning_mode?: LearningMode}
export type AgentRange = {start_id: string; end_id: string; why: string; relevance?: 1 | 2 | 3}
export type AgentSelection = {episode_id: string; transcript_digest: string; keep: AgentRange[]; skip?: AgentRange[]}
export type SearchPlanRequest = PlanRequestBase & {
    episode_ids?: string[]; source?: 'queue'; skip?: string; mode: Mode; selections?: never
}
/** A connected agent can submit its transcript-version-bound choices without using the app's AI provider. */
export type AgentPlanRequest = PlanRequestBase & {
    episode_ids: string[]; source?: never; skip?: never; mode: 'agent'; selections: AgentSelection[]
}
export type PlanRequest = SearchPlanRequest | AgentPlanRequest

/** One line of the script: a sentence (or the part of one the cut keeps, `partial`), times in the episode. */
export type ScriptLine = {start: number; end: number; text: string; partial?: boolean}
export type CutReason = 'removed' | 'sponsor' | 'skip' | 'budget' | 'other'
export type ScriptPart = {kind: 'keep' | 'cut'; start: number; end: number; lines: ScriptLine[]; chapters: string[]
    span_id?: string; why?: string; reason?: CutReason}
/** GET /plans/{id}/script: every word the cut keeps and leaves out. `words` is false for a plan made before plans kept them. */
export type Script = {plan_id: string; want: string; skip: string; kept_seconds: number; source_seconds: number; words: boolean
    episodes: {episode_id: string; title: string; duration: number; parts: ScriptPart[]}[]}

export type JobStage = 'waiting' | 'audio' | 'timing' | 'render' | 'listen' | 'fix' | 'done'
/** Adds `stage` (what the export is doing) and `detail` (the reason behind a failure). */
export type Job = {id: string; status: 'queued' | 'running' | 'done' | 'failed'; progress: number; error: string | null; cut_id: string | null
    stage?: JobStage | null; detail?: string | null}
export type CutIndexEntry = {cut_start: number; cut_end: number; episode_id: string; source_start: number; source_end: number; title: string}
/** One thing the listen-back check found; `cut_time` is in the MP3, `source_time` in the episode. */
export type CheckFinding = {kind: string; severity: 'error' | 'warning'; piece: number; edge: 'start' | 'end' | null; cut_time: number
    words: string[]; seconds: number; heard: string; message: string; source_time?: number; episode_id?: string; stuck?: boolean}
/** A re-cut: one edge of passage `piece` moved from `from` to `to` (seconds in the episode). */
export type CheckFix = {render: number; piece: number; edge: 'start' | 'end'; episode_id: string; from: number; to: number; why: string}
/** The export's listen-back check. `passed`/`fixed`: every cut is right; `problems`: some aren't; `audio_only`: no speech-to-text. */
export type CutCheck = {status: 'passed' | 'fixed' | 'problems' | 'audio_only'; reason: string | null; renders: number; kept: number
    joins: number; words: {expected: number; heard: number} | null; findings: CheckFinding[]; fixes: CheckFix[]}
/** `label` is "timing not checked" when a publisher transcript could not be checked against the file. */
export type Cut = {id: string; plan_id: string; duration: number; size_bytes: number; index: CutIndexEntry[]; title?: string; label?: string | null
    check?: CutCheck | null}
export type Ads = {episode_id: string; spans: {start: number; end: number; label: string}[]}
export type NativeSkipSpan = {start: number; end: number; label: string; category: string; reason: string; segment_ids: string[]}
/** Our own transcript-derived skips. No community timing database is involved. */
export type NativeSkips = {
    episode_id: string; source: 'podsift-transcript'; origin: string | null; timed: boolean
    transcript_digest: string; duration: number | null
    timing_status: 'matched' | 'unverified' | 'mismatch' | 'missing'; auto_skip_safe: boolean
    spans: NativeSkipSpan[]
}
export type SkipPreferences = {manual_categories: string[]; minimum_seconds: number}
export const getSkipPreferences = () => companion<SkipPreferences>('/settings/skipping')
export const putSkipPreferences = (settings: SkipPreferences) => companion<SkipPreferences>('/settings/skipping',
    {method: 'PUT', body: JSON.stringify(settings)})
export const SKIP_PREFERENCES_KEY = ['cuts', 'skip-preferences']
/** The transcript JSON; adds `origin` (where its timing came from). */
export type TimedTranscript = Transcript & {origin?: 'feed' | 'generated' | 'library'}
/** AI settings (the key itself is never returned); `configured` says a provider is ready. */
export type AiSettings = {provider: string; base_url: string; model: string; max_input_chars: number; key_set: boolean; key_hint: string | null
    configured?: boolean}

/** AI mode can take minutes on a free tier (the provider waits out its rate limit): `signal` cancels it. */
export const createPlan = (request: PlanRequest, signal?: AbortSignal) =>
    companion<Plan>('/plans', {method: 'POST', body: JSON.stringify(request), signal})
export const listPlans = (episodeId: string) => companion<Plan[]>(`/plans?episode_id=${encodeURIComponent(episodeId)}&limit=20`)
export const getPlan = (id: string) => companion<Plan>(`/plans/${encodeURIComponent(id)}`)
export const getScript = (id: string) => companion<Script>(`/plans/${encodeURIComponent(id)}/script`)
export async function updatePlan(id: string, change: {spans?: {id: string; enabled: boolean}[]; context_seconds?: number}): Promise<Plan> {
    const updated = await companion<Plan | null>(`/plans/${encodeURIComponent(id)}`, {method: 'PATCH', body: JSON.stringify(change)})
    return updated?.spans ? updated : getPlan(id)
}
export const startRender = (planId: string) => companion<{job_id: string}>(`/plans/${encodeURIComponent(planId)}/render`, {method: 'POST'})
export const getJob = (id: string) => companion<Job>(`/jobs/${encodeURIComponent(id)}`)
export const cancelJob = (id: string) => companion<{deleted: boolean}>(`/jobs/${encodeURIComponent(id)}`, {method: 'DELETE'})
export const getCut = (id: string) => companion<Cut>(`/cuts/${encodeURIComponent(id)}`)
export const getAds = (episodeId: string) => companion<Ads>(`/episodes/${encodeURIComponent(episodeId)}/ads`)
export const getSkipSegments = (episodeId: string) => companion<NativeSkips>(`/episodes/${encodeURIComponent(episodeId)}/skip-segments`)
export const getTranscript = (episodeId: string) => companion<TimedTranscript>(`/episodes/${episodeId}/transcript`)
export const getAiSettings = () => companion<AiSettings>('/settings/ai')
/** Same query as the episode workspace, so both share one cache entry. */
export const transcriptQuery = (episodeId: string) => ({queryKey: ['transcript', episodeId], queryFn: () => getTranscript(episodeId), retry: false})

/** AI mode needs a ready provider: `configured`, else a model plus a key (unless it runs locally). */
export const aiReady = (settings?: AiSettings | null) => typeof settings?.configured === 'boolean' ? settings.configured
    : Boolean(settings?.model && (settings.key_set || ['ollama', 'custom'].includes(settings.provider)))

/** The cut's MP3, fetched with the login header (a plain link can't send it), as a file to save. */
export async function cutFile(id: string): Promise<Blob> {
    const response = await podfetch(`/companion/cuts/${encodeURIComponent(id)}.mp3`)
    if (!response.ok) throw new Error('download-failed')
    return response.blob()
}

// ---- PodFetch ----

export type SponsorSegment = {category: string; actionType: string; startMs: number; endMs: number; durationMismatch: boolean}
/** Existing per-user preferences, stored by PodFetch; no third-party data is read. */
export type SponsorSettings = {
    enabled: boolean; skipSponsor: boolean; skipSelfpromo: boolean; skipInteraction: boolean; skipIntro: boolean
    skipOutro: boolean; skipPreview: boolean; skipFiller: boolean; skipMusicOfftopic: boolean
}
export type SponsorBlock = {segments: SponsorSegment[]; preferences: SponsorSettings}

async function podfetchJson<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await podfetch(path, init)
    if (!response.ok) throw new Error(`PodFetch answered ${response.status}`)
    return response.json()
}
/** `id` is PodFetch's internal episode id here, not the episode_id. */
export const getSponsorBlock = (id: string) => podfetchJson<SponsorBlock>(`/api/v1/podcasts/episodes/${encodeURIComponent(id)}/sponsorblock`)
export const getSponsorSettings = () => podfetchJson<SponsorSettings>('/api/v1/settings/sponsorblock')
export const putSponsorSettings = (settings: SponsorSettings) => podfetchJson<SponsorSettings>('/api/v1/settings/sponsorblock',
    {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(settings)})
export const SPONSOR_SETTINGS_KEY = ['cuts', 'sponsor-settings']

/** Who may download episodes in PodFetch: admins and uploaders. Null when it can't be told. */
export async function canDownload(): Promise<boolean | null> {
    const response = await podfetch('/api/v1/users/me')
    if (!response.ok) return null
    const role = (await response.json())?.role
    return typeof role === 'string' ? ['admin', 'uploader'].includes(role) : null
}
