import {lazy} from 'react'
import type {Feature} from '../../types'

// Loaded on the first visit to /search, so the page adds nothing to the app's first download.
const SearchPage = lazy(() => import('./SearchPage').then(module => ({default: module.SearchPage})))

/** Search inside every episode: /search?q= (the header's search opens it). */
export const feature: Feature = {
    id: 'search',
    routes: [{path: 'search', element: <SearchPage/>}],
}
