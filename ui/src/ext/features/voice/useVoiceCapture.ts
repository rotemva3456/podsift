// One recording, start to text. Two paths:
//  - default: record audio, POST it to /companion/speech/transcribe (the user's own AI provider)
//  - opt-in (settings.ts): the browser's own SpeechRecognition, no audio sent through us
// Both auto-stop after RECORD_MS; `stop()` ends one early. Errors are VoiceCaptureError with a
// message that is safe to show as is.
import {useCallback, useRef, useState} from 'react'
import {transcribeAudio} from './api'
import {browserRecognitionEnabled, canRecordAudio, speechRecognitionCtor, type SpeechRecognitionCtor} from './settings'

export type CaptureState = 'idle' | 'listening' | 'processing'

export class VoiceCaptureError extends Error {}

export const RECORD_MS = 30_000
const RECORDER_TYPES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4']

function pickRecorderType(): string | undefined {
    if (typeof MediaRecorder === 'undefined' || typeof MediaRecorder.isTypeSupported !== 'function') return undefined
    return RECORDER_TYPES.find(type => MediaRecorder.isTypeSupported(type))
}

async function record(stopRef: {current: () => void}, setState: (state: CaptureState) => void): Promise<string> {
    if (!canRecordAudio()) throw new VoiceCaptureError('Voice input is not supported in this browser.')
    let stream: MediaStream
    try {
        stream = await navigator.mediaDevices.getUserMedia({audio: true})
    } catch {
        throw new VoiceCaptureError('The microphone could not be used. Check your browser and system permissions.')
    }
    try {
        const mimeType = pickRecorderType()
        const recorder = mimeType ? new MediaRecorder(stream, {mimeType}) : new MediaRecorder(stream)
        const chunks: Blob[] = []
        recorder.ondataavailable = event => {if (event.data.size > 0) chunks.push(event.data)}
        const finished = new Promise<void>((resolve, reject) => {
            recorder.onstop = () => resolve()
            recorder.onerror = () => reject(new VoiceCaptureError('The recording failed. Try again.'))
        })
        stopRef.current = () => {if (recorder.state !== 'inactive') recorder.stop()}
        recorder.start()
        const timer = setTimeout(() => stopRef.current(), RECORD_MS)
        try {
            await finished
        } finally {
            clearTimeout(timer)
        }
        if (chunks.length === 0) throw new VoiceCaptureError('No audio was recorded. Try again.')
        setState('processing')
        const blob = new Blob(chunks, {type: mimeType || chunks[0]!.type || 'application/octet-stream'})
        const {text} = await transcribeAudio(blob)
        return text
    } finally {
        stream.getTracks().forEach(track => track.stop())
    }
}

function recognize(Recognizer: SpeechRecognitionCtor, stopRef: {current: () => void}): Promise<string> {
    return new Promise<string>((resolve, reject) => {
        const recognizer = new Recognizer()
        recognizer.lang = navigator.language || 'en-US'
        recognizer.interimResults = false
        recognizer.continuous = false
        recognizer.maxAlternatives = 1
        let settled = false, timer = 0
        const finish = (run: () => void) => {
            if (settled) return
            settled = true
            clearTimeout(timer)
            try {recognizer.stop()} catch { /* already stopped */ }
            run()
        }
        recognizer.onresult = event => {
            const transcript = Array.from(event.results).map(result => result[0].transcript).join(' ').trim()
            finish(() => transcript ? resolve(transcript) : reject(new VoiceCaptureError('No speech was recognized. Try again.')))
        }
        recognizer.onerror = event => finish(() => reject(new VoiceCaptureError(
            event.error === 'not-allowed' || event.error === 'permission-denied' || event.error === 'service-not-allowed'
                ? 'The microphone could not be used. Check your browser and system permissions.'
                : 'No speech was recognized. Try again.')))
        recognizer.onend = () => finish(() => reject(new VoiceCaptureError('No speech was recognized. Try again.')))
        stopRef.current = () => {try {recognizer.stop()} catch { /* already stopped */ }}
        timer = window.setTimeout(() => stopRef.current(), RECORD_MS)
        recognizer.start()
    })
}

/** `start()` pauses nothing itself (the caller owns playback); it resolves with the heard text,
 * or rejects with a VoiceCaptureError. `stop()` ends a listening/recording capture early. */
export function useVoiceCapture() {
    const [state, setState] = useState<CaptureState>('idle')
    const busy = useRef(false)
    const stopRef = useRef<() => void>(() => {})

    const start = useCallback(async (): Promise<string> => {
        if (busy.current) throw new VoiceCaptureError('Already listening.')
        busy.current = true
        setState('listening')
        try {
            const recognizer = browserRecognitionEnabled() ? speechRecognitionCtor() : undefined
            return recognizer ? await recognize(recognizer, stopRef) : await record(stopRef, setState)
        } finally {
            busy.current = false
            stopRef.current = () => {}
            setState('idle')
        }
    }, [])

    const stop = useCallback(() => stopRef.current(), [])
    return {state, start, stop}
}
