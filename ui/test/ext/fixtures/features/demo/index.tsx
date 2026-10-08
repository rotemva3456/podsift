// Test fixture only: exercises every slot. Real features live in ui/src/ext/features/<id>/.
import {Star} from 'lucide-react'
import {useTranslation} from 'react-i18next'
import type {EpisodeCtx, Feature} from '../../../../../src/ext/types'

function Header({episodeId, position}: EpisodeCtx) {
    const {t} = useTranslation('demo')
    return <p className="demo-header">{t('header', {id: episodeId, position})}</p>
}

function Tool({seek}: EpisodeCtx) {
    const {t} = useTranslation('demo')
    return <button type="button" onClick={() => seek(42)}>{t('tool-button')}</button>
}

const Badge = ({episodeId}: {episodeId: string}) => <span className="demo-badge">HEAR {episodeId}</span>
const Count = () => <span className="nav-count">3</span>
const Section = () => <section className="demo-home">Demo home section</section>
const Action = ({episodeId, position}: {episodeId: string; position: number}) =>
    <button type="button" aria-label={`Demo action for ${episodeId} at ${position}`}>D</button>

export const feature: Feature = {
    id: 'demo',
    navLinks: [{path: '/demo', label: 'nav', mobileLabel: 'nav-short', icon: Star, badge: Count, mobile: true},
               {path: '/demo/desk', label: 'nav-desk', icon: Star}],
    routes: [{path: '/demo', element: <p>Demo page</p>}],
    episodeHeader: Header,
    episodeTools: [{id: 'tool', label: 'tool', Component: Tool}],
    settingsTabs: [{path: 'demo', label: 'settings', element: <p>Demo settings page</p>}],
    rowBadges: Badge,
    playerActions: [Action],
    homeSection: Section,
}
