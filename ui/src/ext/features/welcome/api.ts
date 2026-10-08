// Welcome: whether the library is empty (PodFetch's own API, not ours), the
// AI connection status (companion/routes/settings_ai.py), and this listener's own topics
// (companion/routes/profile.py), which every brief's verdict prompt reads as evidence.
import {companion} from '../../../utils/companion'
import type {Show} from '../../../utils/listening'
import {podfetch} from '../../shared/podfetch'

export type Profile = {topics: string[]}
export type AiStatus = {configured: boolean; provider: string}

export const SHOWS_KEY = ['welcome-shows'] as const
export const PROFILE_KEY = ['welcome-profile'] as const
export const AI_STATUS_KEY = ['welcome-ai-status'] as const

async function getJson<T>(path: string): Promise<T> {
    const response = await podfetch(path)
    if (!response.ok) throw new Error(`PodFetch answered ${response.status}`)
    return response.json()
}

/** Every show PodFetch already knows about. An empty list is what puts the welcome card up. */
export const fetchShows = () => getJson<Show[]>('/api/v1/podcasts')
export const getAiStatus = () => companion<AiStatus>('/settings/ai')
export const getProfile = () => companion<Profile>('/profile')
export const saveProfile = (topics: string[]) => companion<Profile>('/profile', {method: 'PUT', body: JSON.stringify({topics})})
