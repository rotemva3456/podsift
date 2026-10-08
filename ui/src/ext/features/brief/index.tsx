import {lazy} from 'react'
import type {Feature} from '../../types'
import {BriefPanel} from './BriefPanel'
import {BriefBadge} from './Verdict'
import './brief.css'

// The queue page loads on the first visit to /briefs.
const QueuePage = lazy(() => import('./QueuePage').then(module => ({default: module.QueuePage})))

/** Episode brief, "is it worth my time?": a panel above the episode workspace, a HEAR/READ/SKIP chip on
 *  episode rows, and /briefs?show=<podcast id> to brief up to 20 episodes of a show at once. */
export const feature: Feature = {
    id: 'brief',
    episodeHeader: BriefPanel,
    rowBadges: BriefBadge,
    routes: [{path: 'briefs', element: <QueuePage/>}],
}
