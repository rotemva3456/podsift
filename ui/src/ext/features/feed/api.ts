// Podcast app feeds. The server side is companion/feeds.py and
// companion/routes/feed.py. The token is write-only in the other direction from the AI key: it
// goes DOWN once, right after creation, and is never asked for again -- only replaced.
import {companion} from '../../../utils/companion'

export type FeedStatus = {configured: boolean; created_at: string | null}
export type FeedLinks = {token: string; shows_url_template: string; cuts_url: string}

export const FEED_STATUS_KEY = ['feed-settings']
export const getFeedStatus = () => companion<FeedStatus>('/settings/feed')
export const makeFeedToken = () => companion<FeedLinks>('/settings/feed/token', {method: 'POST'})
