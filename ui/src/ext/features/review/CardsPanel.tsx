// The episode workspace's "Cards" tab: this episode's flashcards, and a button to make them.
import {useTranslation} from 'react-i18next'
import {Link} from 'react-router-dom'
import {useMutation, useQuery, useQueryClient} from '@tanstack/react-query'
import {LoaderCircle, Sparkles} from 'lucide-react'
import {Button} from '../../../components/ui/button'
import {MakeTranscript} from '../../shared/MakeTranscript'
import type {EpisodeCtx} from '../../types'
import {cardsKey, dueKey, fetchCards, makeCards} from './api'

export function CardsPanel(ctx: EpisodeCtx) {
    return <Panel key={ctx.episodeId} {...ctx}/>
}

function Panel({episodeId, episode}: EpisodeCtx) {
    const {t} = useTranslation('review')
    const client = useQueryClient()
    const cards = useQuery({queryKey: cardsKey(episodeId), queryFn: () => fetchCards(episodeId), retry: false})
    const make = useMutation({
        mutationFn: (regenerate: boolean) => makeCards(episodeId, regenerate),
        onSuccess: made => {
            client.setQueryData(cardsKey(episodeId), made)
            void client.invalidateQueries({queryKey: dueKey})
        },
    })
    if (cards.isLoading) return <p role="status" className="review-muted">{t('loading')}</p>
    const data = cards.data
    if (!data) return <div className="review-actions"><p role="alert">{t('load-error')}</p>
        <Button variant="outline" size="sm" onClick={() => void cards.refetch()}>{t('try-again')}</Button></div>

    if (data.cards.length === 0 && data.has_transcript === false) return <div className="review-transcript">
        <p>{t('no-transcript')}</p>
        <MakeTranscript key={episode.id} episode={episode} onReady={() => void cards.refetch()}/></div>

    return <div className="review-cards">
        {data.cards.length === 0 && !data.ai_ready && <p className="review-muted">{t('no-ai')} <Link to="/settings/ai">{t('connect-ai')}</Link></p>}
        {data.cards.length === 0 && data.ai_ready && <Button disabled={make.isPending} onClick={() => make.mutate(false)}>
            {make.isPending ? <LoaderCircle className="animate-spin"/> : <Sparkles/>}{t('make-cards')}</Button>}
        {data.cards.length > 0 && <>
            <ol className="review-card-list">{data.cards.map(card => <li key={card.id}>
                <p className="review-q">{card.question}</p><p className="review-a">{card.answer}</p></li>)}</ol>
            {data.ai_ready && <Button variant="link" size="sm" disabled={make.isPending} onClick={() => make.mutate(true)}>{t('regenerate')}</Button>}
        </>}
        {make.isError && <p role="alert" className="review-error">{make.error.message}</p>}
    </div>
}
