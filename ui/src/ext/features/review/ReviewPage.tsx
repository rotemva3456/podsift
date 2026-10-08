// /review: due flashcards, scheduled with SM-2, plus "replay what I forget" and the exports.
import {useState} from 'react'
import {useTranslation} from 'react-i18next'
import {useMutation, useQuery, useQueryClient} from '@tanstack/react-query'
import {Button} from '../../../components/ui/button'
import {Exports} from './Exports'
import {Replay} from './Replay'
import {dueKey, fetchDue, gradeCard, type Grade} from './api'

const GRADES: Grade[] = ['again', 'hard', 'good', 'easy']

export function ReviewPage() {
    const {t} = useTranslation('review')
    const client = useQueryClient()
    const [shown, setShown] = useState(false)
    const due = useQuery({queryKey: dueKey, queryFn: () => fetchDue(), retry: false})
    const grade = useMutation({
        mutationFn: ({id, value}: {id: string; value: Grade}) => gradeCard(id, value),
        onSuccess: () => {
            setShown(false)
            void client.invalidateQueries({queryKey: dueKey})
        },
    })
    const cards = due.data?.cards ?? []
    const current = cards[0]

    let body
    if (due.isLoading) body = <p role="status" className="review-muted">{t('loading')}</p>
    else if (due.isError) body = <div className="review-state" role="alert"><p>{t('load-error')}</p>
        <Button variant="outline" size="sm" onClick={() => void due.refetch()}>{t('try-again')}</Button></div>
    else if (!current) body = <div className="review-state" role="status"><h3>{t('empty-title')}</h3><p>{t('empty')}</p></div>
    else body = <div className="review-card" key={current.id}>
        <p className="review-episode">{current.episode_title}</p>
        <p className="review-question">{current.question}</p>
        {shown ? <>
            <p className="review-answer">{current.answer}</p>
            <div className="review-grades">{GRADES.map(value => <Button key={value} variant="outline"
                disabled={grade.isPending} onClick={() => grade.mutate({id: current.id, value})}>{t(`grade-${value}`)}</Button>)}</div>
        </> : <Button onClick={() => setShown(true)}>{t('show-answer')}</Button>}
        {grade.isError && <p role="alert" className="review-error">{grade.error.message}</p>}
    </div>

    return <div className="review-page">
        <div className="page-title"><h1>{t('title')}</h1>
            {due.data && <p>{t('due-count', {count: due.data.count})}</p>}</div>
        {body}
        <Replay/>
        <Exports/>
    </div>
}
