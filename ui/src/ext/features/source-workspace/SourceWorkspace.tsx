import {useEffect, useMemo, useRef, useState} from 'react'
import {Link, useSearchParams} from 'react-router-dom'
import {useMutation, useQuery, useQueryClient, type UseQueryResult} from '@tanstack/react-query'
import {Check, Copy, Lightbulb, Play, Scissors} from 'lucide-react'
import type {Episode} from '../../../utils/listening'
import {clock} from '../../../utils/listening'
import {saveCompanionNote, type Transcript} from '../../../utils/companion'
import {Button} from '../../../components/ui/button'
import {Input} from '../../../components/ui/input'
import {Textarea} from '../../../components/ui/textarea'
import {Card, CardContent, CardDescription, CardHeader, CardTitle} from '../../../components/ui/card'
import {ListenLoading, ListenState} from '../../../components/ListenState'
import {CutWorkspace} from '../cuts/CutWorkspace'
import {LearningModePicker, learningModes} from '../cuts/LearningModePicker'
import type {LearningMode} from '../cuts/api'
import {MakeTranscript} from '../../shared/MakeTranscript'
import {authHeader} from '../../shared/podfetch'
import {cachedSubjects, createAgentPlan, exactRanges, listEpisodePlans, planHasEpisode, publisherSubjects} from './api'
import './source-workspace.css'

const drafts = new Map<string, string>()
const ownerKey = () => {
    const value = authHeader() ?? 'anonymous'
    let hash = 5381
    for (let index = 0; index < value.length; index++) hash = ((hash << 5) + hash) ^ value.charCodeAt(index)
    return (hash >>> 0).toString(36)
}

type Props = {
    episode: Episode; transcript: UseQueryResult<Transcript, Error>; sourcePosition: number; active: number
    requestedPassage: number; playing: boolean; follow: boolean; onFollow: () => void; onSeek: (seconds: number) => void
    onExplain: (seconds: number, quote: string) => void; passagesRef: React.RefObject<HTMLOListElement | null>; activeRef: React.RefObject<HTMLLIElement | null>
}

export function SourceWorkspace({episode, transcript, sourcePosition, active, requestedPassage, playing, follow, onFollow, onSeek,
    onExplain, passagesRef, activeRef}: Props) {
    const [params, setParams] = useSearchParams()
    const effort = params.get('learning_mode')
    const requestedEffort: LearningMode = effort === 'focus' || effort === 'chill' ? effort : 'balanced'
    const [learningMode, setLearningMode] = useState<LearningMode>(requestedEffort)
    useEffect(() => {setLearningMode(requestedEffort)}, [requestedEffort])
    const [search, setSearch] = useState('')
    const [selected, setSelected] = useState<Set<number>>(new Set())
    const draftKey = `${ownerKey()}:${episode.episode_id}`
    const [draft, setDraft] = useState(() => drafts.get(draftKey) ?? '')
    const [copied, setCopied] = useState(false)
    const [copyError, setCopyError] = useState('')
    const [actionsOpen, setActionsOpen] = useState(false)
    const client = useQueryClient()
    const mounted = useRef(false)
    const episodeRef = useRef(episode.episode_id)
    episodeRef.current = episode.episode_id
    useEffect(() => {mounted.current = true; return () => {mounted.current = false}}, [])
    useEffect(() => () => {drafts.set(draftKey, draft)}, [draft, draftKey])
    useEffect(() => {setSearch(''); setSelected(new Set()); setCopied(false); setCopyError('')}, [episode.episode_id])
    useEffect(() => {if (selected.size) setActionsOpen(true)}, [selected.size])

    const subjects = useQuery({queryKey: ['source-subjects', episode.episode_id], queryFn: async () => {
        const publisher = await publisherSubjects(episode.id)
        return publisher.length ? publisher : cachedSubjects(episode.episode_id)
    }, retry: false})
    const plans = useQuery({queryKey: ['cuts', 'plans', episode.episode_id], queryFn: () => listEpisodePlans(episode.episode_id), retry: false})
    const availablePlans = (plans.data ?? []).filter(plan => planHasEpisode(plan, episode.episode_id))
    const requestedPlan = params.get('plan')
    const planId = requestedPlan || undefined
    const mode = planId ? 'selected' : 'original'

    const segments = transcript.data?.segments ?? []
    const query = search.trim().toLocaleLowerCase()
    const shown = segments.map((segment, index) => ({segment, index})).filter(({segment}) => !query || segment.text.toLocaleLowerCase().includes(query))
    const selectedSegments = [...selected].sort((a, b) => a - b).map(index => segments[index])
        .filter((segment): segment is NonNullable<typeof segment> => Boolean(segment))
    const selectionStart = selectedSegments[0]?.start ?? sourcePosition
    const selectionEnd = selectedSegments.at(-1)?.end ?? selectedSegments.at(-1)?.start
    const selectedIndexes = [...selected].sort((a, b) => a - b)
    const contiguous = selectedIndexes.every((index, offset) => offset === 0 || index === selectedIndexes[offset - 1]! + 1)
    const precise = !!transcript.data?.timed && !!transcript.data.digest && selectedSegments.length > 0
        && selectedSegments.every(segment => !!segment.id && segment.end !== null)
    const highlightable = precise && contiguous
    const selectedQuote = selectedSegments.map(segment => segment.text).join(' ')

    const savedText = draft.trim() || selectedQuote.slice(0, 4000)
    const save = useMutation({mutationFn: () => saveCompanionNote(highlightable ? {
        id: crypto.randomUUID(), episode_id: episode.episode_id, position: selectionStart, text: savedText, kind: 'highlight',
        start: selectionStart, end: selectionEnd!, quote: selectedQuote.slice(0, 4000),
    } : {id: crypto.randomUUID(), episode_id: episode.episode_id, position: selectedSegments.length ? selectionStart : sourcePosition, text: savedText}),
    onSuccess: saved => {
        if (!mounted.current || episodeRef.current !== saved.episode_id) return
        setDraft(''); drafts.delete(draftKey); void client.invalidateQueries({queryKey: ['notes']})
    }})
    const planning = useRef<AbortController | null>(null)
    useEffect(() => () => planning.current?.abort(), [])
    const makePlan = useMutation({mutationFn: (without: boolean) => {
        const data = transcript.data!
        planning.current?.abort(); planning.current = new AbortController()
        const keep = exactRanges(data, selected, !without, without ? 'Kept because the user did not select this passage for removal.' : 'Kept because the user selected this passage in Learn.')
        const skip = exactRanges(data, selected, without, without ? 'Skipped because the user selected this passage for removal in Learn.' : 'Not part of the user selection.')
        return createAgentPlan({episode_ids: [episode.episode_id], mode: 'agent', skip_ads: without,
            ...(learningMode !== 'balanced' ? {learning_mode: learningMode} : {}),
            want: without ? 'Listen to this episode without the passages I selected in Learn.' : 'Listen to the passages I selected in Learn.',
            selections: [{episode_id: episode.episode_id, transcript_digest: data.digest!, keep, skip}]}, planning.current.signal)
    }, onSuccess: plan => {
        if (!mounted.current || episodeRef.current !== episode.episode_id || !planHasEpisode(plan, episode.episode_id)) return
        client.setQueryData(['cuts', 'plans', episode.episode_id], (old: unknown) => [plan, ...(Array.isArray(old) ? old : [])])
        setParams(previous => {const next = new URLSearchParams(previous); next.set('episode', episode.episode_id); next.set('plan', plan.id); next.delete('at'); return next})
    }})

    const toggle = (index: number) => setSelected(current => {const next = new Set(current); next.has(index) ? next.delete(index) : next.add(index); return next})
    const selectBrowserText = () => {
        const selection = window.getSelection()
        if (!selection || selection.isCollapsed || !selection.toString().trim()) return
        const item = (node: Node | null) => (node instanceof Element ? node : node?.parentElement)?.closest<HTMLElement>('[data-passage-index]')
        const first = item(selection.anchorNode), last = item(selection.focusNode)
        if (!first || !last || !passagesRef.current?.contains(first) || !passagesRef.current.contains(last)) return
        const a = Number(first.dataset.passageIndex), b = Number(last.dataset.passageIndex)
        setSelected(current => {const next = new Set(current); for (let i = Math.min(a, b); i <= Math.max(a, b); i++) next.add(i); return next})
    }
    const copyInstruction = async () => {
        if (!transcript.data?.digest || !selectedSegments.length) return
        const ids = selectedSegments.map(segment => segment.id).filter(Boolean).join(', ')
        const instruction = `Use episode ${episode.episode_id}, transcript digest ${transcript.data.digest}. Keep passage IDs ${ids}. Reason: I selected these exact source passages in Learn. Create a mode=agent listen with learning_mode=${learningMode}; do not call another model. ${learningModes[learningMode].instruction} These are my explicit selections; preserve them rather than replacing them.`
        try {await navigator.clipboard.writeText(instruction); setCopied(true); setCopyError('')} catch {setCopyError('Copy is unavailable in this browser.')}
    }
    const openPlan = (id?: string) => {
        setParams(previous => {const next = new URLSearchParams(previous); next.set('episode', episode.episode_id); next.delete('at'); id ? next.set('plan', id) : next.delete('plan'); return next})
    }
    const chooseEffort = (nextMode: LearningMode) => {
        // Update actions immediately; router transitions can finish after the next click.
        setLearningMode(nextMode); setCopied(false)
        setParams(previous => {const next = new URLSearchParams(previous); nextMode === 'balanced' ? next.delete('learning_mode') : next.set('learning_mode', nextMode); return next})
    }

    return <section className="source-workspace transcript-panel" aria-label="Transcript source workspace" data-subjects={subjects.isError ? 'error' : subjects.data?.length ? 'available' : subjects.isPending ? 'loading' : 'empty'}>
        <div className="source-main">
            <div className="source-mode"><div><Button variant={mode === 'original' ? 'secondary' : 'ghost'} aria-pressed={mode === 'original'} onClick={() => openPlan()}>Original transcript</Button><Button variant={mode === 'selected' ? 'secondary' : 'ghost'} aria-pressed={mode === 'selected'} disabled={!availablePlans.length && !planId} onClick={() => openPlan(planId ?? availablePlans[0]?.id)}>My selected listen</Button></div><p>{mode === 'original' ? 'Exact words from the source.' : 'Selected audio, with the reasons behind it.'}</p></div>
            {mode === 'selected' ? planId ? <CutWorkspace key={`${episode.episode_id}:${planId}`} target={{kind: 'episode', episodeId: episode.episode_id}} initialPlanId={planId} onPlanChange={openPlan} onPlay={(_, seconds) => onSeek(seconds)}/> : <div className="source-cut-empty">Choose passages in the original transcript to make your first selected listen.</div> : <>
            <div className="source-reading-toolbar">{(subjects.isError || !!subjects.data?.length) && <details className="source-subjects"><summary><span className="hidden sm:inline">Jump to a subject</span><span className="sm:hidden">Subjects</span>{subjects.data?.length ? ` (${subjects.data.length})` : ''}</summary><div className="source-subject-list">
                {subjects.isError ? <p role="alert">Subjects couldn't load. <Button size="sm" variant="link" onClick={() => void subjects.refetch()}>Try again</Button></p> : subjects.data?.map(subject => <Button key={`${subject.start}-${subject.title}`} variant="ghost" size="sm" onClick={() => onSeek(subject.start)}>{clock(subject.start)} · {subject.title}</Button>)}
            </div></details>}
            <div className="section-heading"><div><h2>Transcript</h2><p>{transcript.data?.source || 'Original episode'}</p></div><Button variant={follow ? 'secondary' : 'ghost'} onClick={onFollow} aria-pressed={follow}>Follow audio</Button></div>
            <details className="source-search"><summary>Find in transcript</summary><div className="transcript-search"><Input aria-label="Find in transcript" placeholder="Find exact words in this episode" value={search} onChange={event => setSearch(event.target.value)}/></div></details></div>
            {selected.size > 0 && <div className="source-selection-bar" role="status"><span>{selected.size} passage{selected.size === 1 ? '' : 's'} selected</span><Button variant="outline" size="sm" aria-expanded={actionsOpen} onClick={() => setActionsOpen(!actionsOpen)}>{actionsOpen ? 'Hide actions' : 'Show actions'}</Button><Button variant="ghost" size="sm" onClick={() => {setSelected(new Set());setActionsOpen(false)}}>Clear selection</Button></div>}
            {((selected.size > 0 && actionsOpen) || (!transcript.data?.timed && selected.size === 0)) && <Card className="source-actions" size="sm"><CardHeader><CardTitle>{selected.size ? 'Selected source' : 'Note at this moment'}</CardTitle><CardDescription>{selected.size ? 'Save the exact quote as-is, or add your own explanation first. The server verifies the source range.' : 'This source has no precise passage timing, so this saves a plain note.'}</CardDescription></CardHeader><CardContent>
                {precise && <><LearningModePicker value={learningMode} disabled={makePlan.isPending} onChange={chooseEffort}/>
                    <p className="text-sm text-muted-foreground">When you choose passages yourself, the mode labels your listen.</p></>}
                {selectedQuote && <blockquote>{selectedQuote}</blockquote>}
                <Textarea aria-label="Idea about selected passage" placeholder="Why does this matter to you?" rows={3} maxLength={4000} value={draft} disabled={save.isPending} onChange={event => setDraft(event.target.value)}/>
                <div className="source-actions-row"><Button variant="outline" disabled={!selectedSegments.length} onClick={() => onExplain(selectionStart, selectedQuote)}><Lightbulb/>Explain in Ask</Button><Button variant="outline" disabled={!transcript.data?.digest || !selectedSegments.length} onClick={() => void copyInstruction()}><Copy/>{copied ? 'Instruction copied' : 'Copy agent instruction'}</Button><Button disabled={!savedText || save.isPending} onClick={() => save.mutate()}>{save.isPending ? 'Saving…' : 'Save idea'}</Button></div>
                <div className="source-actions-row"><Button variant="outline" disabled={!precise || makePlan.isPending} onClick={() => makePlan.mutate(false)}><Scissors/>Keep selected</Button><Button variant="outline" disabled={!precise || makePlan.isPending} onClick={() => makePlan.mutate(true)}>Make a listen without selected</Button></div>
                {precise && !contiguous && <p className="text-sm text-muted-foreground">These separate passages will be saved as an exact-text source note so words between them are not included.</p>}
                {!precise && selectedSegments.length > 0 && <p className="text-sm text-muted-foreground">Precise listen actions need timed passages, stable passage IDs, and a transcript digest. Reading and saving still work.</p>}
                {save.isSuccess && <p role="status" className="source-save-status"><Check/>Idea saved. <Link to="/knowledge">Open Knowledge</Link></p>}
                {save.isError && <p role="alert" className="text-sm text-destructive">{save.error.message} Your draft is kept.</p>}
                {makePlan.isError && <p role="alert" className="text-sm text-destructive">{makePlan.error.message} Your selection is kept.</p>}
                {copyError && <p role="alert" className="text-sm text-destructive">{copyError}</p>}
            </CardContent></Card>}
            {transcript.isLoading ? <ListenLoading/> : transcript.isError ? <ListenState title="Transcript unavailable" retry={() => void transcript.refetch()}>{transcript.error.message}</ListenState> : !transcript.data?.text && !segments.length ? <ListenState title="No transcript for this episode yet">You can still listen and save a note at the current timestamp.<MakeTranscript key={episode.id} episode={episode} onReady={() => void transcript.refetch()}/></ListenState> : segments.length ? <><p className="transcript-hint" data-searching={query ? '' : undefined}>{query ? `${shown.length} matching passages` : 'Select exact passages with the buttons or by selecting their text.'}</p>
                {!shown.length && <ListenState title="No matching passages">Try a shorter phrase or another word.</ListenState>}
                <ol className="transcript-passages" ref={passagesRef} onMouseUp={selectBrowserText}>{shown.map(({segment, index}) => <li key={segment.id ?? index} data-passage-index={index} data-selected={selected.has(index) || undefined} className={`passage source-passage${index === requestedPassage ? ' active-passage requested-passage' : index === active && playing ? ' active-passage' : ''}`} ref={index === active ? activeRef : undefined}>
                    <Button variant="ghost" className="passage-time" aria-label={`Play passage at ${clock(segment.start)}`} onClick={() => onSeek(segment.start)}><Play/>{clock(segment.start)}</Button><p>{segment.text}</p><Button className="source-select" variant={selected.has(index) ? 'secondary' : 'outline'} size="icon-sm" aria-label={selected.has(index) ? `Remove passage at ${clock(segment.start)} from selection` : `Select passage at ${clock(segment.start)}`} title={selected.has(index) ? 'Selected' : 'Select passage'} aria-pressed={selected.has(index)} onClick={() => toggle(index)}>{selected.has(index) ? <Check/> : <Lightbulb/>}</Button>
                </li>)}</ol></> : <><p className="transcript-hint">This transcript has no timestamps. Reading, copying, and plain notes remain available.</p><p className="untimed-transcript">{transcript.data?.text}</p></>}
            </>}
        </div>
    </section>
}
