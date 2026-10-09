import {PodcastArtwork} from '../components/PodcastArtwork'
// Modified by Podsift contributors, 2026-09-22. See CHANGES.md.
import {FC,useState} from 'react'
import {Link} from 'react-router-dom'
import {Plus,Search} from 'lucide-react'
import {$api} from '../utils/http'
import {Button} from '../components/ui/button'
import {Input} from '../components/ui/input'
import {ListenLoading,ListenState} from '../components/ListenState'
export const Podcasts:FC<{onlyFavorites?:boolean}> = ({onlyFavorites}) => {
    const [search,setSearch]=useState('')
    const shows=$api.useQuery('get','/api/v1/podcasts')
    const filtered=shows.data?.filter(show=>(!onlyFavorites||show.favorites)&&show.name.toLowerCase().includes(search.toLowerCase()))
    return <><div className="page-title flex justify-between gap-4"><div><h1>{onlyFavorites?'Favorite podcasts':'Your podcasts'}</h1><p>{shows.data?.length ?? 0} shows in your library.</p></div><Button nativeButton={false} render={<Link to="/discover"/>}><Plus/> Add podcast</Button></div><div className="transcript-search mb-8"><Search size={16}/><Input aria-label="Filter your podcasts" placeholder="Find a show in your library" value={search} onChange={e=>setSearch(e.target.value)}/></div>
        {shows.isLoading?<ListenLoading/>:shows.isError?<ListenState title="Your podcasts couldn't load" retry={()=>void shows.refetch()}/>:!shows.data?.length?<ListenState title="Your library starts with one show"><Button nativeButton={false} render={<Link to="/discover"/>}>Find podcasts</Button></ListenState>:!filtered?.length?<ListenState title="No shows match that name">Try another search.</ListenState>:<div className="library-grid">{filtered.map(show=><Link className="library-show" key={show.id} to={`/podcasts/${show.id}/episodes`}><PodcastArtwork src={show.image_url} alt=""/><h2>{show.name}</h2><p>{show.author}</p><span>View episodes →</span></Link>)}</div>}
    </>
}
