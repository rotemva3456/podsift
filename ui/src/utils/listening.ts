import type { components } from '../../schema'
import useAudioPlayer from '../store/AudioPlayerSlice'
import useCommon from '../store/CommonSlice'
import { startAudioPlayer } from './audioPlayer'
import { podfetch } from '../ext/shared/podfetch'

export type Episode = components['schemas']['PodcastEpisodeDto']
export type Show = components['schemas']['PodcastDto']
export type History = components['schemas']['EpisodeDto']
export const clock = (seconds = 0) => {
    const n = Math.max(0, Math.floor(seconds))
    return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`
}
export const minutes = (seconds: number) => seconds > 0 ? `${Math.ceil(seconds / 60)} min` : 'Duration unavailable'
export const plainText = (text = '') => {
    const document = new DOMParser().parseFromString(text, 'text/html')
    return document.body.textContent?.replace(/\s+/g, ' ').trim() ?? ''
}
export async function playEpisode(episode: Episode, position = 0, autoplay = true) {
    useCommon.getState().setSelectedEpisodes([{podcastEpisode: episode}])
    useAudioPlayer.setState({currentPodcastEpisodeIndex: 0,
        loadedPodcastEpisode: {podcastEpisode: episode, chapters: []},
        metadata: {currentTime: position, duration: episode.total_time, percentage: episode.total_time ? position / episode.total_time * 100 : 0}})
    await startAudioPlayer(episode.local_url, position, autoplay)
}
export async function fetchEpisode(id: string): Promise<components['schemas']['PodcastEpisodeWithHistory']> {
    // Upstream's generated schema mistakenly shares a list operation with this endpoint.
    const response=await podfetch(`/api/v1/episodes/${encodeURIComponent(id)}`)  // with the PodFetch login
    if(!response.ok)throw new Error('This episode could not load. Please try again.')
    const result=await response.json()
    if(!result?.podcastEpisode?.episode_id)throw new Error('The library returned an invalid episode.')
    return result
}
