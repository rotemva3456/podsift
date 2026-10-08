// Modified by Podsift contributors, 2026-09-22. See CHANGES.md.
import {useState} from 'react'
import {Link,useParams} from 'react-router-dom'
import {useInfiniteQuery} from '@tanstack/react-query'
import {ArrowLeft,Search,Settings} from 'lucide-react'
import {$api,client} from '../utils/http'
import {Button} from '../components/ui/button'
import {Input} from '../components/ui/input'
import {ListenEpisodeRow} from '../components/ListenEpisodeRow'
import {ListenLoading,ListenState} from '../components/ListenState'
import {plainText} from '../utils/listening'
import {PodcastSettingsModal} from '../components/PodcastSettingsModal'
export const PodcastDetailPage=()=>{
    const {id=''}=useParams(),[search,setSearch]=useState('')
    const show=$api.useQuery('get','/api/v1/podcasts/{id}',{params:{path:{id}}})
    const episodes=useInfiniteQuery({queryKey:['show-episodes',id],initialPageParam:'',queryFn:async({pageParam})=>{
        const response=await client.GET('/api/v1/podcasts/{id}/episodes',{params:{path:{id},query:{only_unlistened:false,...(pageParam?{last_podcast_episode:pageParam}:{})}}})
        return response.data ?? []
    },getNextPageParam:page=>page.length ? page.at(-1)?.podcastEpisode.episode_id : undefined})
    if(show.isLoading)return <ListenLoading/>
    if(show.isError||!show.data)return <ListenState title="This show couldn't load" retry={()=>void show.refetch()}/>
    const items=episodes.data?.pages.flat().filter(item=>item.podcastEpisode.name.toLowerCase().includes(search.toLowerCase())) ?? []
    return <><Link to="/podcasts" className="back-link"><ArrowLeft size={16}/> Your podcasts</Link><div className="workspace-heading"><img src={show.data.image_url} alt=""/><div><p className="eyebrow">{show.data.author}</p><h1>{show.data.name}</h1><p className="show-summary">{plainText(show.data.summary ?? '')}</p><a className="text-xs text-muted-foreground" href={show.data.rssfeed} target="_blank" rel="noreferrer">Original RSS feed ↗</a></div></div>
        <div className="section-heading"><h2>Episodes</h2><PodcastSettingsModal podcast={show.data}/></div><div className="transcript-search mb-4"><Search size={16}/><Input aria-label="Filter episodes" value={search} onChange={e=>setSearch(e.target.value)} placeholder="Find an episode in the loaded list"/></div>
        {episodes.isLoading?<ListenLoading/>:episodes.isError?<ListenState title="Episodes couldn't load" retry={()=>void episodes.refetch()}/>:!items.length?<ListenState title={search?'No matching episodes loaded':'No episodes yet'}>{search?'Load more episodes below or try another word.':'This feed has no episodes yet.'}</ListenState>:items.map(item=><ListenEpisodeRow key={item.podcastEpisode.id} episode={item.podcastEpisode} show={show.data} history={item.podcastHistoryItem}/>)}
        {episodes.hasNextPage&&<div className="flex justify-center mt-6"><Button variant="outline" disabled={episodes.isFetchingNextPage} onClick={()=>void episodes.fetchNextPage()}>{episodes.isFetchingNextPage?'Loading…':'Load more episodes'}</Button></div>}
    </>
}
