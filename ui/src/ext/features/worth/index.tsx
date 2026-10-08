// Worth hearing: new episodes of your watched shows, briefed on a schedule so
// you see a HEAR verdict without opening each one yourself. Server side: companion/autobrief.py.
import {lazy} from 'react'
import {Sparkles} from 'lucide-react'
import type {Feature} from '../../types'
import {WorthBadge} from './WorthBadge'
import {WorthHome} from './WorthHome'
import {WorthSettings} from './WorthSettings'
import './worth.css'

// The page loads on the first visit to /worth-hearing.
const WorthHearingPage = lazy(() => import('./WorthHearingPage').then(module => ({default: module.WorthHearingPage})))

export const feature: Feature = {
    id: 'worth',
    // Worth hearing stays under More, with WorthHome surfacing new results on Listen now.
    navLinks: [{path: 'worth-hearing', label: 'nav-label', icon: Sparkles, badge: WorthBadge}],
    routes: [{path: 'worth-hearing', element: <WorthHearingPage/>}],
    settingsTabs: [{path: 'worth-hearing', label: 'settings-tab', element: <WorthSettings/>}],
    homeSection: WorthHome,
}
