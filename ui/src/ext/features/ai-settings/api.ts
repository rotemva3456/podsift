// The AI settings API. The key is write-only: it goes up in a
// PUT (or in one Test / model-list request) and never comes back; responses carry key_hint only.
import {companion} from '../../../utils/companion'

export type ProviderId = 'openai' | 'groq' | 'openrouter' | 'ollama' | 'custom'

export type Preset = {
    id: ProviderId; label: string; base_url: string; needs_key: boolean; key_url: string | null
    max_input_chars: number; suggested_models: string[]
}

export type AiSettings = {
    provider: ProviderId; base_url: string; model: string; max_input_chars: number
    key_set: boolean; key_hint: string | null
    configured: boolean                              // Ask, briefs and cuts can use AI
    from_env: ('base_url' | 'api_key' | 'model')[]   // set in the server's .env, read-only here
    problem: string | null; saved: boolean; presets: Preset[]
}

/** What the form sends. Locked (env) fields are left out; api_key only when the user typed one. */
export type AiDraft = {
    provider?: ProviderId; base_url?: string; model?: string; max_input_chars?: number
    api_key?: string; clear_key?: boolean
}

export type TestResult = {ok: boolean; needs: 'base_url' | 'key' | 'model' | null; message: string; seconds?: number}
export type ModelList = {models: {id: string; context: number | null}[]; suggested: string | null}

export const SETTINGS_KEY = ['ai-settings']
const json = (method: string, body: AiDraft) => ({method, body: JSON.stringify(body)})

export const getAiSettings = () => companion<AiSettings>('/settings/ai')
export const saveAiSettings = (draft: AiDraft) => companion<AiSettings>('/settings/ai', json('PUT', draft))
export const testAiSettings = (draft: AiDraft) => companion<TestResult>('/settings/ai/test', json('POST', draft))
export const listModels = (draft: AiDraft) => companion<ModelList>('/settings/ai/models', json('POST', draft))
