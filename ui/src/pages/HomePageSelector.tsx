// Modified by Podsift contributors, 2026-09-22; 2026-09-26: feature sections (ui/src/ext). See CHANGES.md.
import {Outlet} from 'react-router-dom'
import {homeSections} from '../ext/registry'
export const HomePageSelector = () => <><div className="page-title"><h1>Today</h1><p>Pick up where you left off, or choose what to hear next.</p></div>
    {homeSections.length > 0 && <div className="home-sections">{homeSections.map(({featureId, Component}) => <Component key={featureId}/>)}</div>}
    <Outlet/></>
