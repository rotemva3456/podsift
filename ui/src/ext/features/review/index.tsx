// Flashcards, "replay what I forget", and Anki/Obsidian export.
import {lazy} from 'react'
import {Brain} from 'lucide-react'
import type {Feature} from '../../types'
import {CardsPanel} from './CardsPanel'
import {DueBadge} from './DueBadge'
import './review.css'

const ReviewPage = lazy(() => import('./ReviewPage').then(module => ({default: module.ReviewPage})))

export const feature: Feature = {
    id: 'review',
    navLinks: [{path: '/review', label: 'nav', icon: Brain, badge: DueBadge, mobile: true}],
    routes: [{path: '/review', element: <ReviewPage/>}],
    episodeTools: [{id: 'cards', label: 'tool', Component: CardsPanel}],
}
