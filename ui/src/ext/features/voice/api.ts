// The speech API: companion/routes/speech.py.
import {companion} from '../../../utils/companion'

export type SpeechStatus = {configured: boolean}
export type Transcription = {text: string; duration: number | null}

export const getSpeechStatus = () => companion<SpeechStatus>('/speech/status')

/** Sends a recorded clip as-is (any type ffmpeg can read); the server converts it before it
 * reaches an AI provider. */
export const transcribeAudio = (blob: Blob) => companion<Transcription>('/speech/transcribe', {
    method: 'POST', body: blob, headers: {'Content-Type': blob.type || 'application/octet-stream'},
})
