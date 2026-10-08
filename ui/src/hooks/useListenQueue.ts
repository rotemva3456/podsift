import {useMutation, useQuery, useQueryClient} from '@tanstack/react-query'
import {client} from '../utils/http'
import type {Episode} from '../utils/listening'

const key = ['listen-next']
const name = 'Listen next'
async function readQueue() {
    const result = await client.GET('/api/v1/playlist')
    return result.data?.find(item => item.name === name) ?? null
}
export function useListenQueue() {
    const cache = useQueryClient()
    const query = useQuery({queryKey: key, queryFn: readQueue})
    const mutation = useMutation({
        scope: {id: 'listen-queue'},
        mutationFn: async ({episode, remove, ids}: {episode?: Episode; remove?: string; ids?: string[]}) => {
            const current = await readQueue()
            let items = ids ?? (current?.items ?? []).map(item => item.podcastEpisode.id)
            if (episode && !items.includes(episode.id)) items.push(episode.id)
            if (remove) items = items.filter(id => id !== remove)
            const body = {name, items: items.map(episode => ({episode}))}
            if (current) await client.PUT('/api/v1/playlist/{playlist_id}', {params: {path: {playlist_id: current.id}}, body})
            else await client.POST('/api/v1/playlist', {body})
        },
        onSuccess: () => cache.invalidateQueries({queryKey: key}),
    })
    return {...query, items: query.data?.items ?? [], update: mutation.mutateAsync, saving: mutation.isPending, saveError: mutation.error}
}
