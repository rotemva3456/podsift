import {useTranslation} from 'react-i18next'
import {useQuery} from '@tanstack/react-query'
import {Link} from 'react-router-dom'
import {ListenLoading, ListenState} from '../../../components/ListenState'
import {plainText} from '../../../utils/listening'
import {VerdictChip} from '../brief/Verdict'
import {fetchWorthHearing, type WorthItem, WORTH_KEY} from './api'

function Row({item}: {item: WorthItem}) {
    const {t} = useTranslation('worth')
    return <li className="worth-row">
        <Link to={`/learn?episode=${encodeURIComponent(item.episode_id)}`} className="worth-row-link">
            <span className="worth-row-meta">
                <VerdictChip verdict={item.verdict} reason={item.verdict_reason} small/>
                {item.percent_new != null && <span className="worth-muted worth-row-percent">{t('percent-new', {percent: item.percent_new})}</span>}
            </span>
            <span className="worth-row-copy">
                <span className="worth-row-title">{plainText(item.title ?? '')}</span>
                {item.summary && <span className="worth-muted">{item.summary}</span>}
            </span>
        </Link>
    </li>
}

/** HEAR episodes expanded, READ/SKIP collapsed below. */
export function WorthHearingPage() {
    const {t} = useTranslation('worth')
    const worth = useQuery({queryKey: WORTH_KEY, queryFn: fetchWorthHearing, retry: false})
    if (worth.isLoading) return <ListenLoading/>
    if (worth.isError) return <ListenState title={t('load-error')} retry={() => void worth.refetch()}/>
    const hear = worth.data?.hear ?? [], other = worth.data?.other ?? []
    if (!hear.length && !other.length) return worth.data?.login_blocks_auto
        ? <ListenState title={t('empty')}><p>{t('login-required')}</p></ListenState>
        : <ListenState title={t('empty')}>
            <p>{t('empty-hint')}</p>
            <Link to="/settings/worth-hearing">{t('open-settings')}</Link>
        </ListenState>
    return <>
        <div className="page-title"><h1>{t('title')}</h1><p>{t('intro')}</p></div>
        {hear.length > 0
            ? <ul className="worth-list">{hear.map(item => <Row key={item.episode_id} item={item}/>)}</ul>
            : <p className="worth-muted">{t('no-hear')}</p>}
        {other.length > 0 && <details className="worth-collapsed">
            <summary>{t('other-toggle', {count: other.length})}</summary>
            <ul className="worth-list">{other.map(item => <Row key={item.episode_id} item={item}/>)}</ul>
        </details>}
    </>
}
