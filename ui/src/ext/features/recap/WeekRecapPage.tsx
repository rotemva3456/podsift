// The /recap page: this week's episodes, minutes, key ideas and highlights,
// with "Copy as Markdown" and "Download .md".
import {useState} from 'react'
import {useTranslation} from 'react-i18next'
import {Link} from 'react-router-dom'
import {useQuery} from '@tanstack/react-query'
import {Copy, Download} from 'lucide-react'
import {Button} from '../../../components/ui/button'
import {ListenLoading, ListenState} from '../../../components/ListenState'
import {clock} from '../../../utils/listening'
import {fetchWeek, type WeekRecap, weekKey} from './api'

/** Plain, portable Markdown of one week's recap: episodes, key ideas and highlights. */
export function toMarkdown(week: WeekRecap): string {
    const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`
    const lines: string[] = ['# This week', '',
        `${plural(week.episodes.length, 'episode')} · ${week.minutes} min · ${plural(week.highlights.length, 'highlight')}`, '']
    if (week.episodes.length) {
        lines.push('## Episodes', '')
        for (const ep of week.episodes) lines.push(`- ${ep.title ?? ep.episode_id}${ep.podcast_name ? ` (${ep.podcast_name})` : ''}`)
        lines.push('')
    }
    if (week.key_ideas.length) {
        lines.push('## Key ideas', '')
        for (const idea of week.key_ideas) lines.push(`- ${idea.text} — *${idea.title ?? idea.episode_id}*`)
        lines.push('')
    }
    if (week.highlights.length) {
        lines.push('## Highlights', '')
        for (const h of week.highlights) lines.push(`- "${(h.quote ?? h.text).trim()}"`)
        lines.push('')
    }
    return lines.join('\n')
}

export function WeekRecapPage() {
    const {t} = useTranslation('recap')
    const week = useQuery({queryKey: weekKey, queryFn: fetchWeek, retry: false})
    const [copied, setCopied] = useState(false)

    const copy = async () => {
        if (!week.data) return
        try {
            await navigator.clipboard.writeText(toMarkdown(week.data))
            setCopied(true)
            setTimeout(() => setCopied(false), 2000)
        } catch {
            // clipboard access can be denied by the browser; Download still works
        }
    }
    const download = () => {
        if (!week.data) return
        const url = URL.createObjectURL(new Blob([toMarkdown(week.data)], {type: 'text/markdown'}))
        const link = document.createElement('a')
        link.href = url
        link.download = 'this-week.md'
        link.click()
        URL.revokeObjectURL(url)
    }

    return <>
        <div className="page-title"><h1>{t('week-title')}</h1><p>{t('week-intro')}</p></div>
        {week.isLoading ? <ListenLoading/>
            : week.isError || !week.data ? <ListenState title={t('week-error')} retry={() => void week.refetch()}/>
            : week.data.episodes.length === 0 && week.data.highlights.length === 0
                ? <ListenState title={t('week-empty-title')}><p>{t('week-empty')}</p></ListenState>
            : <WeekBody week={week.data} onCopy={() => void copy()} onDownload={download} copied={copied}/>}
    </>
}

function WeekBody({week, onCopy, onDownload, copied}: {
    week: WeekRecap; onCopy: () => void; onDownload: () => void; copied: boolean
}) {
    const {t} = useTranslation('recap')
    const episodes = `${week.episodes.length} episode${week.episodes.length === 1 ? '' : 's'}`
    const highlights = `${week.highlights.length} highlight${week.highlights.length === 1 ? '' : 's'}`
    return <>
        <div className="recap-week-bar">
            <p className="recap-week-summary">{t('week-summary', {episodes, minutes: `${week.minutes} min`, highlights})}</p>
            <div className="recap-week-actions">
                <Button variant="outline" size="sm" onClick={onCopy}><Copy size={14}/>{copied ? t('copied') : t('copy-markdown')}</Button>
                <Button variant="outline" size="sm" onClick={onDownload}><Download size={14}/>{t('download-md')}</Button>
            </div>
        </div>
        {week.episodes.length > 0 && <div className="recap-section">
            <h2>{t('episodes')}</h2>
            <ul className="recap-week-episodes">{week.episodes.map(ep => <li key={ep.episode_id}>
                <Link to={`/learn?episode=${ep.episode_id}`}>{ep.title ?? ep.episode_id}</Link>
                {ep.podcast_name && <span className="recap-muted"> — {ep.podcast_name}</span>}
                {ep.heard_seconds !== null && ep.total_seconds !== null &&
                    <span className="recap-muted"> · {clock(ep.heard_seconds)} / {clock(ep.total_seconds)}</span>}
            </li>)}</ul></div>}
        {week.key_ideas.length > 0 && <div className="recap-section">
            <h2>{t('key-ideas')}</h2>
            <ul className="recap-list">{week.key_ideas.map((idea, index) => <li key={index}>
                <Link to={`/learn?episode=${idea.episode_id}&at=${idea.citations[0]?.start ?? 0}`} className="recap-time">
                    {idea.title ?? idea.episode_id}</Link><span>{idea.text}</span></li>)}</ul></div>}
        {week.highlights.length > 0 && <div className="recap-section">
            <h2>{t('highlights')}</h2>
            <ul className="recap-list recap-highlights">{week.highlights.map(h => <li key={h.id}>
                <Link to={`/learn?episode=${h.episode_id}&at=${h.start ?? h.position}`} className="recap-time">
                    {clock(h.start ?? h.position)}</Link><blockquote>{h.quote ?? h.text}</blockquote></li>)}</ul></div>}
    </>
}
