// The "Cut" tab of the episode workspace.
import {useTranslation} from 'react-i18next'
import {useQuery} from '@tanstack/react-query'
import {LoaderCircle} from 'lucide-react'
import {Button} from '../../../components/ui/button'
import {MakeTranscript} from '../../shared/MakeTranscript'
import type {EpisodeCtx} from '../../types'
import {transcriptQuery} from './api'
import {CutWorkspace} from './CutWorkspace'

export function CutPanel({episodeId, episode, seek}: EpisodeCtx) {
    const {t} = useTranslation('cuts')
    const transcript = useQuery(transcriptQuery(episodeId))
    // A failed background refetch keeps the last data: keep working with it.
    const data = transcript.data
    let body
    if (!data && transcript.isError) body = <div className="cut-state" role="alert"><h3>{t('transcript-error')}</h3><p>{transcript.error.message}</p>
        <Button variant="outline" onClick={() => void transcript.refetch()}>{t('try-again')}</Button></div>
    else if (!data) body = <p role="status" className="cut-hint"><LoaderCircle size={14} className="animate-spin inline"/> {t('loading')}</p>
    else if (!data.text && !data.segments.length) body = <div className="cut-state" role="status">
        <h3>{t('no-transcript')}</h3><p>{t('no-transcript-hint')}</p>
        <MakeTranscript key={episode.id} episode={episode} onReady={() => void transcript.refetch()}/></div>
    else body = <CutWorkspace key={episodeId} target={{kind: 'episode', episodeId}} onPlay={(_, seconds) => seek(seconds)}/>
    return <div className="cut-panel">
        <h2>{t('title')}</h2>
        <p className="cut-intro">{t('intro')}</p>
        {body}
    </div>
}
