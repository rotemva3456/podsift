// The Listen now card: "This week: N episodes, M highlights", linking to
// /recap. It is how phones reach the recap page (they get only one bottom-bar link, and flashcards has
// it); renders nothing when there is nothing to show.
import {useTranslation} from 'react-i18next'
import {Link} from 'react-router-dom'
import {useQuery} from '@tanstack/react-query'
import {Rewind} from 'lucide-react'
import {fetchWeek, weekKey} from './api'

export function HomeRecapCard() {
    const {t} = useTranslation('recap')
    const week = useQuery({queryKey: weekKey, queryFn: fetchWeek, retry: false, staleTime: 60_000})
    const data = week.data
    if (!data || (data.episodes.length === 0 && data.highlights.length === 0)) return null
    const episodes = `${data.episodes.length} episode${data.episodes.length === 1 ? '' : 's'}`
    const highlights = `${data.highlights.length} highlight${data.highlights.length === 1 ? '' : 's'}`
    return <Link to="/recap" className="recap-home-card">
        <Rewind size={18}/>
        <span>{t('home-card', {episodes, highlights})}</span>
    </Link>
}
