// /queue/cut: one plan across the Listen next queue, then Smart Play or one MP3.
import {useTranslation} from 'react-i18next'
import {Link} from 'react-router-dom'
import {ArrowLeft} from 'lucide-react'
import {Button} from '../../../components/ui/button'
import {ListenLoading} from '../../../components/ListenState'
import {useListenQueue} from '../../../hooks/useListenQueue'
import {fetchEpisode, plainText, playEpisode} from '../../../utils/listening'
import {CutWorkspace} from './CutWorkspace'

export function QueueCut() {
    const {t} = useTranslation('cuts')
    const queue = useListenQueue()
    const episodes = queue.items.map(item => item.podcastEpisode)
    const titles = Object.fromEntries(episodes.map(episode => [episode.episode_id, plainText(episode.name)]))
    const minutes = Math.round(episodes.reduce((total, episode) => total + episode.total_time, 0) / 60)
    const play = async (episodeId: string, seconds: number) => {
        const {podcastEpisode} = await fetchEpisode(episodeId)
        await playEpisode(podcastEpisode, seconds)
    }
    let body
    if (queue.isLoading) body = <ListenLoading/>
    else if (queue.isError) body = <div className="cut-state" role="alert"><h3>{t('queue-error')}</h3>
        <Button variant="outline" onClick={() => void queue.refetch()}>{t('try-again')}</Button></div>
    else if (!episodes.length) body = <div className="cut-state" role="status"><h3>{t('queue-empty')}</h3><p>{t('queue-empty-hint')}</p></div>
    else body = <CutWorkspace target={{kind: 'queue', episodeIds: episodes.map(episode => episode.episode_id)}} titles={titles}
        onPlay={(episodeId, seconds) => void play(episodeId, seconds).catch(() => {})}/>
    return <div className="cut-panel cut-queue">
        <Link to="/queue" className="back-link"><ArrowLeft size={16}/> {t('back-to-queue')}</Link>
        <div className="page-title"><h1>{t('queue-title')}</h1>
            {episodes.length > 0 && <p>{t('queue-intro', {count: episodes.length, minutes})}</p>}</div>
        {body}
    </div>
}
