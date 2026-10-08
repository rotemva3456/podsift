import {useTranslation} from 'react-i18next'
import {useQuery} from '@tanstack/react-query'
import {badgeKey, cachedBrief, type Verdict} from './api'

/** HEAR, READ or SKIP as a small chip; the reason shows on hover. */
export function VerdictChip({verdict, reason, small = false}: {verdict: Verdict; reason?: string | null; small?: boolean}) {
    const {t} = useTranslation('brief')
    return <span className={`brief-chip brief-${verdict.toLowerCase()}${small ? ' brief-chip-small' : ''}`} title={reason ?? undefined}>
        <span className="sr-only">{t('verdict')} </span>{t(`verdict-${verdict}`)}
    </span>
}

/** The row badge: the verdict of a brief that already exists. It never makes a brief. */
export function BriefBadge({episodeId}: {episodeId: string}) {
    const brief = useQuery({queryKey: badgeKey(episodeId), queryFn: () => cachedBrief(episodeId), staleTime: 60_000, retry: false})
    return brief.data?.verdict ? <VerdictChip verdict={brief.data.verdict} reason={brief.data.verdict_reason} small/> : null
}
