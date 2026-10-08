// Modified by Podsift contributors, 2026-09-22. See CHANGES.md.
import {Link} from 'react-router-dom'
import {BookOpen, Pause, Play, RotateCcw, RotateCw, Volume2} from 'lucide-react'
import {FC} from 'react'
import {AudioAmplifier} from '../models/AudioAmplifier'
import useAudioPlayer from '../store/AudioPlayerSlice'
import {getAudioPlayer, startAudioPlayer} from '../utils/audioPlayer'
import {clock, plainText} from '../utils/listening'
import {Button} from './ui/button'
import {Slider} from './ui/slider'
import {playerActions} from '../ext/registry'
export const DrawerAudioPlayer: FC<{audioAmplifier:AudioAmplifier|undefined}> = () => {
    const episode=useAudioPlayer(state=>state.loadedPodcastEpisode?.podcastEpisode)
    const playing=useAudioPlayer(state=>state.isPlaying),metadata=useAudioPlayer(state=>state.metadata)
    const pending=useAudioPlayer(state=>state.pendingSeek),error=useAudioPlayer(state=>state.playbackError)
    const speed=useAudioPlayer(state=>state.playBackRate),volume=useAudioPlayer(state=>state.volume)
    if(!episode)return null
    const position=pending ?? metadata?.currentTime ?? 0,duration=metadata?.duration || episode.total_time
    const seek=(time:number)=>{
        const audio=getAudioPlayer();if(!audio)return
        const target=Math.max(0,Math.min(time,duration || Infinity))
        if(audio.readyState<1){void startAudioPlayer(episode.local_url,target,!audio.paused);return}
        audio.currentTime=target;useAudioPlayer.getState().setCurrentTimeUpdate(target)
    }
    return <div className="listen-player" data-testid="audio-player-bar" aria-label="Now playing">
        {error && <p role="alert" className="playback-error">{error}</p>}
        <div className="player-episode"><img src={episode.local_image_url} alt=""/><Link to={`/learn?episode=${episode.episode_id}`}><span>{plainText(episode.name)}</span><small>Transcript & notes</small></Link></div>
        <div className="player-center"><div className="player-controls"><Button variant="ghost" size="icon" aria-label="Back 15 seconds" onClick={()=>seek(position-15)}><RotateCcw/></Button>
            <Button className="round-play" size="icon-lg" aria-label={playing?'Pause':'Play'} onClick={()=>{const audio=getAudioPlayer();if(playing)audio?.pause();else void startAudioPlayer(episode.local_url,position)}}>{playing?<Pause fill="currentColor"/>:<Play fill="currentColor"/>}</Button>
            <Button variant="ghost" size="icon" aria-label="Forward 30 seconds" onClick={()=>seek(position+30)}><RotateCw/></Button><Button variant="ghost" className="speed-button" aria-label={`Playback speed ${speed} times`} onClick={()=>{const values=[1,1.25,1.5,1.75,2];const next=values[(values.indexOf(speed)+1)%values.length]!;useAudioPlayer.getState().setPlayBackRate(next);const audio=getAudioPlayer();if(audio)audio.playbackRate=next}}>{speed}×</Button>
            {playerActions.length>0 && <div className="player-actions">{playerActions.map(({key,Component})=><Component key={key} episodeId={episode.episode_id} position={position}/>)}</div>}
        </div><div className="player-progress"><span>{clock(position)}</span><Slider aria-label="Playback position" min={0} max={duration||1} step={1} value={[position]} onValueChange={value=>seek(Array.isArray(value)?value[0]!:value)}/><span>{clock(duration)}</span></div></div>
        <div className="player-extras"><Button variant="ghost" size="icon" nativeButton={false} render={<Link to={`/learn?episode=${episode.episode_id}`}/>} aria-label="Open transcript and notes"><BookOpen/></Button><Volume2 size={16}/><Slider aria-label="Volume" min={0} max={100} value={[volume]} onValueChange={value=>useAudioPlayer.getState().setVolume(Array.isArray(value)?value[0]!:value)}/></div>
    </div>
}
