import {useTranslation} from 'react-i18next'
import {useQuery} from '@tanstack/react-query'
import {Link} from 'react-router-dom'
import {Sparkles} from 'lucide-react'
import {fetchWorthHearing, WORTH_KEY} from './api'

/** "N new episodes worth hearing", top of Listen now (§1.3 homeSection). Nothing while the setting
 *  is off or there is nothing to show yet: an empty autobrief_seen means an empty `hear` list, so
 *  checking the count alone already covers "off". */
export function WorthHome() {
    const {t} = useTranslation('worth')
    const worth = useQuery({queryKey: WORTH_KEY, queryFn: fetchWorthHearing, staleTime: 60_000, retry: false})
    const count = worth.data?.hear.length ?? 0
    if (!count) return null
    return <Link to="/worth-hearing" className="worth-home-card">
        <Sparkles size={18}/><span>{t('home-card', {count})}</span>
    </Link>
}
