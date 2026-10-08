// Modified by Podsift contributors, 2026-09-30. See CHANGES.md.
import {useEffect, useState} from 'react'
import {BookOpen, Bookmark, ChevronDown, Compass, Headphones, Home, Library, ListMusic, MoreHorizontal, Settings} from 'lucide-react'
import {useTranslation} from 'react-i18next'
import {NavLink, useLocation} from 'react-router-dom'
import {navLinks} from '../ext/registry'
import {Button} from './ui/button'
import {Popover, PopoverContent, PopoverTitle, PopoverTrigger} from './ui/popover'

const primaryLinks = [
    {path: '/home/view', label: 'Today', mobileLabel: 'Today', icon: Home},
    {path: '/podcasts', label: 'Library', mobileLabel: 'Library', icon: Library},
    {path: '/learn', label: 'Learn', mobileLabel: 'Learn', icon: BookOpen},
    {path: '/knowledge', label: 'Knowledge', mobileLabel: 'Knowledge', icon: Bookmark},
]

const absolutePath = (path: string) => `/${path.replace(/^\/+/, '')}`
const atPath = (current: string, path: string) => current === path || current.startsWith(`${path}/`)

export const Sidebar = () => {
    const {t} = useTranslation()
    const location = useLocation()
    const features = navLinks.map(link => ({...link, path: absolutePath(link.path), label: t(link.label, {ns: link.featureId})}))
    const moreLinks = [
        {path: '/discover', label: 'Find podcasts', icon: Compass, badge: undefined},
        {path: '/queue', label: 'Listen next', icon: ListMusic, badge: undefined},
        ...features,
    ]
    const moreActive = moreLinks.some(link => atPath(location.pathname, link.path))
    const [moreOpen, setMoreOpen] = useState(moreActive)
    const [mobileMoreOpen, setMobileMoreOpen] = useState(false)

    useEffect(() => {if (moreActive) setMoreOpen(true)}, [location.pathname, moreActive])

    return <>
        <aside className="listen-sidebar">
            <NavLink to="/home/view" className="listen-brand"><span><Headphones size={22}/></span>Podsift</NavLink>
            <nav aria-label="Main navigation">
                {primaryLinks.map(({path, label, icon: Icon}) => <NavLink key={path} to={path}><Icon size={19}/>{label}</NavLink>)}
                <Button variant="ghost" className="listen-more-toggle" aria-expanded={moreOpen} onClick={() => setMoreOpen(!moreOpen)}>
                    <MoreHorizontal size={19}/> More tools <ChevronDown className={moreOpen ? 'more-chevron open' : 'more-chevron'} size={15}/>
                </Button>
                {moreOpen && <div className="listen-more-links">
                    {moreLinks.map(({path, label, icon: Icon, badge: Badge}) => <NavLink key={path} to={path}><Icon size={18}/>{label}{Badge && <Badge/>}</NavLink>)}
                </div>}
            </nav>
            <div className="sidebar-bottom"><NavLink to="/settings"><Settings size={18}/> Settings</NavLink><a href="https://github.com/SamTV12345/PodFetch" target="_blank" rel="noreferrer">Built on PodFetch ↗</a></div>
        </aside>
        <nav className="listen-mobile-nav" aria-label="Mobile navigation">
            {primaryLinks.map(({path, mobileLabel, icon: Icon}) => <NavLink key={path} to={path}><Icon size={20}/><span>{mobileLabel}</span></NavLink>)}
            <Popover open={mobileMoreOpen} onOpenChange={setMobileMoreOpen}>
                <PopoverTrigger render={<Button variant="ghost" className={moreActive || atPath(location.pathname, '/settings') ? 'mobile-more-trigger active' : 'mobile-more-trigger'} aria-label="More tools"><MoreHorizontal size={20}/><span>More</span></Button>}/>
                <PopoverContent side="top" align="end" sideOffset={8} className="mobile-more-popover">
                    <PopoverTitle className="sr-only">More tools</PopoverTitle>
                    <nav className="mobile-more-links" aria-label="More navigation">
                        {[...moreLinks, {path: '/settings', label: 'Settings', icon: Settings, badge: undefined}].map(({path, label, icon: Icon, badge: Badge}) => <NavLink key={path} to={path} onClick={() => setMobileMoreOpen(false)}><Icon size={18}/>{label}{Badge && <Badge/>}</NavLink>)}
                    </nav>
                </PopoverContent>
            </Popover>
        </nav>
    </>
}
