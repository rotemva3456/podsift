import {useEffect, useRef} from 'react'
import {client} from '../utils/http'
import {getAudioPlayer} from '../utils/audioPlayer'
import {fetchEpisode, playEpisode} from '../utils/listening'
import useAudioPlayer from '../store/AudioPlayerSlice'
import {useListenQueue} from '../hooks/useListenQueue'
const KEY='podsift-listening-position-v1'
let restoring=false
export function ListenSession() {
    const queue=useListenQueue(),queueRef=useRef(queue)
    queueRef.current=queue
    useEffect(()=>{
        if(restoring)return
        restoring=true
        try {
            const stored=JSON.parse(localStorage.getItem(KEY) ?? 'null')
            if(stored?.id && Number.isFinite(stored.position) && stored.position>=0)void fetchEpisode(stored.id).then(result=>{
                if(!useAudioPlayer.getState().loadedPodcastEpisode)void playEpisode(result.podcastEpisode,stored.position,false)
            }).catch(()=>{})
        }catch{}
    },[])
    useEffect(()=>{
        const audio=getAudioPlayer();if(!audio)return
        let lastSaved=0
        const persist=(remote=false)=>{
            const state=useAudioPlayer.getState(),episode=state.loadedPodcastEpisode?.podcastEpisode
            if(!episode)return
            const position=state.pendingSeek ?? state.metadata?.currentTime ?? 0
            try{localStorage.setItem(KEY,JSON.stringify({id:episode.episode_id,position}))}catch{}
            if(remote && state.pendingSeek===undefined)void client.POST('/api/v1/podcasts/episode',{body:{podcastEpisodeId:episode.episode_id,time:Math.round(position)},keepalive:true}).catch(()=>{})
        }
        const tick=()=>{if(Date.now()-lastSaved>5000){persist(true);lastSaved=Date.now()}}
        const pause=()=>persist(true)
        const error=()=>useAudioPlayer.setState({playbackError:'Audio is unavailable. Try playing the episode again.',isPlaying:false})
        const ended=()=>{
            persist(true)
            const state=useAudioPlayer.getState(),q=queueRef.current
            const index=q.items.findIndex(item=>item.podcastEpisode.episode_id===state.loadedPodcastEpisode?.podcastEpisode.episode_id)
            if(index>=0){const next=q.items[index+1];void q.update({remove:q.items[index]!.podcastEpisode.id}).catch(()=>{});if(next)void playEpisode(next.podcastEpisode)}
        }
        audio.addEventListener('timeupdate',tick);audio.addEventListener('pause',pause);audio.addEventListener('ended',ended);audio.addEventListener('error',error);window.addEventListener('pagehide',pause)
        return()=>{audio.removeEventListener('timeupdate',tick);audio.removeEventListener('pause',pause);audio.removeEventListener('ended',ended);audio.removeEventListener('error',error);window.removeEventListener('pagehide',pause)}
    },[])
    return null
}
