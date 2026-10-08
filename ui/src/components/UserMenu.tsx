import {FC, useMemo} from 'react'
import {useQuery} from '@tanstack/react-query'
import { CustomDropdownMenu, MenuItem } from './CustomDropdownMenu'
import { CircleUserRound, Info, LogOut, Settings, Users } from 'lucide-react'
import useCommon from "../store/CommonSlice";
import {$api} from "../utils/http";
import {removeLogin} from "../utils/login";
import {ADMIN_ROLE} from "../models/constants";
import {enqueueSnackbar} from '@/utils/toast'
import {queryClient} from '../utils/socketio'
import {clearPrivateAccountState, hostedLogout, hostedProfile, isHostedRuntime} from '../ext/features/hosted-auth/session'


const AccountTrigger = ()=>{
    const username = useCommon(state => state.loginData)

    return <>
        <span className="hidden md:block ui-text">{username?.username}</span>
        <CircleUserRound className="ui-text hover:ui-text-hover" size={20} />
    </>
}

export const UserMenu: FC = () => {
    const hosted = isHostedRuntime()
    const configModel = $api.useQuery('get', '/api/v1/sys/config', {}, {enabled: !hosted})
    const legacy = $api.useQuery('get', '/api/v1/users/{username}', {
        params: {path: {username: 'me'}},
    }, {enabled: !hosted})
    const hostedQuery = useQuery({queryKey: ['hosted', 'profile'], queryFn: hostedProfile, enabled: hosted})
    const data = hosted ? hostedQuery.data : legacy.data
    const isLoading = hosted ? hostedQuery.isLoading : legacy.isLoading

    const menuItems: Array<MenuItem> = useMemo(()=>{
        if (isLoading || !data) {
            return []
        }
        const menuItems: Array<MenuItem> = [

        ]

        if (data.role === ADMIN_ROLE) {

        }

        menuItems.push({
            icon: <CircleUserRound size={16} />,
            translationKey: 'profile',
            path: 'profile'
        })

        if (data.role === 'admin') {
            menuItems.push({
                icon: <Settings size={16} />,
                translationKey: 'settings',
                path: 'settings'
            })
            menuItems.push({
                icon: <Info size={16} />,
                translationKey: 'system-info',
                path: 'info'
            })
        }

        if (data.role === 'admin') {
            menuItems.push({
                icon: <Users size={16} />,
                translationKey: 'administration',
                path: 'administration'
            })
        }

        if (isHostedRuntime() || configModel?.data?.oidcConfigured || configModel?.data?.basicAuth) {
            menuItems.push({
                icon: <LogOut size={16} />,
                translationKey: 'logout',
                onClick: async () => {
                    if (isHostedRuntime()) {
                        try {
                            await hostedLogout()
                            clearPrivateAccountState(queryClient)
                            window.location.assign('/ui/login')
                        } catch {
                            enqueueSnackbar('Could not sign out. Check the service and try again.', {variant: 'error'})
                        }
                        return
                    }
                    removeLogin()
                    window.location.reload()
                }
            })
        }
        return menuItems
    }, [configModel, data, isLoading])


    return (
        <CustomDropdownMenu menuItems={menuItems} trigger={<AccountTrigger/>} />
    )
}
