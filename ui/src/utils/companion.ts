import {authHeader} from '../ext/shared/podfetch'

export type Passage = {id?: string; start: number; end: number | null; text: string}
export type Transcript = {episode_id: string; source: string | null; timed: boolean; text: string; segments: Passage[]; digest?: string}
export type Note = {
    id: string; episode_id: string; title: string; position: number; text: string; created_at: string
    kind?: 'note' | 'highlight'; start?: number | null; end?: number | null; quote?: string | null
}
export type SaveNote = {
    id: string; episode_id: string; position: number; text: string
    kind?: 'note' | 'highlight'; start?: number; end?: number; quote?: string
}
export type Answer = {
    episode_id: string; question: string; position: number
    status: 'answered' | 'insufficient_evidence' | 'not_configured' | 'no_timed_transcript'
    claims: {text: string; citations: string[]}[]
    passages: (Passage & {id: string; episode_id: string})[]
}
/** Calls our API with the PodFetch login (the same header as the PodFetch client), and returns its JSON. */
export async function companion<T>(path: string, options?: RequestInit): Promise<T> {
    const headers = new Headers(options?.headers ?? {'Content-Type': 'application/json'})
    const authorization = authHeader()
    if (authorization && !headers.has('Authorization')) headers.set('Authorization', authorization)
    const response = await fetch(`/companion${path}`, {...options, headers})
    if (!response.ok) {
        const body = await response.json().catch(() => null)
        throw new Error(typeof body?.detail === 'string' ? body.detail : 'Could not complete this request. Please try again.')
    }
    return response.json()
}
export const saveCompanionNote = (note: SaveNote) => companion<Note>('/notes', {method: 'POST', body: JSON.stringify(note)})
export function passageAt(segments: Passage[], position: number): number {
    return segments.findIndex((segment, i) => position >= segment.start && position < (segment.end ?? segments[i + 1]?.start ?? Infinity))
}
