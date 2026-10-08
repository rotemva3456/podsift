import {useId} from 'react'
import {Button} from '../../../components/ui/button'
import type {LearningMode} from './api'

export const learningModes: Record<LearningMode, {label: string; hint: string; instruction: string}> = {
    balanced: {label: 'Any effort', hint: 'Useful material at any difficulty.', instruction: 'Choose useful passages at any difficulty.'},
    focus: {label: 'Focus', hint: "For when you're sharp: difficult ideas, confusing parts, and deeper reasoning.",
        instruction: 'Prioritize difficult ideas, confusing parts I mention, and deeper reasoning. Keep the prerequisites and complete explanations. Save routine recap for Chill.'},
    chill: {label: 'Chill', hint: 'For a relaxed listen: recap, familiar ideas, and clear explanations.',
        instruction: 'Choose recap, familiar ideas, and clear explanations with little active thought. Save difficult new concepts and multi-step problems for Focus. Keep explanations coherent.'},
}

export function LearningModePicker({value, onChange, disabled = false}: {
    value: LearningMode; onChange: (mode: LearningMode) => void; disabled?: boolean
}) {
    const hintId = useId()
    return <fieldset className="min-w-0 space-y-2" aria-describedby={hintId}>
        <legend className="text-sm font-medium mb-2">Listening effort</legend>
        <div className="flex flex-wrap gap-2">
            {(Object.keys(learningModes) as LearningMode[]).map(mode => <Button key={mode} type="button" size="sm"
                disabled={disabled} variant={value === mode ? 'secondary' : 'outline'} aria-pressed={value === mode}
                onClick={() => onChange(mode)}>{learningModes[mode].label}</Button>)}
        </div>
        <p id={hintId} className="text-sm text-muted-foreground">{learningModes[value].hint}</p>
    </fieldset>
}
