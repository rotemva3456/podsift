import {Button} from './ui/button'
import {FC, useEffect, useMemo} from 'react'
import {
    SKIPPED_TIME
} from '../utils/Utilities'
import useAudioPlayer, {type AudioPlayerPlay} from '../store/AudioPlayerSlice'
import { Pause, Play, RotateCcw, RotateCw, SkipBack, SkipForward } from 'lucide-react'
import { useKeyDown } from '../hooks/useKeyDown'
import useCommon from "../store/CommonSlice";
import {getAudioPlayer, startAudioPlayer} from "../utils/audioPlayer";
import {cn} from "../lib/utils";
import {usePlaybackLogger} from "../hooks/usePlaybackLogger";
import {useCastRemote} from "../hooks/useCastRemote";

type PlayerTimeControlsProps = {
    currentPodcastEpisode?: AudioPlayerPlay
}


const SPEED_STEPS = [0.5, 1,1.1,1.25, 1.5, 2, 2.5, 3]


export const PlayerTimeControls: FC<PlayerTimeControlsProps> = ({ currentPodcastEpisode }) => {
    const logCurrentPlaybackTime = usePlaybackLogger()
    const cast = useCastRemote()
    const setSelectedEpisodes = useCommon(state => state.setSelectedEpisodes)
    const episodes = useCommon(state => state.selectedEpisodes)
    const isPlaying  = useAudioPlayer(state => state.isPlaying)
    const speed = useAudioPlayer(state => state.playBackRate)
    const time  = useAudioPlayer(state => state.metadata?.currentTime)
    const setCurrentPodcastEpisode = useAudioPlayer(state => state.setCurrentPodcastEpisode)
    const setPlaybackRate = useAudioPlayer(state => state.setPlayBackRate)

    const [hasPrevious, index, hasNext] = useMemo(()=>{
        if (!currentPodcastEpisode) {
            return [false, -1, false]
        }
        const index = episodes.findIndex(e => e.podcastEpisode.id === currentPodcastEpisode?.podcastEpisode.id)

        return [index > 0, index, index < episodes.length -1]
    }, [episodes, currentPodcastEpisode])


    const skipToPreviousEpisode = () => {
        if (currentPodcastEpisode === undefined) return


        if (index === -1) return

        if (index === 0) return

        switchToEpisodes(index - 1)
    }

    useEffect(() => {
        const audioPlayer = getAudioPlayer()
        if (!audioPlayer) {
            return
        }
        audioPlayer.onended = () => {
            if (currentPodcastEpisode === undefined) return
            logCurrentPlaybackTime(currentPodcastEpisode!.podcastEpisode.episode_id, currentPodcastEpisode!.podcastEpisode.total_time)
        }
    }, [currentPodcastEpisode]);


    const skipToNextEpisode = () => {
        if (currentPodcastEpisode === undefined) return

        const index = episodes.findIndex(e => e.podcastEpisode.id === currentPodcastEpisode.podcastEpisode.id)

        if (index === -1) return

        if (index === episodes.length + 1) return

        switchToEpisodes(index + 1)
    }

    const switchToEpisodes = async (index: number) => {
        const nextEpisode = episodes[index]
        if (!nextEpisode) return
        setCurrentPodcastEpisode(index)
        await startAudioPlayer(nextEpisode.podcastEpisode.local_url, nextEpisode.podcastHistoryItem?.position ?? 0)
    }

    const handleButton = () => {
        if (cast.isCasting) {
            if (cast.state === 'playing' || cast.state === 'buffering') {
                void cast.pause()
            } else {
                void cast.resume()
            }
            return
        }
        const audioPlayer = getAudioPlayer()
        if (!audioPlayer) {
            return
        }

        if (audioPlayer.paused) {
            void audioPlayer.play().catch(() => {})
        } else {
            if (time && currentPodcastEpisode) {
                logCurrentPlaybackTime(currentPodcastEpisode.podcastEpisode.episode_id, time)
                const mappedEpisodes = episodes.map(e=>{
                    if(e.podcastEpisode.episode_id === currentPodcastEpisode.podcastEpisode.episode_id){
                        return {
                            ...e,
                           podcastHistoryItem:{
                                 ...e.podcastHistoryItem!,
                               position: time
                           }
                        }
                    }
                    return e
                })
                setSelectedEpisodes(mappedEpisodes)
            }

            audioPlayer.pause()
        }
    }

    const changeSpeed = () => {
        const audioPlayer = getAudioPlayer()
        if (!audioPlayer) {
            return
        }

        const currentIndex = SPEED_STEPS.indexOf(speed)

        if (currentIndex === SPEED_STEPS.length - 1) {
            audioPlayer.playbackRate = SPEED_STEPS[0]!
            setPlaybackRate(SPEED_STEPS[0]!)
            return
        }

        audioPlayer.playbackRate = SPEED_STEPS[currentIndex + 1]!
        setPlaybackRate(SPEED_STEPS[currentIndex + 1]!)
    }

    const seekForward = () => {
        if (cast.isCasting) {
            const max = cast.durationSecs > 0 ? cast.durationSecs : Infinity
            void cast.seek(Math.min(max, cast.positionSecs + SKIPPED_TIME))
            return
        }
        const audioPlayer = getAudioPlayer()
        if (!audioPlayer) {
            return
        }

        if (audioPlayer.currentTime + SKIPPED_TIME < audioPlayer.duration) {
            audioPlayer.currentTime += SKIPPED_TIME
        }
    }

    const seekBackward = () => {
        if (cast.isCasting) {
            void cast.seek(Math.max(0, cast.positionSecs - SKIPPED_TIME))
            return
        }
        const audioPlayer = getAudioPlayer()
        if (!audioPlayer) {
            return
        }

        if (audioPlayer.currentTime - SKIPPED_TIME > 0 ) {
            audioPlayer.currentTime -= SKIPPED_TIME
        } else {
            audioPlayer.currentTime = 0
        }
    }

    useKeyDown(seekBackward, ['j', 'ArrowLeft'], false)

    useKeyDown(seekForward, ['l', 'ArrowRight'], false)

    useKeyDown(handleButton, ['k', ' '], false)

    const isCurrentlyPlaying = cast.isCasting
        ? (cast.state === 'playing' || cast.state === 'buffering')
        : isPlaying

    return (
        <div className="expanded-time-controls flex items-center justify-center gap-2" onKeyDownCapture={event => {
            // Focused controls use native activation; global shortcuts still work elsewhere.
            if ([' ', 'ArrowLeft', 'ArrowRight'].includes(event.key)) event.stopPropagation()
        }}>
            {/* Skip back 30s */}
            <Button variant="ghost" size="icon-lg"
                onClick={() => seekBackward()}
                className="relative cursor-pointer ui-text hover:ui-text-hover active:scale-90"
                aria-label="Skip back 30 seconds"
            >
                <RotateCcw className="size-6" />
                <span className="absolute inset-0 grid place-items-center text-[8px] font-bold mt-0.5">30</span>
            </Button>

            {/* Previous */}
            <Button variant="ghost" size="icon-lg" aria-label="Previous episode" disabled={!hasPrevious} className={cn("cursor-pointer ui-text hover:ui-text-hover active:scale-90", hasPrevious ? '' : 'opacity-10')} onClick={() => skipToPreviousEpisode()}>
                <SkipBack className="size-6" fill="currentColor" />
            </Button>

            {/* Play/pause */}
            <Button size="icon-lg" className="round-play" aria-label={isCurrentlyPlaying ? 'Pause' : 'Play'} onClick={() => handleButton()}>
                {isCurrentlyPlaying
                    ? <Pause size={22} fill="currentColor" className="size-5" />
                    : <Play size={22} fill="currentColor" className="size-5 ml-0.5" />}
            </Button>

            {/* Next */}
            <Button variant="ghost" size="icon-lg" aria-label="Next episode" disabled={!hasNext} className={cn("cursor-pointer ui-text hover:ui-text-hover active:scale-90", hasNext ? '' : 'opacity-10')} onClick={() => skipToNextEpisode()}>
                <SkipForward className="size-6" fill="currentColor" />
            </Button>

            {/* Skip forward 30s */}
            <Button variant="ghost" size="icon-lg"
                onClick={() => seekForward()}
                className="relative cursor-pointer ui-text hover:ui-text-hover active:scale-90"
                aria-label="Skip forward 30 seconds"
            >
                <RotateCw className="size-6" />
                <span className="absolute inset-0 grid place-items-center text-[8px] font-bold mt-0.5">30</span>
            </Button>

            {/* Speed fixed width to prevent layout shift when value changes */}
            <Button variant="ghost" size="icon-lg" aria-label={`Playback speed ${speed} times`} onClick={() => changeSpeed()}>{speed}×</Button>
        </div>
    )
}
