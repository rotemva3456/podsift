import {Link} from 'react-router-dom'
import {useTranslation} from 'react-i18next'
import {ArrowDown, ArrowUp, Play, Scissors, X} from 'lucide-react'
import {useListenQueue} from '../hooks/useListenQueue'
import {Button} from '../components/ui/button'
import {ListenEpisodeRow} from '../components/ListenEpisodeRow'
import {ListenLoading, ListenState} from '../components/ListenState'
import {minutes, playEpisode} from '../utils/listening'
export const ListenQueue = () => {
    const queue = useListenQueue()
    const {t} = useTranslation('cuts')
    const move = (index:number, delta:number) => {
        const ids=queue.items.map(item=>item.podcastEpisode.id)
        const from=ids[index],to=ids[index+delta]
        if (!from||!to) return
        ids[index]=to;ids[index+delta]=from
        void queue.update({ids}).catch(()=>{})
    }
    return <><div className="page-title flex justify-between items-start gap-4"><div><h1>Listen next</h1><p>{queue.items.length ? `${queue.items.length} episodes · ${minutes(queue.items.reduce((total,item)=>total+item.podcastEpisode.total_time,0))}`:'A short list for your next walk, drive, or quiet hour.'}</p></div>{queue.items[0] && <div className="flex flex-wrap gap-2 justify-end"><Button variant="outline" nativeButton={false} render={<Link to="/queue/cut"/>}><Scissors/> {t('cut-my-queue')}</Button><Button onClick={()=>void playEpisode(queue.items[0]!.podcastEpisode, queue.items[0]!.podcastHistoryItem?.position ?? 0)}><Play/> Play queue</Button></div>}</div>
        {queue.isLoading ? <ListenLoading/> : queue.isError ? <ListenState title="Your queue couldn't load" retry={()=>void queue.refetch()}/> : !queue.items.length ? <ListenState title="Your queue is empty"><p>Use the queue button beside an episode to add it here.</p><Button className="mt-4" variant="outline" nativeButton={false} render={<Link to="/home/view"/>}>Browse episodes</Button></ListenState> : queue.items.map((item,index)=><div className="queue-row" key={item.podcastEpisode.episode_id}><div className="queue-order"><span>{index+1}</span><Button size="icon" variant="ghost" aria-label={`Move episode ${index+1} up`} disabled={!index || queue.saving} onClick={()=>move(index,-1)}><ArrowUp size={14}/></Button><Button size="icon" variant="ghost" aria-label={`Move episode ${index+1} down`} disabled={index===queue.items.length-1 || queue.saving} onClick={()=>move(index,1)}><ArrowDown size={14}/></Button></div><ListenEpisodeRow episode={item.podcastEpisode} history={item.podcastHistoryItem}/></div>)}
        {queue.saveError && <p role="alert" className="text-destructive">Your queue wasn't updated. Please try again.</p>}
    </>
}
