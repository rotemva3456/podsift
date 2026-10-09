import {useState} from 'react'
import {useQuery} from '@tanstack/react-query'
import {useTranslation} from 'react-i18next'
import {Link} from 'react-router-dom'
import {ArrowUpRight, Bookmark, Search} from 'lucide-react'
import {Button} from '../../../components/ui/button'
import {Input} from '../../../components/ui/input'
import {Card, CardContent} from '../../../components/ui/card'
import {ListenLoading, ListenState} from '../../../components/ListenState'
import {companion, type Note} from '../../../utils/companion'
import {clock, plainText} from '../../../utils/listening'

type SavedIdea = Note & {kind?: 'note' | 'highlight'; start?: number | null; end?: number | null; quote?: string | null}
type Filter = 'all' | 'ideas' | 'notes'

export function KnowledgePage() {
    const {t} = useTranslation('knowledge')
    const [search, setSearch] = useState('')
    const [filter, setFilter] = useState<Filter>('all')
    const notes = useQuery({queryKey: ['notes', 'knowledge'], queryFn: () => companion<SavedIdea[]>('/notes')})
    const records = notes.data ?? []
    const query = search.trim().toLocaleLowerCase()
    const shown = records.filter(note => (filter === 'all' || (filter === 'ideas' ? note.kind === 'highlight' : note.kind !== 'highlight'))
        && (!query || [note.text, note.quote ?? '', plainText(note.title)].some(value => value.toLocaleLowerCase().includes(query))))
    const clear = () => {setSearch(''); setFilter('all')}

    return <>
        <div className="page-title"><h1>{t('title')}</h1><p>{t('intro')}</p></div>
        <div className="knowledge-controls">
            <div className="knowledge-search"><Search size={17} aria-hidden="true"/>
                <Input aria-label={t('search-label')} placeholder={t('search-placeholder')} value={search} onChange={event => setSearch(event.target.value)}/>
            </div>
            <div className="knowledge-filters" role="group" aria-label={t('filter-label')}>
                {(['all', 'ideas', 'notes'] as const).map(value => <Button key={value} size="sm" variant={filter === value ? 'secondary' : 'ghost'}
                    aria-pressed={filter === value} onClick={() => setFilter(value)}>{t(`filter-${value}`)}</Button>)}
            </div>
        </div>
        {notes.isLoading ? <ListenLoading/> : notes.isError ? <ListenState title={t('load-error')} retry={() => void notes.refetch()}>{t('load-error-hint')}</ListenState>
            : !records.length ? <ListenState title={t('empty-title')}><p>{t('empty-hint')}</p>
                <Button variant="outline" className="mt-4" nativeButton={false} render={<Link to="/home/view"/>}>{t('choose-source')}</Button>
            </ListenState> : <>
                <p className="knowledge-count" role="status">{t('count', {count: shown.length})}</p>
                {!shown.length ? <ListenState title={t('no-match')}><p>{t('no-match-hint')}</p>
                    <Button variant="outline" className="mt-4" onClick={clear}>{t('clear')}</Button>
                </ListenState> : <section className="knowledge-list" aria-label={t('list-label')}>
                    {shown.map(note => {
                        const source = `/learn?episode=${encodeURIComponent(note.episode_id)}&at=${note.start ?? note.position}`
                        return <Card key={note.id} className="knowledge-idea">
                            <CardContent className="knowledge-idea-content">
                                <p className="knowledge-kind"><Bookmark size={13} aria-hidden="true"/>{t(note.kind === 'highlight' ? 'saved-idea' : 'personal-note')}</p>
                                {note.quote ? <blockquote className="knowledge-quote">{note.quote}</blockquote>
                                    : <p className="knowledge-thought">{note.text}</p>}
                                {note.quote && note.text !== note.quote && <div className="knowledge-annotation">
                                    <p className="knowledge-annotation-label">{t('personal-note')}</p>
                                    <p className="knowledge-note">{note.text}</p>
                                </div>}
                                <footer className="knowledge-footer">
                                    <div className="knowledge-source-meta">
                                        <Link className="knowledge-source" to={source}><span>{plainText(note.title)}</span><ArrowUpRight size={15} aria-hidden="true"/></Link>
                                        <p className="knowledge-location">{clock(note.start ?? note.position)}{note.end != null ? ` – ${clock(note.end)}` : ''}
                                            {' · '}<time dateTime={note.created_at}>{new Date(note.created_at).toLocaleDateString()}</time></p>
                                    </div>
                                    <Link className="knowledge-open" to={source}>{t('open-source')} <ArrowUpRight size={14} aria-hidden="true"/></Link>
                                </footer>
                            </CardContent>
                        </Card>
                    })}
                </section>}
            </>}
    </>
}
