import {Headphones} from 'lucide-react'
import {Button} from './ui/button'
export function ListenState({title, children, retry}: {title: string; children?: React.ReactNode; retry?: () => void}) {
    return <div className="listen-state" role={retry ? 'alert' : 'status'}><Headphones size={28}/><h2>{title}</h2><div>{children}</div>{retry && <Button variant="outline" onClick={retry}>Try again</Button>}</div>
}
export function ListenLoading() {return <div className="listen-loading" role="status" aria-label="Loading episodes">{[1,2,3].map(n=><div key={n} className="animate-pulse bg-muted h-24 rounded-lg"/>)}<span className="sr-only">Loading episodes…</span></div>}
