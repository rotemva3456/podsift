// Modified by Podsift contributors, 2026-09-22. See CHANGES.md.
import {PropsWithChildren} from 'react'
export const MainContentPanel = ({children}:PropsWithChildren) => <main id="main-content" tabIndex={-1} className="listen-main"><div className="listen-content">{children}</div></main>
