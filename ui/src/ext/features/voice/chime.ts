// A short confirmation tone with the Web Audio API - no audio asset to ship, works offline, and
// is heard rather than read (hands-free, often while driving).
type AudioContextCtor = typeof AudioContext

function audioContextCtor(): AudioContextCtor | undefined {
    const scope = window as unknown as {AudioContext?: AudioContextCtor; webkitAudioContext?: AudioContextCtor}
    return scope.AudioContext ?? scope.webkitAudioContext
}

/** `ok`: a short rising beep. Not ok: a lower, shorter one. Never throws. */
export function playChime(ok: boolean): void {
    const Ctor = audioContextCtor()
    if (!Ctor) return
    try {
        const context = new Ctor()
        const oscillator = context.createOscillator()
        const gain = context.createGain()
        oscillator.frequency.value = ok ? 880 : 330
        gain.gain.setValueAtTime(0.0001, context.currentTime)
        gain.gain.exponentialRampToValueAtTime(0.2, context.currentTime + 0.01)
        gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 0.18)
        oscillator.connect(gain)
        gain.connect(context.destination)
        oscillator.onended = () => void context.close()
        oscillator.start()
        oscillator.stop(context.currentTime + 0.2)
    } catch { /* the chime is a nicety; the note or answer already happened */ }
}
