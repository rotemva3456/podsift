// Modified by Podsift contributors, 2026-09-22. See CHANGES.md.
import {create} from "zustand";
import {components} from "../../schema";




type AudioMetadata = {
    currentTime: number,
    duration: number,
    percentage: number
}

export type AudioPlayerPlay = components["schemas"]["PodcastEpisodeWithHistory"] & {
    chapters: components['schemas']['PodcastChapterDto'][]
}

/** [start, end] in seconds from the start of the episode's downloaded file. */
export type TimeRange = [number, number]
/**
 * Smart Play: play only the `keep` ranges of one episode (the episode's `episode_id`). The player
 * skips everything else, and only while it plays the downloaded file. See `watchSkips` in
 * utils/audioPlayer.ts.
 */
export type SkipPlan = {episodeId: string, keep: TimeRange[], label: string}
/** Sponsor parts to skip in one episode, whatever else plays (the player's "Skip sponsors" switch). */
export type SponsorSkip = {episodeId: string, spans: TimeRange[], manualSpans?: TimeRange[], duration?: number}
export type SkipNoticeKind = 'skipped' | 'sponsor' | 'finished' | 'next' | 'next-failed'
/** A replay applies once to this episode and media source, including overlapping Smart Play gaps. */
export type SkippedPassage = {episodeId: string; source: string; start: number; end: number}

type AudioPlayerProps = {
    isPlaying: boolean,
    pendingSeek?: number,
    playbackError?: string,
    currentPodcastEpisodeIndex: number | undefined,
    currentPodcast: components["schemas"]["PodcastDto"]|undefined,
    metadata: AudioMetadata|undefined,
    volume: number,
    loadedPodcastEpisode?: AudioPlayerPlay
    playBackRate: number,
    /** The Smart Play plan of the playing episode, and the plans to play after it (in order). */
    skipPlan: SkipPlan | null,
    skipNext: SkipPlan[],
    sponsorSkip: SponsorSkip | null,
    /** The last thing the skipping did that the listener should hear about; `at` is Date.now(). */
    skipNotice: {kind: SkipNoticeKind, at: number, passage?: SkippedPassage} | null,
    skipReplay: SkippedPassage | null,
    /** The media element's current src: PodFetch's stream (/proxy/…) or the downloaded file. */
    mediaSource: string,
    setPlaying: (isPlaying: boolean) => void,
    setCurrentPodcastEpisode: (currentPodcastEpisode: number) => void,
    setMetadata: (metadata: AudioMetadata) => void,
    setCurrentTimeUpdate: (currentTime: number) => void,
    setCurrentTimeUpdatePercentage: (percentage: number) => void,
    setCurrentPodcast: (currentPodcast: components["schemas"]["PodcastDto"]) => void,
    setVolume: (volume: number) => void,
    setPlayBackRate: (playBackRate: number) => void,
    setSkipPlan: (skipPlan: SkipPlan | null, skipNext?: SkipPlan[]) => void,
    /** "Stop Smart Play": back to normal playback. Sponsor skipping stays as it is. */
    stopSmartPlay: (notice?: SkipNoticeKind) => void,
    setSponsorSkip: (sponsorSkip: SponsorSkip | null) => void,
    notifySkip: (kind: SkipNoticeKind | null, passage?: SkippedPassage) => void
}


const useAudioPlayer = create<AudioPlayerProps>()((set, get) => ({
    isPlaying: false,
    currentPodcastEpisodeIndex: undefined,
    loadedPodcastEpisode: undefined,
    currentPodcast: undefined,
    metadata: undefined,
    volume: 100,
    playBackRate: 1,
    setPlaying: (isPlaying: boolean) => set({isPlaying}),
    setCurrentPodcastEpisode: (currentPodcastEpisode) => set({currentPodcastEpisodeIndex: currentPodcastEpisode}),
    setMetadata: (metadata: AudioMetadata) => set({metadata}),
    setCurrentTimeUpdate: (currentTime: number) => {
        const metadata = get().metadata
        if(metadata){
                const percentage = metadata.duration > 0 ? (currentTime / metadata.duration) * 100 : 0
                set({metadata: {...metadata, currentTime, percentage}})
        }
    },
    setCurrentTimeUpdatePercentage: (percentage: number) => {
        const metadata = get().metadata
        if(metadata){
            const currentTime = metadata.duration > 0 ? (percentage / 100) * metadata.duration : 0
            set({metadata: {...metadata, percentage, currentTime}})
        }
    },
    setCurrentPodcast: (currentPodcast: components["schemas"]["PodcastDto"]) => set({currentPodcast}),
    setVolume: (volume: number) => set({volume}),
    setPlayBackRate: (playBackRate: number) => set({playBackRate}),
    skipPlan: null,
    skipNext: [],
    sponsorSkip: null,
    skipNotice: null,
    skipReplay: null,
    mediaSource: '',
    setSkipPlan: (skipPlan, skipNext = []) => set({skipPlan, skipNext, skipNotice: null, skipReplay: null}),
    stopSmartPlay: (notice) => set({skipPlan: null, skipNext: [], skipNotice: notice ? {kind: notice, at: Date.now()} : null}),
    setSponsorSkip: (sponsorSkip) => set({sponsorSkip}),
    notifySkip: (kind, passage) => set({skipNotice: kind ? {kind, at: Date.now(), ...(passage ? {passage} : {})} : null})
}))

export default useAudioPlayer
