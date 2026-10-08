// Reading an Ask answer aloud. Generic text-to-speech plumbing only;
// building the words to speak (citations as "from minute N") is the caller's job.

/** Speaks `text` and resolves when it finishes (or at once when speech synthesis isn't available,
 * or `text` is blank). Cancels anything already speaking first. */
export function speak(text: string): Promise<void> {
    const synth = window.speechSynthesis
    if (!synth || !text.trim()) return Promise.resolve()
    return new Promise(resolve => {
        synth.cancel()
        const utterance = new SpeechSynthesisUtterance(text)
        utterance.onend = () => resolve()
        utterance.onerror = () => resolve()
        synth.speak(utterance)
    })
}

export function stopSpeaking(): void {
    window.speechSynthesis?.cancel()
}
