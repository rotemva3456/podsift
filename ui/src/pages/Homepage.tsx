import {PodcastArtwork} from '../components/PodcastArtwork'
// Modified by Podsift contributors, 2026-09-22. See CHANGES.md.
import {Link} from 'react-router-dom'
import {ArrowRight, Play} from 'lucide-react'
import {$api} from '../utils/http'
import {Button} from '../components/ui/button'
import {ListenEpisodeRow} from '../components/ListenEpisodeRow'
import {ListenLoading, ListenState} from '../components/ListenState'
import {clock, minutes, plainText, playEpisode} from '../utils/listening'

export const Homepage = () => {
    const last = $api.useQuery('get', '/api/v1/podcasts/episode/lastwatched')
    const shows = $api.useQuery('get', '/api/v1/podcasts')
    const timeline = $api.useQuery('get', '/api/v1/podcasts/timeline', {params: {query: {favoredOnly: false, notListened: false, favoredEpisodes: false}}})
    const recent = last.data?.find(item => {
        const position = item.episode.position ?? 0
        const duration = item.podcastEpisode.total_time || item.episode.total || 0
        return position > 0 && duration > 0 && position < duration * .98
    })
    if (timeline.isLoading || shows.isLoading) return <ListenLoading/>
    if (timeline.isError || shows.isError) return <ListenState title="Your library couldn't load" retry={() => {void timeline.refetch(); void shows.refetch()}}>Check your connection and try again.</ListenState>
    if (!shows.data?.length) return null
    const position = recent?.episode.position ?? 0
    const duration = recent ? (recent.podcastEpisode.total_time || recent.episode.total || 0) : 0
    const progress = duration > 0 ? Math.min(100, Math.round(position / duration * 100)) : 0
    return <>
        {recent && <section className="continue-card" aria-labelledby="continue-title">
            <PodcastArtwork src={recent.podcastEpisode.local_image_url || recent.podcast.image_url} alt={`${recent.podcast.name} cover`}/>
            <div className="continue-copy"><p className="eyebrow">Continue listening</p><p className="continue-show">{recent.podcast.name}</p>
                <h2 id="continue-title">{plainText(recent.podcastEpisode.name)}</h2>
                <div className="continue-progress"><progress value={progress} max="100" aria-label={`${progress}% complete`}/>
                    <p><span>{clock(position)} played</span><span>{minutes(Math.max(0, duration - position))} left</span></p>
                </div>
                <div className="continue-actions"><Button size="lg" onClick={() => void playEpisode(recent.podcastEpisode, position)}><Play fill="currentColor"/> Resume</Button><Button variant="ghost" size="lg" nativeButton={false} render={<Link to={`/learn?episode=${encodeURIComponent(recent.podcastEpisode.episode_id)}`}/>}>Open transcript <ArrowRight/></Button></div>
            </div>
        </section>}
        <section className="home-episodes"><div className="section-heading"><div><h2>Latest episodes</h2><p>New from the shows you follow. Read a brief, add one to your queue, or start listening.</p></div><Link to="/timeline">View all <ArrowRight size={14}/></Link></div>
            {timeline.data?.data.length ? timeline.data.data.slice(0, 5).map(item => <ListenEpisodeRow key={item.podcast_episode.id} episode={item.podcast_episode} show={item.podcast} history={item.history} briefAction/>) : <ListenState title="Episodes are on their way">Refresh this page after your feeds have finished loading.</ListenState>}
        </section>
        <section className="show-section" aria-label="Your podcasts"><div className="section-heading"><h2>Your podcasts</h2><Link to="/podcasts">View library <ArrowRight size={14}/></Link></div>
            <div className="show-shelf">{shows.data.slice(0, 6).map(show => <Link key={show.id} className="show-tile" to={`/podcasts/${show.id}/episodes`}><PodcastArtwork src={show.image_url} alt={`${show.name} cover`} loading="lazy"/><span>{show.name}</span><small>{show.author || 'Podcast'}</small></Link>)}</div>
        </section>
    </>
}
