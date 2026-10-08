// Highlights and recap: "Save last 30 s" in the player (also the `h` key), a
// "Recap" tab in the episode workspace, a Listen now card, and the /recap page for this week.
import {lazy} from 'react'
import {Rewind} from 'lucide-react'
import type {Feature} from '../../types'
import {HomeRecapCard} from './HomeRecapCard'
import {RecapTab} from './RecapTab'
import {SaveHighlight} from './SaveHighlight'
import './recap.css'

// Loaded on the first visit to /recap, so the page adds nothing to the app's first download.
const WeekRecapPage = lazy(() => import('./WeekRecapPage').then(module => ({default: module.WeekRecapPage})))

export const feature: Feature = {
    id: 'recap',
    // HomeRecapCard keeps the weekly recap visible on Listen now; More also links to it.
    navLinks: [{path: 'recap', label: 'nav-recap', icon: Rewind}],
    routes: [{path: 'recap', element: <WeekRecapPage/>}],
    episodeTools: [{id: 'recap', label: 'tool', Component: RecapTab}],
    playerActions: [SaveHighlight],
    homeSection: HomeRecapCard,
}
