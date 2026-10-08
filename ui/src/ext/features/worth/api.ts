// "Worth hearing": new episodes of your watched shows, briefed for you.
import {companion} from '../../../utils/companion'

export type Verdict = 'HEAR' | 'READ' | 'SKIP'
export type WorthItem = {
    episode_id: string; title: string | null; verdict: Verdict; summary: string | null
    verdict_reason: string | null; percent_new: number | null; duration: number | null; briefed_on: string
}
// login_blocks_auto: PodFetch needs a login the background check and the feed have no session
// for (companion/autobrief.py login_blocks_auto) -- automatic briefs can't run at all on this install.
export type WorthList = {hear: WorthItem[]; other: WorthItem[]; login_blocks_auto: boolean}

export type WorthSettings = {
    enabled: boolean; show_ids: string[]; daily_cap: number; updated_at: string | null
    login_blocks_auto: boolean
}
export type WorthDraft = {enabled: boolean; show_ids: string[]; daily_cap: number}
export type WorthEstimate = {count: number; input_chars: number; input_tokens: number; model: string | null}

export const WORTH_KEY = ['worth-hearing'] as const
export const WORTH_SETTINGS_KEY = ['worth-hearing-settings'] as const
const json = (method: string, body: WorthDraft): RequestInit => ({method, body: JSON.stringify(body)})

/** The page's data: HEAR episodes, and everything else autobrief has checked. Never calls AI. */
export const fetchWorthHearing = () => companion<WorthList>('/worth-hearing')
export const getWorthSettings = () => companion<WorthSettings>('/settings/worth-hearing')
export const saveWorthSettings = (draft: WorthDraft) => companion<WorthSettings>('/settings/worth-hearing', json('PUT', draft))
/** What saving `draft` would send today, before it is saved. Never calls AI. */
export const estimateWorthSettings = (draft: WorthDraft) => companion<WorthEstimate>('/settings/worth-hearing/estimate', json('POST', draft))
