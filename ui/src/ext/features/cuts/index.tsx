// Cuts: Smart Play in the normal player, the Cut tab, "Cut my queue", and sponsor
// skipping. The server side is companion/routes/cuts.py's plans/jobs/cuts API.
import {lazy} from 'react'
import type {Feature} from '../../types'
import {CutPanel} from './CutPanel'
import {PlayerControls} from './PlayerControls'
import {SponsorSettings} from './SponsorSettings'
import './cuts.css'

// The queue page uses the PodFetch client (utils/http.ts), which only loads inside the app's pages.
const QueueCut = lazy(() => import('./QueueCut').then(module => ({default: module.QueueCut})))

export const feature: Feature = {
    id: 'cuts',
    episodeTools: [{id: 'cut', label: 'tool', Component: CutPanel}],
    routes: [{path: '/queue/cut', element: <QueueCut/>}],
    settingsTabs: [{path: 'sponsors', label: 'settings-tab', element: <SponsorSettings/>}],
    playerActions: [PlayerControls],
}
