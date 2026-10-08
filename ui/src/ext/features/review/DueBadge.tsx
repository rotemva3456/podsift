import {useQuery} from '@tanstack/react-query'
import {dueKey, fetchDue} from './api'

/** The nav badge: how many cards are due now. Renders nothing at 0, or while it can't be told. */
export function DueBadge() {
    const due = useQuery({queryKey: dueKey, queryFn: () => fetchDue(), staleTime: 30_000, retry: false, refetchInterval: 60_000})
    return due.data && due.data.count > 0 ? <span className="nav-count">{due.data.count}</span> : null
}
