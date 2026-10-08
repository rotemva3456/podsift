import {useEffect, useRef, useState} from 'react'
import {useQuery, useQueryClient} from '@tanstack/react-query'
import {canDownload} from './api'
import {downloadEpisode} from './smartPlay'

export type DownloadState = 'idle' | 'downloading' | 'done' | 'not-allowed' | 'error'

/**
 * Download episodes through PodFetch, one after the other, then refresh them in the episode cache
 * (`['listen-episode', id]`, shared with the episode workspace) so their file links are current.
 * `allowed` is false when the user's role can't download (only admins and uploaders can).
 */
export function useDownload(onDone?: () => void) {
    const [state, setState] = useState<DownloadState>('idle')
    const alive = useRef(true), done = useRef(onDone)
    done.current = onDone
    useEffect(() => {alive.current = true; return () => {alive.current = false}}, [])
    const client = useQueryClient()
    const role = useQuery({queryKey: ['cuts', 'can-download'], queryFn: canDownload, staleTime: Infinity, retry: false})
    const start = async (episodeIds: string[]) => {
        setState('downloading')
        try {
            for (const id of episodeIds) {
                if (await downloadEpisode(id, () => alive.current) === 'not-allowed') return void (alive.current && setState('not-allowed'))
                await client.invalidateQueries({queryKey: ['listen-episode', id]})
            }
            if (!alive.current) return
            setState('done')
            done.current?.()
        } catch {
            if (alive.current) setState('error')
        }
    }
    return {state: role.data === false ? 'not-allowed' as const : state, start}
}
