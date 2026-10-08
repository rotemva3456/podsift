// AI with your own key: the Settings → AI tab. The server side is
// companion/routes/settings_ai.py; features use it through companion/llm.py (get_llm).
import type {Feature} from '../../types'
import {AiSettings} from './AiSettings'
import './ai-settings.css'

export const feature: Feature = {
    id: 'ai-settings',
    settingsTabs: [{path: 'ai', label: 'tab', element: <AiSettings/>}],
}
