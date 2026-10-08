import {useQuery} from '@tanstack/react-query'
import {fetchWorthHearing, WORTH_KEY} from './api'

/** The sidebar nav count: how many episodes are waiting on "Worth hearing". Nothing when there are none. */
export function WorthBadge() {
    const worth = useQuery({queryKey: WORTH_KEY, queryFn: fetchWorthHearing, staleTime: 60_000, retry: false})
    const count = worth.data?.hear.length ?? 0
    return count > 0 ? <span className="nav-count">{count}</span> : null
}
