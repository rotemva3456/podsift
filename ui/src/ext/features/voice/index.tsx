// Hands-free notes and questions while driving. The player action here is the
// voice-note mic; the Ask-by-voice mic lives directly in components/AskEpisode.tsx (this feature
// owns that file too) and reuses useVoiceCapture from here.
import type {Feature} from '../../types'
import {VoiceNoteButton} from './VoiceNoteButton'

export const feature: Feature = {
    id: 'voice',
    playerActions: [VoiceNoteButton],
}
