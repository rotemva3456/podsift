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
    const recent = last.data?.find(item => (item.episode.position ?? 0) > 0 && (item.episode.position ?? 0) < (item.episode.total || item.podcastEpisode.total_time) * .98)
    if (timeline.isLoading || shows.isLoading) return <ListenLoading/>
    if (timeline.isError || shows.isError) return <ListenState title="Your library couldn't load" retry={() => {void timeline.refetch(); void shows.refetch()}}>Check your connection and try again.</ListenState>
    if (!shows.data?.length) return <ListenState title="Add your first podcast"><p>Follow a show to see its episodes here. Your place is saved as you listen.</p><Button className="mt-5" nativeButton={false} render={<Link to="/discover"/>}>Find a podcast <ArrowRight/></Button></ListenState>
    return <>
        {recent && <section className="continue-card" aria-labelledby="continue-title">
            <img src={recent.podcastEpisode.local_image_url} alt=""/>
            <div><p className="eyebrow" id="continue-title">Continue listening</p><p className="text-sm text-muted-foreground mb-2">{recent.podcast.name}</p>
                <h2>{plainText(recent.podcastEpisode.name)}</h2>
                <p className="text-sm text-muted-foreground mt-3">{clock(recent.episode.position ?? 0)} played · {minutes(Math.max(0, recent.podcastEpisode.total_time - (recent.episode.position ?? 0)))} left</p>
                <div className="flex gap-3 mt-5"><Button size="lg" onClick={() => void playEpisode(recent.podcastEpisode, recent.episode.position ?? 0)}><Play fill="currentColor"/> Resume</Button><Button variant="outline" size="lg" nativeButton={false} render={<Link to={`/learn?episode=${encodeURIComponent(recent.podcastEpisode.episode_id)}`}/>}>Open transcript</Button></div>
            </div>
        </section>}
        <section className="home-episodes"><div className="section-heading"><div><h2>Latest episodes</h2><p>Open a brief to decide what to hear. Play the full episode, or make a cut for later.</p></div><Link to="/timeline">View all <ArrowRight size={14}/></Link></div>
            {timeline.data?.data.length ? timeline.data.data.slice(0, 5).map(item => <ListenEpisodeRow key={item.podcast_episode.id} episode={item.podcast_episode} show={item.podcast} history={item.history} briefAction/>) : <ListenState title="Episodes are on their way">Refresh this page after your feeds have finished loading.</ListenState>}
        </section>
        <section className="show-section" aria-label="Your podcasts"><div className="section-heading"><h2>Your podcasts</h2><Link to="/podcasts">View library <ArrowRight size={14}/></Link></div>
            <div className="show-shelf">{shows.data.slice(0, 6).map(show => <Link key={show.id} className="show-tile" to={`/podcasts/${show.id}/episodes`}><img src={show.image_url} alt=""/><span>{show.name}</span><small>{show.author || 'Podcast'}</small></Link>)}</div>
        </section>
    </>
}
