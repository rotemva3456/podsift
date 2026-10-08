// Modified by Podsift contributors, 2026-09-24: feature settings tabs (ui/src/ext), and the open tab
// scrolls into view on phones. See CHANGES.md.
import {useEffect, useRef} from 'react'
import { useTranslation } from 'react-i18next'
import { Heading1 } from '../components/Heading1'
import {NavLink, Outlet, useLocation} from "react-router-dom";
import {getConfigFromHtmlFile} from "../utils/config";
import {settingsTabs} from "../ext/registry";

export const SettingsPage = () => {
    const { t } = useTranslation()
    const config = getConfigFromHtmlFile()
    const tabs = useRef<HTMLDivElement>(null)
    const {pathname} = useLocation()

    // On a phone the tabs are wider than the screen and scroll sideways. Keep the open tab in
    // view, so you can see where you are (feature tabs come last and start off-screen).
    useEffect(() => {
        tabs.current?.querySelector<HTMLElement>('a.active')
            ?.scrollIntoView?.({block: 'nearest', inline: 'center'})
    }, [pathname])

    return (
        <div>
            <Heading1 className="mb-10">{t('settings')}</Heading1>

            {/* Tabs */}
            <div ref={tabs} className={`
                scrollbox-x mb-10 py-2
                w-[calc(100vw-2rem)] ${/* viewport - padding */ ''}
                xs:w-[calc(100vw-4rem)] ${/* viewport - padding */ ''}
                md:w-[calc(100vw-18rem-4rem)] ${/* viewport - sidebar - padding */ ''}
            `}>
                <ul className="flex gap-2 border-b ui-border min-w-fit ui-text-muted settings-selector ">
                    <li className={`cursor-pointer inline-block px-2 py-4`}>
                        <NavLink to="retention" className="">
                            {t('data-retention')}
                        </NavLink>
                    </li>
                    <li className={`cursor-pointer inline-block px-2 py-4`}>
                        <NavLink to="opml">
                            {t('opml-export')}
                        </NavLink>
                    </li>
                    <li className={`cursor-pointer inline-block px-2 py-4`}>
                        <NavLink to="naming">
                            {t('podcast-naming')}
                        </NavLink>
                    </li>
                    <li className={`cursor-pointer inline-block px-2 py-4`}>
                        <NavLink to="podcasts">
                            {t('manage-podcasts')}
                        </NavLink>
                    </li>
                    <li className={`cursor-pointer inline-block px-2 py-4`}>
                        <NavLink to="rescan">
                            {t('rescan-audio-files')}
                        </NavLink>
                    </li>
                    <li className={`cursor-pointer inline-block px-2 py-4`}>
                        <NavLink to="gpodder">
                            {t('manage-gpodder-podcasts')}
                        </NavLink>
                    </li>
                    {config?.mopidyIntegrationEnabled && (
                        <li className={`cursor-pointer inline-block px-2 py-4`}>
                            <NavLink to="mopidy">
                                {t('manage-mopidy-servers')}
                            </NavLink>
                        </li>
                    )}
                    {settingsTabs.map(tab => (
                        <li key={tab.featureId + tab.path} className={`cursor-pointer inline-block px-2 py-4`}>
                            <NavLink to={tab.path}>
                                {t(tab.label, {ns: tab.featureId})}
                            </NavLink>
                        </li>
                    ))}
                </ul>
            </div>

            <div className="max-w-(--breakpoint-md)">
                <Outlet/>
            </div>

        </div>
    )
}
