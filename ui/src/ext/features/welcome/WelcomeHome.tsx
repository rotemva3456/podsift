// The welcome home card is shown only while the library is empty. Its primary action reuses
// PodFetch's /discover page; AI setup and listener topics remain optional preferences.
import {useState} from 'react'
import {useTranslation} from 'react-i18next'
import {useMutation, useQuery, useQueryClient} from '@tanstack/react-query'
import {Link} from 'react-router-dom'
import {Plus, Sparkles, X} from 'lucide-react'
import {Button} from '../../../components/ui/button'
import {Input} from '../../../components/ui/input'
import {AI_STATUS_KEY, fetchShows, getAiStatus, getProfile, PROFILE_KEY, saveProfile, SHOWS_KEY} from './api'

const capitalize = (word: string) => word ? word.charAt(0).toUpperCase() + word.slice(1) : word

function TopicsEditor() {
    const {t} = useTranslation('welcome')
    const client = useQueryClient()
    const profile = useQuery({queryKey: PROFILE_KEY, queryFn: getProfile, staleTime: 60_000, retry: false})
    const [draft, setDraft] = useState('')
    const topics = profile.data?.topics ?? []
    const save = useMutation({mutationFn: saveProfile, onSuccess: next => client.setQueryData(PROFILE_KEY, next)})

    function add(text: string) {
        const words = text.split(',').map(word => word.trim()).filter(Boolean)
        setDraft('')
        if (!words.length) return
        const next = [...topics]
        for (const word of words) if (!next.some(existing => existing.toLowerCase() === word.toLowerCase())) next.push(word)
        if (next.length !== topics.length) save.mutate(next)
    }
    const remove = (word: string) => save.mutate(topics.filter(existing => existing !== word))

    return <div className="welcome-topics">
        {topics.length > 0 && <ul className="welcome-topic-chips">
            {topics.map(word => <li key={word} className="welcome-topic-chip">{word}
                <button type="button" onClick={() => remove(word)} aria-label={t('remove-topic', {topic: word})}><X size={12}/></button>
            </li>)}
        </ul>}
        <form className="welcome-topic-form" onSubmit={event => {event.preventDefault(); add(draft)}}>
            <Input value={draft} onChange={event => setDraft(event.target.value)} maxLength={60}
                  onKeyDown={event => {if (event.key === ',' || event.key === 'Enter') {event.preventDefault(); add(draft)}}}
                  placeholder={t('topics-placeholder')} aria-label={t('topics-input-label')}/>
            <Button type="submit" variant="outline" size="sm" disabled={!draft.trim()}>{t('add-topic')}</Button>
        </form>
    </div>
}

/** Top of Listen now, only while GET /api/v1/podcasts returns []: renders nothing once the
 *  listener has added a single show (the query this component itself makes turns non-empty). */
export function WelcomeHome() {
    const {t} = useTranslation('welcome')
    const shows = useQuery({queryKey: SHOWS_KEY, queryFn: fetchShows, staleTime: 30_000, retry: false})
    const ai = useQuery({queryKey: AI_STATUS_KEY, queryFn: getAiStatus, staleTime: 60_000, retry: false})
    if (shows.isLoading || shows.isError || (shows.data?.length ?? 0) > 0) return null
    const connected = ai.data?.configured ?? false
    return <section className="welcome-card" aria-labelledby="welcome-title">
        <div className="welcome-intro">
            <p className="eyebrow">{t('step-1-title')}</p>
            <h2 id="welcome-title">{t('title')}</h2>
            <p className="welcome-subtitle">{t('step-1-body')} Follow one podcast and its latest episodes will appear here.</p>
            <Button size="lg" nativeButton={false} render={<Link to="/discover"/>}><Plus/> {t('step-1-cta')}</Button>
        </div>
        <div className="welcome-preferences">
            <div className="welcome-preference">
                <div><h3>{t('step-2-title')}</h3>
                    <p>{connected ? t('step-2-connected', {provider: capitalize(ai.data!.provider)}) : t('step-2-body')}</p>
                </div>
                {!connected && <Button size="sm" variant="ghost" nativeButton={false} render={<Link to="/settings/ai"/>}>
                    <Sparkles size={14}/> {t('step-2-cta')}
                </Button>}
            </div>
            <div className="welcome-preference welcome-preference-topics">
                <div><h3>{t('step-3-title')}</h3><p>{t('step-3-body')}</p></div>
                <TopicsEditor/>
            </div>
        </div>
    </section>
}
