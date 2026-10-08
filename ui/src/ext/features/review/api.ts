// Flashcards and "replay what I forget". Reuses the cuts feature's Plan API (cuts/api.ts)
// and Smart Play (cuts/smartPlay.ts) for the plan "replay" returns: it is a normal plan.
import {companion} from '../../../utils/companion'
import {podfetch} from '../../shared/podfetch'
import type {Plan} from '../cuts/api'

export type {Plan}
export type Citation = {segment_id: string; start: number; end: number}
export type Card = {
    id: string; episode_id: string; episode_title: string; question: string; answer: string
    citations: Citation[]; due: number; interval: number; ease: number; reps: number; lapses: number
    model: string | null; created_at: string
}
export type Grade = 'again' | 'hard' | 'good' | 'easy'
export type CardsResponse = {episode_id: string; cards: Card[]; ai_ready: boolean; has_transcript?: boolean}
export type Due = {count: number; cards: Card[]}

export const dueKey = ['review', 'due']
export const cardsKey = (episodeId: string) => ['review', 'cards', episodeId]

export const fetchCards = (episodeId: string) => companion<CardsResponse>(`/episodes/${encodeURIComponent(episodeId)}/cards`)
export const makeCards = (episodeId: string, regenerate = false) =>
    companion<CardsResponse>(`/episodes/${encodeURIComponent(episodeId)}/cards`, {method: 'POST', body: JSON.stringify({regenerate})})
export const fetchDue = (limit = 50) => companion<Due>(`/review/due?limit=${limit}`)
export const gradeCard = (id: string, grade: Grade) =>
    companion<Card>(`/review/${encodeURIComponent(id)}/grade`, {method: 'POST', body: JSON.stringify({grade})})
/** A cut plan of every card answered Again 2+ times. 409 (thrown as an Error) when none qualify yet. */
export const replayPlan = () => companion<Plan>('/review/replay', {method: 'POST'})

/** The two exports (Anki TSV, Obsidian Markdown): fetched with the login header, not a plain link. */
async function exportText(path: string): Promise<string> {
    const response = await podfetch(`/companion${path}`)
    if (!response.ok) throw new Error('Could not make the export. Please try again.')
    return response.text()
}
export const fetchAnkiExport = () => exportText('/review/export.tsv')
export const fetchObsidianExport = () => exportText('/review/export.md')

/** Save a text export as a downloaded file (no server round trip: the text is already in hand). */
export function saveText(text: string, filename: string, type: string): void {
    const url = URL.createObjectURL(new Blob([text], {type}))
    const link = Object.assign(document.createElement('a'), {href: url, download: filename})
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 60_000)
}
