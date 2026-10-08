// The recap API and the shared note/highlight shape. Times are seconds from
// the start of the episode audio; see companion/recap.py and companion/routes/notes.py.
import {companion} from '../../../utils/companion'

export type Citation = {segment_id: string; start: number; end: number}
export type KeyIdea = {text: string; citations: Citation[]}
export type WeekKeyIdea = KeyIdea & {episode_id: string; title: string}
export type LearnedStatus = 'ready' | 'not_generated' | 'failed' | 'no_transcript' | 'no_ai'
export type Learned = {
    status: LearnedStatus; text: string | null; citations: Citation[]
    model: string | null; created_at: string | null; error: string | null
}
export type Heard = {heard: boolean; position: number | null; total: number | null; percent: number | null; at: string | null}

/** A row from POST/GET /companion/notes: a plain note (`kind: "note"`) or a highlight, which also
 *  carries the transcript quote and the range it was saved from. */
export type NoteRow = {
    id: string; episode_id: string; title: string; position: number; text: string; created_at: string
    kind: 'note' | 'highlight'; start: number | null; end: number | null; quote: string | null
}

export type EpisodeRecap = {
    episode_id: string; heard: Heard; key_ideas: KeyIdea[]
    highlights: NoteRow[]; notes: NoteRow[]; learned: Learned
}
export type WeekEpisode = {
    episode_id: string; title: string | null; podcast_name: string | null
    heard_seconds: number | null; total_seconds: number | null; at: string
}
export type WeekRecap = {
    since: string; until: string; minutes: number
    episodes: WeekEpisode[]; key_ideas: WeekKeyIdea[]; highlights: NoteRow[]
}

export const recapKey = (episodeId: string) => ['recap', episodeId] as const
export const weekKey = ['recap-week'] as const

const path = (episodeId: string) => `/episodes/${encodeURIComponent(episodeId)}/recap`

export const fetchRecap = (episodeId: string) => companion<EpisodeRecap>(path(episodeId))
/** Makes (or reuses the cached) "what you learned" paragraph. Needs AI and a transcript. */
export const makeLearned = (episodeId: string, regenerate = false) =>
    companion<Learned>(`${path(episodeId)}/learned`, {method: 'POST', body: JSON.stringify({regenerate})})
export const fetchWeek = () => companion<WeekRecap>('/recap/week')

export type SaveNoteInput = {
    id: string; episode_id: string; position: number; text: string
    kind?: 'note' | 'highlight'; start?: number; end?: number; quote?: string
}
/** Saves a note or a highlight. Highlights and voice notes share this one endpoint. */
export const saveNote = (note: SaveNoteInput) =>
    companion<NoteRow>('/notes', {method: 'POST', body: JSON.stringify(note)})
