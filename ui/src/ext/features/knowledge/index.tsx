import {lazy} from 'react'
import type {Feature} from '../../types'
import './knowledge.css'

const KnowledgePage = lazy(() => import('./KnowledgePage').then(module => ({default: module.KnowledgePage})))

export const feature: Feature = {
    id: 'knowledge',
    routes: [{path: '/knowledge', element: <KnowledgePage/>}],
}
