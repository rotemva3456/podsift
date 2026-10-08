import {BookOpen, Check, ListPlus, Play} from 'lucide-react'
import {Link} from 'react-router-dom'
import {Button} from './ui/button'
import {clock, Episode, History, minutes, plainText, playEpisode, Show} from '../utils/listening'
import {useListenQueue} from '../hooks/useListenQueue'
import {rowBadges} from '../ext/registry'

export function ListenEpisodeRow({episode, show, history, briefAction = false}: {episode: Episode; show?: Show; history?: History | null; briefAction?: boolean}) {
    const queue = useListenQueue()
    const queued = queue.items.some(item => item.podcastEpisode.episode_id === episode.episode_id)
    const position = history?.position ?? 0
    return <article className="episode-row" data-episode={episode.id}>
        <img className="episode-cover" src={episode.local_image_url || show?.image_url} alt="" loading="lazy" />
        <div className="episode-copy">
            <p className="episode-show">{show?.name ?? new Date(episode.date_of_recording).toLocaleDateString(undefined, {month: 'short', day: 'numeric'})}</p>
            <Link className="episode-title" to={`/learn?episode=${encodeURIComponent(episode.episode_id)}`}>{plainText(episode.name)}</Link>
            <p className="episode-description">{plainText(episode.description)}</p>
            <span className="episode-meta">{minutes(episode.total_time)}{position > 0 && ` · Played to ${clock(position)}`}</span>
            {rowBadges.length > 0 && <span className="episode-badges">{rowBadges.map(({featureId, Component}) => <Component key={featureId} episodeId={episode.episode_id}/>)}</span>}
        </div>
        <div className="episode-actions">
            <Button variant="ghost" size="icon" aria-label={queued ? `Remove ${plainText(episode.name)} from queue` : `Add ${plainText(episode.name)} to queue`} disabled={queue.saving}
                onClick={() => void queue.update(queued ? {remove: episode.id} : {episode}).catch(() => {})}>{queued ? <Check/> : <ListPlus/>}</Button>
            <Button variant={briefAction ? 'outline' : 'ghost'} size={briefAction ? 'sm' : 'icon'} nativeButton={false} render={<Link to={`/learn?episode=${encodeURIComponent(episode.episode_id)}${briefAction ? '&overview=1' : ''}`}/>} aria-label={briefAction ? `Open brief for ${plainText(episode.name)}` : `Read ${plainText(episode.name)}`}>{briefAction ? 'Open brief' : <BookOpen/>}</Button>
            <Button className="round-play" size="icon-lg" aria-label={`Play ${plainText(episode.name)}`} onClick={() => void playEpisode(episode, position)}><Play fill="currentColor"/></Button>
        </div>
        {queue.saveError && <p role="alert" className="text-destructive text-sm">Could not update the queue. Try again.</p>}
    </article>
}
