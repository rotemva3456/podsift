import {PodcastArtwork} from '../components/PodcastArtwork'
// Modified by Podsift contributors, 2026-09-22. See CHANGES.md.
import {useState} from 'react'
import {useQueryClient} from '@tanstack/react-query'
import {Link} from 'react-router-dom'
import {Check, Plus, Rss, Search} from 'lucide-react'
import {$api} from '../utils/http'
import {Button} from '../components/ui/button'
import {Input} from '../components/ui/input'
import {ListenLoading, ListenState} from '../components/ListenState'
export const DiscoverPage = () => {
    const [text,setText]=useState(''),[term,setTerm]=useState(''),[feed,setFeed]=useState(''),[added,setAdded]=useState<string[]>([])
    const cache=useQueryClient()
    const results=$api.useQuery('get','/api/v1/podcasts/{type_of}/{podcast}/search',{params:{path:{type_of:0,podcast:term}}},{enabled:!!term,retry:false})
    const subscribe=$api.useMutation('post','/api/v1/podcasts/feed')
    const itunes=$api.useMutation('post','/api/v1/podcasts/itunes')
    const shows=$api.useQuery('get','/api/v1/podcasts')
    const refresh=()=>{void cache.invalidateQueries({queryKey:['get','/api/v1/podcasts']});void cache.invalidateQueries({queryKey:['get','/api/v1/podcasts/timeline']})}
    const data=results.data && 'results' in results.data ? results.data.results : []
    const busy=subscribe.isPending||itunes.isPending
    return <><div className="page-title"><h1>Find podcasts</h1><p>Search for a show, a person, or something you want to learn.</p></div>
        <form className="discover-search" onSubmit={e=>{e.preventDefault();setTerm(text.trim())}}><Input aria-label="Search podcasts" placeholder="Try networking, design, or a favorite host" value={text} onChange={e=>setText(e.target.value)}/><Button type="submit" disabled={!text.trim()}><Search/> Search</Button></form>
        <details className="rss-entry"><summary><Rss size={15}/> Have an RSS feed? Add it directly.</summary><form onSubmit={e=>{e.preventDefault();void subscribe.mutateAsync({body:{rssFeedUrl:feed}}).then(()=>{setAdded([...added,feed]);setFeed('');refresh()}).catch(()=>{})}}><Input type="url" aria-label="RSS feed URL" placeholder="https://example.com/podcast.xml" required value={feed} onChange={e=>setFeed(e.target.value)}/><Button type="submit" disabled={busy||!feed.trim()}>Add podcast</Button></form>{subscribe.isSuccess&&<p role="status">Podcast added. <Link to="/podcasts">Open your library →</Link></p>}{subscribe.isError&&<p role="alert">Couldn't add this feed. Check the address and try again.</p>}</details>
        {!term ? <div className="discover-start"><h2>What are you curious about?</h2><p>Choose a topic to search the podcast directory.</p><div>{['Networking','Technology','Science','History','Design','Language learning'].map(topic=><Button key={topic} variant="outline" onClick={()=>{setText(topic);setTerm(topic)}}>{topic}</Button>)}</div></div> : results.isLoading ? <ListenLoading/> : results.isError ? <ListenState title="Search couldn't connect" retry={()=>void results.refetch()}>You can still add a show using its RSS feed.</ListenState> : !data?.length ? <ListenState title="No podcasts found">Try a broader topic, a show name, or an RSS feed.</ListenState> : <><div className="section-heading mt-8"><h2>Results for “{term}”</h2><span className="text-xs text-muted-foreground">Podcast directory</span></div><div className="discovery-grid">{data.map(item=>{
            const followed=added.includes(String(item.trackId))||shows.data?.some(show=>show.name===item.collectionName)
            return <article className="discovery-item" key={item.trackId}><PodcastArtwork src={item.artworkUrl600 ?? item.artworkUrl100 ?? undefined} alt="" loading="lazy"/><div><h3>{item.collectionName}</h3><p>{item.artistName}</p><Button variant={followed?'secondary':'outline'} disabled={!!followed||busy} onClick={()=>void itunes.mutateAsync({body:{trackId:item.trackId!,userId:0}}).then(()=>{setAdded(current=>[...current,String(item.trackId)]);refresh()}).catch(()=>{})}>{followed?<Check size={14}/>:<Plus size={14}/>} {followed?'Following':'Follow podcast'}</Button></div></article>
        })}</div>{itunes.isError&&<p role="alert" className="text-destructive">Couldn't follow that show. Please try again.</p>}</>}
    </>
}
