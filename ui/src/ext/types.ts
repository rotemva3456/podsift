import type {ComponentType, ReactNode} from 'react'
import type {LucideIcon} from 'lucide-react'
import type {components} from '../../schema'

/** A PodFetch episode as the UI receives it (`id` is PodFetch's internal id). */
export type PodcastEpisode = components['schemas']['PodcastEpisodeDto']

/**
 * What a feature gets in the episode workspace. `episodeId` is the episode's public `episode_id`,
 * the id in every `/companion/episodes/{id}` URL. `position` is the moment the user is working on:
 * the selected passage, else the playback position. Times are seconds from the start of the
 * original episode audio; `seek` plays the episode from there.
 */
export type EpisodeCtx = {episodeId: string; episode: PodcastEpisode; position: number; seek: (s: number) => void}

/**
 * One feature, exported as `feature` from `ui/src/ext/features/<id>/index.tsx`. Labels are keys in
 * the feature's own strings, `ui/src/ext/features/<id>/locales/en.json` (i18next namespace `<id>`).
 */
export type Feature = {
    id: string
    navLinks?: {path: string; label: string; mobileLabel?: string; icon: LucideIcon
        badge?: ComponentType     // a small count after the label (render <span className="nav-count">), or nothing
        mobile?: boolean          // retained for older extensions; feature links now live under More on phones
    }[]
    routes?: {path: string; element: ReactNode}[]                          // under "/"
    episodeHeader?: ComponentType<EpisodeCtx>                              // top of the episode workspace
    episodeTools?: {id: string; label: string; Component: ComponentType<EpisodeCtx>}[]  // tabs next to Transcript / Ask / Notes
    settingsTabs?: {path: string; label: string; element: ReactNode}[]     // under /settings
    rowBadges?: ComponentType<{episodeId: string}>                         // on episode rows
    playerActions?: ComponentType<{episodeId: string; position: number}>[] // buttons in the player bar
    homeSection?: ComponentType                                            // top of Listen now, above the episodes
}
