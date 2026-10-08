// Podcast app feeds: a Settings tab. The server side is companion/feeds.py and
// companion/routes/feed.py.
import type {Feature} from '../../types'
import {FeedSettings} from './FeedSettings'

export const feature: Feature = {
    id: 'feed',
    settingsTabs: [{path: 'feed', label: 'settings-tab', element: <FeedSettings/>}],
}
