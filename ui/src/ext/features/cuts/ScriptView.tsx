// The script: before anything is cut, every line the cut keeps and every part it leaves out,
// with its time in the episode, why it goes, and the chapter it's in (GET /plans/{id}/script).
// Kept passages show their first and last lines - where the cuts fall - and open to every line.
import {useState} from 'react'
import {useTranslation} from 'react-i18next'
import {ChevronDown, ChevronRight, Play, Scissors, Undo2, X} from 'lucide-react'
import {Button} from '../../../components/ui/button'
import {clock} from '../../../utils/listening'
import type {Plan, Script, ScriptLine, ScriptPart} from './api'

type Props = {
    plan: Plan; script: Script; title: (episodeId: string) => string; onPlay: (episodeId: string, seconds: number) => void
    toggle: (span: string, enabled: boolean) => void; busy: boolean
}

/** Lines a kept passage shows before "Show all": its first and its last, where the cuts fall. */
const HEAD = 2, TAIL = 2
/** Characters of a cut part's first line shown before "Show". */
const PREVIEW = 110
const raw = {interpolation: {escapeValue: false}}

export function ScriptView({plan, script, title, onPlay, toggle, busy}: Props) {
    const {t} = useTranslation('cuts')
    const many = script.episodes.filter(e => e.parts.some(p => p.kind === 'keep')).length > 1
    return <div className="cut-script" aria-label={t('script-label')}>
        {script.episodes.map(episode => <section key={episode.episode_id} className="cut-script-episode">
            {many && <h3 className="cut-episode">{title(episode.episode_id) || episode.title}</h3>}
            <ol className="cut-passages">{episode.parts.map(part => part.kind === 'keep'
                ? <Keep key={`k${part.start}`} part={part} episodeId={episode.episode_id} onPlay={onPlay} busy={busy}
                    remove={() => part.span_id && toggle(part.span_id, false)}/>
                : <Gap key={`c${part.start}`} part={part} plan={plan} script={script} episodeId={episode.episode_id} onPlay={onPlay}
                    busy={busy} restore={id => toggle(id, true)}/>)}</ol>
        </section>)}
    </div>
}

function Keep({part, episodeId, onPlay, remove, busy}: {part: ScriptPart; episodeId: string; onPlay: Props['onPlay']
    remove: () => void; busy: boolean}) {
    const {t} = useTranslation('cuts')
    const [all, setAll] = useState(false)
    const time = clock(part.start)
    const hidden = all ? 0 : Math.max(0, part.lines.length - HEAD - TAIL)
    const shown = hidden ? [...part.lines.slice(0, HEAD), null, ...part.lines.slice(-TAIL)] : part.lines
    return <li className="cut-passage" data-kind="keep">
        <Button variant="ghost" className="passage-time" aria-label={t('play-passage', {time})} onClick={() => onPlay(episodeId, part.start)}>
            <Play/>{time}–{clock(part.end)}</Button>
        <div className="cut-passage-text">
            <p className="cut-part-head"><span className="cut-keep-label">{t('keep')}</span>
                {part.why && <span className="cut-subject">{part.why}</span>}
                {part.chapters.length > 0 && <span className="cut-chapters">{t('in-chapter', {chapters: part.chapters.join(' · '), ...raw})}</span>}</p>
            <ol className="cut-lines">{shown.map((line, index) => line
                ? <Line key={`${line.start}-${index}`} line={line} episodeId={episodeId} onPlay={onPlay}/>
                : <li key="more" className="cut-lines-more"><Button variant="link" size="sm" onClick={() => setAll(true)}>
                    {t('show-lines', {count: hidden})}</Button></li>)}</ol>
            {all && part.lines.length > HEAD + TAIL && <Button variant="link" size="sm" onClick={() => setAll(false)}>{t('hide-lines')}</Button>}
        </div>
        <Button variant="ghost" size="sm" disabled={busy || !part.span_id} aria-label={t('remove-passage', {time})} onClick={remove}>
            <X/><span className="cut-action-label">{t('remove')}</span></Button>
    </li>
}

function Gap({part, plan, script, episodeId, onPlay, restore, busy}: {part: ScriptPart; plan: Plan; script: Script; episodeId: string
    onPlay: Props['onPlay']; restore: (span: string) => void; busy: boolean}) {
    const {t} = useTranslation('cuts')
    const [open, setOpen] = useState(false)
    const time = clock(part.start)
    const removed = part.reason === 'removed'
        ? plan.spans.find(s => !s.enabled && s.episode_id === episodeId && s.start < part.end && s.end > part.start) : undefined
    const why = part.why || t(`reason-${part.reason ?? 'other'}`, {want: script.want, skip: script.skip, minutes: plan.minutes ?? '', ...raw})
    const first = part.lines[0]?.text ?? ''
    // one short line needs no "Show": the preview is the whole of what's cut
    const whole = part.lines.length === 1 && first.length <= PREVIEW
    return <li className="cut-passage cut-gap" data-kind="cut" data-reason={part.reason}>
        <Button variant="ghost" className="passage-time" aria-label={t('play-cut', {time})} onClick={() => onPlay(episodeId, part.start)}>
            <Scissors/>{time}–{clock(part.end)}</Button>
        <div className="cut-passage-text">
            <p className="cut-part-head"><span className="cut-cut-label">{t('cut-out', {length: clock(part.end - part.start)})}</span>
                <span className="cut-subject">{why}</span>
                {part.chapters.length > 0 && <span className="cut-chapters">{t('in-chapter', {chapters: part.chapters.join(' · '), ...raw})}</span>}</p>
            {part.lines.length > 0 && <p className="cut-gap-preview">
                {!open && <span>“{first.length > PREVIEW ? `${first.slice(0, PREVIEW)}…` : first}”
                    {whole && part.lines[0]?.partial && <em className="cut-partial"> {t('partial')}</em>}</span>}
                {!whole && <Button variant="link" size="sm" aria-expanded={open} onClick={() => setOpen(!open)}>
                    {open ? <ChevronDown/> : <ChevronRight/>}{t(open ? 'hide-cut-words' : 'show-cut-words', {count: part.lines.length})}</Button>}
            </p>}
            {open && <ol className="cut-lines">{part.lines.map((line, index) =>
                <Line key={`${line.start}-${index}`} line={line} episodeId={episodeId} onPlay={onPlay}/>)}</ol>}
        </div>
        {removed && <Button variant="ghost" size="sm" disabled={busy} aria-label={t('restore-passage', {time: clock(removed.start)})}
            onClick={() => restore(removed.id)}><Undo2/><span className="cut-action-label">{t('restore')}</span></Button>}
    </li>
}

function Line({line, episodeId, onPlay}: {line: ScriptLine; episodeId: string; onPlay: Props['onPlay']}) {
    const {t} = useTranslation('cuts')
    const time = clock(line.start)
    return <li className="cut-line">
        <button type="button" className="cut-line-time" aria-label={t('play-line', {time})} onClick={() => onPlay(episodeId, line.start)}>{time}</button>
        <span>{line.text}{line.partial && <em className="cut-partial"> {t('partial')}</em>}</span>
    </li>
}
