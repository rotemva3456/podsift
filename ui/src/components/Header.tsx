// Modified by Podsift contributors, 2026-09-22. See CHANGES.md.
import {Search, Plus} from 'lucide-react'
import {Link, useLocation} from 'react-router-dom'
import {Button} from './ui/button'
import {ThemeSelector} from './ThemeSelector'
export const Header = () => {
    const location=useLocation()
    return <header className="listen-header"><Link to="/search" className="search-link"><Search size={17}/><span>Search your episodes</span></Link><div className="flex items-center gap-3"><ThemeSelector/>{!location.pathname.startsWith('/discover') && <Button variant="outline" nativeButton={false} render={<Link to="/discover"/>}><Plus/> <span className="hidden sm:inline">Add podcast</span></Button>}</div></header>
}
