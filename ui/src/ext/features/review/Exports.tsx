// Anki-importable TSV and Obsidian Markdown, over every card the user has.
import {useState} from 'react'
import {useTranslation} from 'react-i18next'
import {Download} from 'lucide-react'
import {Button} from '../../../components/ui/button'
import {fetchAnkiExport, fetchObsidianExport, saveText} from './api'

export function Exports() {
    const {t} = useTranslation('review')
    const [error, setError] = useState<'anki' | 'obsidian' | null>(null)

    const anki = async () => {
        setError(null)
        try {saveText(await fetchAnkiExport(), 'cards.tsv', 'text/tab-separated-values')} catch {setError('anki')}
    }
    const obsidian = async () => {
        setError(null)
        try {saveText(await fetchObsidianExport(), 'cards.md', 'text/markdown')} catch {setError('obsidian')}
    }
    return <section className="review-export">
        <h2>{t('export-title')}</h2>
        <div className="review-export-actions">
            <div>
                <Button variant="outline" onClick={() => void anki()}><Download/>{t('export-anki')}</Button>
                <p className="review-muted">{t('export-anki-hint')}</p>
                {error === 'anki' && <p role="alert" className="review-error">{t('export-error')}</p>}
            </div>
            <div>
                <Button variant="outline" onClick={() => void obsidian()}><Download/>{t('export-obsidian')}</Button>
                <p className="review-muted">{t('export-obsidian-hint')}</p>
                {error === 'obsidian' && <p role="alert" className="review-error">{t('export-error')}</p>}
            </div>
        </div>
    </section>
}
