// Welcome: a first-run home card, shown only while the library is empty -
// add a show (reusing PodFetch's own search/RSS page), optionally connect AI, and pick topics
// that steer every brief's HEAR/READ/SKIP verdict (companion/routes/profile.py).
import type {Feature} from '../../types'
import {WelcomeHome} from './WelcomeHome'
import './welcome.css'

export const feature: Feature = {
    id: 'welcome',
    homeSection: WelcomeHome,
}
