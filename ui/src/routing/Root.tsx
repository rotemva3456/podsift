// Modified by Podsift contributors, 2026-09-22. See CHANGES.md.
import {ListenSession} from '../components/ListenSession'
import { useEffect } from 'react'
import { Outlet } from 'react-router-dom'
import App from '../App'
import { AudioComponents } from '../components/AudioComponents'
import { EpisodeSearchModal } from '../components/EpisodeSearchModal'
import { Header } from '../components/Header'
import { MainContentPanel } from '../components/MainContentPanel'
import { Sidebar } from '../components/Sidebar'
import { $api } from '../utils/http'
import { connectSocket } from '../utils/socketio'


export const Root = () => {
    const user = $api.useQuery('get', '/api/v1/users/{username}', {
        params: { path: { username: 'me' } }
    })

    useEffect(() => {
        if (user.data) {
            connectSocket(user.data.apiKey || '')
        }
    }, [user.data])

    return (
        <App>
            <a className="skip-link" href="#main-content">Skip to content</a>
            <div className="listen-shell">
                <Sidebar />
                <MainContentPanel>
                    <Header />
                    <div className="grid grid-rows-[1fr_auto] pb-8">
                        <Outlet />
                    </div>
                </MainContentPanel>
                <AudioComponents />
                <ListenSession/>
                <EpisodeSearchModal />
            </div>
        </App>
    )
}
