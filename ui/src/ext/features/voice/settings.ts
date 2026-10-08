// Voice input settings and feature detection.
//
// Speech recognition has two paths. Default: record audio and send it to the server
// (POST /companion/speech/transcribe), which uses the user's own AI provider. Opt-in: the
// browser's own SpeechRecognition, which never uploads audio through us - but in Chrome the
// browser itself sends the audio to Google to recognize it, so this is off unless the listener
// turns it on. The choice is stored on this device only.
const BROWSER_RECOGNITION_KEY = 'voice.browserSpeechRecognition'

export function browserRecognitionEnabled(): boolean {
    try {
        return localStorage.getItem(BROWSER_RECOGNITION_KEY) === '1'
    } catch {
        return false
    }
}

export function setBrowserRecognitionEnabled(on: boolean): void {
    try {
        if (on) localStorage.setItem(BROWSER_RECOGNITION_KEY, '1')
        else localStorage.removeItem(BROWSER_RECOGNITION_KEY)
    } catch { /* private browsing or storage disabled: the setting just won't stick */ }
}

// The Web Speech API's recognizer has no official TS type (still non-standard); this is the
// small slice of it we use.
export interface SpeechRecognitionLike extends EventTarget {
    lang: string
    interimResults: boolean
    continuous: boolean
    maxAlternatives: number
    start(): void
    stop(): void
    onresult: ((event: {results: ArrayLike<{0: {transcript: string}; isFinal: boolean}>}) => void) | null
    onerror: ((event: {error?: string}) => void) | null
    onend: (() => void) | null
}
export type SpeechRecognitionCtor = new () => SpeechRecognitionLike

export function speechRecognitionCtor(): SpeechRecognitionCtor | undefined {
    const scope = window as unknown as {SpeechRecognition?: SpeechRecognitionCtor; webkitSpeechRecognition?: SpeechRecognitionCtor}
    return scope.SpeechRecognition ?? scope.webkitSpeechRecognition
}

export function canRecordAudio(): boolean {
    return typeof MediaRecorder !== 'undefined' && !!navigator.mediaDevices?.getUserMedia
}

/** Whether voice input can work at all in this browser, by either path. */
export function canCapture(): boolean {
    return !!speechRecognitionCtor() || canRecordAudio()
}
