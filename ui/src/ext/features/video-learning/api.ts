import {companion, type Passage} from '../../../utils/companion'

export type VideoLearning = {
    task: 'summary' | 'watch_plan'; goal: string; minutes: number | null
    points: {text: string; citations: string[]; sources: Passage[]}[]
    moments: {start: number; end: number; title: string; why: string; action: 'watch' | 'read' | 'check_screen'}[]
    caveat: string; watch_seconds: number; visual_coverage: 'none'; transcript_digest: string
}
export type Video = {
    id: string; title: string; duration: number; bytes: number; has_audio: boolean
    status: 'saved' | 'queued' | 'running' | 'cancelling' | 'ready' | 'failed' | 'cancelled' | 'interrupted'
    job_kind: 'transcribe' | 'learn' | 'visual-review' | null; progress: number; error: string | null
    has_transcript: boolean; has_speech: boolean; media_url: string; learning: VideoLearning | null
    visual_review: {url: string; status: 'prepared'; visual_coverage: 'none'} | null
}
export type VideoStatus = {speech_configured: boolean; learning_configured: boolean; vision_configured: boolean; max_bytes: number}
export type VideoTranscript = {
    video_id: string; segments: Passage[]; digest: string; next_cursor: number | null
    total_segments: number; processed_seconds: number; coverage_kind: 'full_audio'; visual_coverage: 'none'
}

export const videoStatus = () => companion<VideoStatus>('/videos/status')
export const video = (id: string) => companion<Video>(`/videos/${encodeURIComponent(id)}`)
export const videoLibrary = (cursor = 0) => companion<{videos: Video[]; next_cursor: number | null}>(`/videos?cursor=${cursor}`)
export const uploadVideo = (file: File, signal: AbortSignal) => companion<Video>(`/videos?filename=${encodeURIComponent(file.name)}`,
    {method: 'POST', body: file, headers: {'Content-Type': file.type || 'application/octet-stream'}, signal})
export const videoTranscript = (id: string, cursor = 0) => companion<VideoTranscript>(`/videos/${encodeURIComponent(id)}/transcript?cursor=${cursor}`)
export const videoAction = (id: string, action: 'transcribe' | 'learn' | 'cancel' | 'visual-review', payload?: unknown) =>
    companion(`/videos/${encodeURIComponent(id)}/${action}`, {method: 'POST', body: payload === undefined ? undefined : JSON.stringify(payload)})
export const processing = (source?: Video) => !!source && ['queued', 'running', 'cancelling'].includes(source.status)
