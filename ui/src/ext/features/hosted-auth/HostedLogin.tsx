import {FormEvent, useState} from 'react'
import {useNavigate} from 'react-router-dom'
import {Button} from '@/components/ui/button'
import {Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle} from '@/components/ui/card'
import {Input} from '@/components/ui/input'
import {Skeleton} from '@/components/ui/skeleton'
import {queryClient} from '@/utils/socketio'
import {acceptAccount, detectRuntimeMode, hostedLogin, setRuntimeMode} from './session'

const message = (status?: number) => status === 401 ? 'That email or password did not match.'
    : status === 403 ? 'This account does not have an invitation to Podsift.'
    : 'Sign-in is temporarily unavailable. Check the service and try again.'

export function HostedLogin() {
    const navigate = useNavigate()
    const [email, setEmail] = useState('')
    const [password, setPassword] = useState('')
    const [error, setError] = useState('')
    const [busy, setBusy] = useState(false)
    const submit = async (event: FormEvent) => {
        event.preventDefault(); setBusy(true); setError('')
        try {
            const user = await hostedLogin(email, password)
            acceptAccount(user.user_id, queryClient)
            setPassword('')
            navigate('/home/view', {replace: true})
        } catch (caught) {
            setError(message((caught as {status?: number}).status))
        } finally { setBusy(false) }
    }
    return <main className="flex min-h-full items-center justify-center bg-background px-4 py-12 text-foreground">
        <Card className="w-full max-w-sm">
            <CardHeader>
                <CardTitle>Sign in to Podsift</CardTitle>
                <CardDescription>Continue to your private podcast library and saved learning.</CardDescription>
            </CardHeader>
            <form onSubmit={submit}>
                <CardContent className="flex flex-col gap-4">
                    {error && <p className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive" role="alert">{error}</p>}
                    <label className="flex flex-col gap-2 text-sm" htmlFor="hosted-email">Email
                        <Input id="hosted-email" type="email" autoComplete="email" value={email}
                               onChange={event => setEmail(event.target.value)} required disabled={busy}/>
                    </label>
                    <label className="flex flex-col gap-2 text-sm" htmlFor="hosted-password">Password
                        <Input id="hosted-password" type="password" autoComplete="current-password" value={password}
                               onChange={event => setPassword(event.target.value)} required disabled={busy}/>
                    </label>
                </CardContent>
                <CardFooter className="mt-4 flex-col items-stretch gap-3">
                    <Button type="submit" size="lg" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</Button>
                    <p className="text-center text-xs text-muted-foreground">Access is invitation-only.</p>
                </CardFooter>
            </form>
        </Card>
    </main>
}

export function HostedModeUnavailable() {
    const [busy, setBusy] = useState(false)
    const retry = async () => {
        setBusy(true)
        const mode = await detectRuntimeMode()
        setRuntimeMode(mode)
        if (mode !== 'unavailable') window.location.reload()
        else setBusy(false)
    }
    return <main className="flex min-h-full items-center justify-center bg-background px-4 py-12 text-foreground">
        <Card className="w-full max-w-sm"><CardHeader><CardTitle>Sign-in service unavailable</CardTitle>
            <CardDescription>Podsift could not reach its session service. Your password was not sent.</CardDescription>
        </CardHeader><CardContent>{busy ? <Skeleton className="h-9 w-full"/> :
            <Button className="w-full" onClick={retry}>Try again</Button>}</CardContent></Card>
    </main>
}
