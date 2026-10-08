// Modified by Podsift contributors, 2026-09-22. See CHANGES.md.
import {useEffect, useState} from 'react'
import {AudioAmplifier} from '../models/AudioAmplifier'
import {AudioPlayer} from './AudioPlayer'
import {DetailedAudioPlayer} from './DetailedAudioPlayer'
import useAudioPlayer from '../store/AudioPlayerSlice'
import useCommon from '../store/CommonSlice'
import {client} from '../utils/http'
import {fetchEpisode} from '../utils/listening'

export const AudioComponents = () => {
    const detailed=useCommon(state=>state.detailedAudioPlayerOpen)
    const [amplifier,setAmplifier]=useState<AudioAmplifier>()
    const index=useAudioPlayer(state=>state.currentPodcastEpisodeIndex)
    const episodes=useCommon(state=>state.selectedEpisodes)
    useEffect(()=>{
        const selected=episodes[index ?? -1]?.podcastEpisode
        if(!selected)return
        let cancelled=false
        // This endpoint includes optional history. A never-played episode is valid.
        void Promise.all([
            fetchEpisode(selected.episode_id),
            client.GET('/api/v1/podcasts/episodes/{id}/chapters',{params:{path:{id:selected.id}}}),
            client.GET('/api/v1/podcasts/{id}',{params:{path:{id:selected.podcast_id}}}),
        ]).then(([detail,chapters,show])=>{
            if(cancelled)return
            useAudioPlayer.setState({loadedPodcastEpisode:{...detail,chapters:chapters.data ?? []},currentPodcast:show.data})
        }).catch(()=>{if(!cancelled)useAudioPlayer.setState({loadedPodcastEpisode:{podcastEpisode:selected,chapters:[]}})})
        return()=>{cancelled=true}
    },[index,episodes])
    return <><AudioPlayer audioAmplifier={amplifier} setAudioAmplifier={setAmplifier}/>{detailed&&<DetailedAudioPlayer audioAmplifier={amplifier} setAudioAmplifier={setAmplifier}/>}</>
}
