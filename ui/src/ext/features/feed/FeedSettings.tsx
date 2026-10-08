// Settings → Podcast app feeds: a link (with a QR code) for a show's own RSS, with AI + sponsor
// chapters added, and a second link for the cuts you've exported. Both go in your phone's normal
// podcast app, not a browser. The token shows once, right when it's made; after that this page can
// only offer a new one (which stops the old link working) -- it is never sent back to us.
import {useId, useState} from 'react'
import {useTranslation} from 'react-i18next'
import {useMutation, useQuery} from '@tanstack/react-query'
import {renderSVG} from 'uqr'
import {CircleCheck, Copy, Info, QrCode, RefreshCw} from 'lucide-react'
import {Button} from '../../../components/ui/button'
import type {Show} from '../../../utils/listening'
import {podfetch} from '../../shared/podfetch'
import {FEED_STATUS_KEY, type FeedLinks, getFeedStatus, makeFeedToken} from './api'
import './feed.css'

async function getShows(): Promise<Show[]> {
    const response = await podfetch('/api/v1/podcasts')
    if (!response.ok) throw new Error(`${response.status}`)
    return await response.json() as Show[]
}

function LinkCard({url, label}: {url: string; label: string}) {
    const {t} = useTranslation('feed')
    const [copied, setCopied] = useState(false)
    const svg = renderSVG(url, {ecc: 'M', border: 2})
    const copy = async () => {
        try {
            await navigator.clipboard.writeText(url)
            setCopied(true)
            setTimeout(() => setCopied(false), 2000)
        } catch {/* the clipboard may be unavailable; the field below is still selectable */}
    }
    return <div className="feed-card">
        <p className="feed-card-label"><QrCode size={14} aria-hidden/> {label}</p>
        <div className="feed-qr" aria-hidden dangerouslySetInnerHTML={{__html: svg}}/>
        <div className="feed-row">
            <input className="feed-link" readOnly value={url} aria-label={label}
                onFocus={event => event.currentTarget.select()}/>
            <Button type="button" variant="outline" onClick={() => void copy()}>
                {copied ? <CircleCheck size={14} aria-hidden/> : <Copy size={14} aria-hidden/>}
                {copied ? t('copied') : t('copy')}
            </Button>
        </div>
    </div>
}

export function FeedSettings() {
    const {t} = useTranslation('feed')
    const id = useId()
    const status = useQuery({queryKey: FEED_STATUS_KEY, queryFn: getFeedStatus, retry: false})
    const [links, setLinks] = useState<FeedLinks | null>(null)
    const [showId, setShowId] = useState('')
    const shows = useQuery({queryKey: ['feed', 'shows'], queryFn: getShows, retry: false, enabled: links !== null})
    const make = useMutation({mutationFn: makeFeedToken, onSuccess: setLinks})

    if (status.isPending) return <p role="status" className="feed-muted">{t('loading')}</p>
    if (status.isError) return <div role="alert" className="feed-state">
        <p>{t('load-error')}</p>
        <Button variant="outline" onClick={() => void status.refetch()}>{t('try-again')}</Button>
    </div>

    const showUrl = (showIdValue: string) => links ? links.shows_url_template.replace('{podcast_id}', showIdValue) : ''
    const label = status.data.configured ? t('new-link') : t('create-link')

    return <section className="feed-settings" aria-labelledby={`${id}-title`}>
        <h2 id={`${id}-title`}>{t('title')}</h2>
        <p className="feed-muted">{t('intro')}</p>

        {!links && status.data.configured && <p className="feed-status"><Info size={16} aria-hidden/> {t('already-made')}</p>}
        {links && <p className="feed-status" data-on=""><CircleCheck size={16} aria-hidden/> {t('made')}</p>}

        <div className="feed-actions">
            <Button type="button" variant={links ? 'outline' : 'default'} disabled={make.isPending}
                onClick={() => make.mutate()}>
                <RefreshCw size={14} aria-hidden/> {label}
            </Button>
            {links && <p className="feed-hint">{t('new-link-hint')}</p>}
        </div>
        {make.isError && <p role="alert" className="feed-error">{t('create-error')} {make.error.message}</p>}

        {links && <>
            <LinkCard url={links.cuts_url} label={t('cuts-feed')}/>

            <div className="feed-field">
                <label htmlFor={`${id}-show`}>{t('pick-show')}</label>
                <select id={`${id}-show`} value={showId} onChange={event => setShowId(event.target.value)}>
                    <option value="">{t('pick-show-placeholder')}</option>
                    {shows.data?.map(show => <option key={show.id} value={show.id}>{show.name}</option>)}
                </select>
                {shows.isError && <p role="alert" className="feed-hint feed-bad">{t('shows-load-error')}</p>}
                <p className="feed-hint">{t('shows-hint')}</p>
            </div>
            {showId && <LinkCard url={showUrl(showId)} label={t('show-feed')}/>}
        </>}

        <p className="feed-hint">{t('reach-hint')}</p>
        <p className="feed-hint">{t('chapters-hint')}</p>
    </section>
}
