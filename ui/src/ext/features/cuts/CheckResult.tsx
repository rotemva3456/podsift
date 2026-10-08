// What listening to the exported MP3 found: every cut right, re-cut until right, or the exact
// problems left - each with the original's time to play. Words the service heard differently
// from the transcript, without any cut being wrong, are notes, not problems.
import {useTranslation} from 'react-i18next'
import {AlertTriangle, CircleCheck, Ear, Play} from 'lucide-react'
import {Button} from '../../../components/ui/button'
import {clock} from '../../../utils/listening'
import type {CheckFinding, CheckFix, CutCheck} from './api'

const raw = {interpolation: {escapeValue: false}}

export function CheckResult({check, onPlay}: {check: CutCheck; onPlay: (episodeId: string, seconds: number) => void}) {
    const {t} = useTranslation('cuts')
    const errors = check.findings.filter(f => f.severity === 'error')
    const notes = check.findings.filter(f => f.severity === 'warning' && f.kind !== 'no_pause')
    return <div className="cut-verdict" data-status={check.status}>
        {check.status === 'passed' && <p className="cut-verdict-head"><CircleCheck/>
            {t(check.joins ? 'check-passed' : 'check-passed-single', {count: check.joins})}</p>}
        {check.status === 'fixed' && <p className="cut-verdict-head"><CircleCheck/>{t('check-fixed', {count: check.fixes.length})}</p>}
        {check.status === 'problems' && <p role="alert" className="cut-verdict-head"><AlertTriangle/>{t('check-problems', {count: errors.length})}</p>}
        {check.status === 'audio_only' && <p className="cut-verdict-head"><Ear/>{t('check-audio-only')}</p>}
        {check.reason && <p className="cut-hint">{check.reason}</p>}
        {check.status === 'fixed' && <ul className="cut-verdict-list">{check.fixes.map(fix =>
            <li key={`${fix.render}-${fix.piece}-${fix.edge}`}>{describeFix(t, fix)}</li>)}</ul>}
        {errors.length > 0 && <ul className="cut-verdict-list">{errors.map((finding, index) =>
            <Found key={index} finding={finding} onPlay={onPlay}/>)}</ul>}
        {errors.length > 0 && <p className="cut-hint">{t('check-problems-hint')}</p>}
        {check.words && check.words.expected > 0 && <p className="cut-hint">{t(check.status === 'problems' ? 'check-words-plain' : 'check-words',
            {heard: check.words.heard, expected: check.words.expected})}</p>}
        {notes.length > 0 && <details className="cut-verdict-notes"><summary>{t('check-notes', {count: notes.length})}</summary>
            <ul className="cut-verdict-list">{notes.map((finding, index) => <Found key={index} finding={finding} onPlay={onPlay}/>)}</ul></details>}
    </div>
}

function Found({finding, onPlay}: {finding: CheckFinding; onPlay: (episodeId: string, seconds: number) => void}) {
    const {t} = useTranslation('cuts')
    const text = t(`finding-${finding.kind}`, {defaultValue: finding.message, n: finding.piece, time: clock(finding.cut_time),
        words: finding.words.join(' '), heard: finding.heard, seconds: finding.seconds.toFixed(1), ...raw})
    return <li>
        <span>{text}{finding.stuck && <> {t('finding-stuck')}</>}</span>
        {finding.episode_id && finding.source_time !== undefined && <Button variant="ghost" size="sm"
            aria-label={t('play-source', {time: clock(finding.source_time)})} onClick={() => onPlay(finding.episode_id!, finding.source_time!)}>
            <Play/>{clock(finding.source_time)}</Button>}
    </li>
}

function describeFix(t: ReturnType<typeof useTranslation>['t'], fix: CheckFix): string {
    const seconds = Math.abs(fix.to - fix.from).toFixed(1)
    const why = fix.why === 'dead_air' ? `dead_air-${fix.edge}` : fix.why
    return t(`fix-${why}`, {n: fix.piece, seconds, defaultValue: t('fix-other', {n: fix.piece, seconds})})
}
