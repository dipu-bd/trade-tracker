import { RouterProvider } from '@tanstack/react-router'
import { useState } from 'react'

import { Button, Card, ErrorNote, Field, inputClass } from '@/components/ui'
import { router } from '@/router'
import { useAuth } from '@/auth'

export function App() {
  const { user, ready } = useAuth()

  if (!ready) {
    return <Splash />
  }
  return user ? <RouterProvider router={router} /> : <LoginScreen />
}

function Splash() {
  return (
    <main className="flex min-h-full items-center justify-center p-8">
      <div className="w-full max-w-xs">
        <div className="skeleton h-2 w-full" />
      </div>
    </main>
  )
}

function LoginScreen() {
  const { login, register } = useAuth()
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [error, setError] = useState<Error | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      if (mode === 'login') await login(email, password)
      else await register(email, password, displayName || email)
    } catch (failure) {
      setError(failure as Error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="mx-auto flex min-h-full w-full max-w-md flex-col justify-center p-4 sm:p-8">
      <div className="mb-5 text-center">
        <h1 className="text-xl font-semibold tracking-tight">Tradebot</h1>
        <p className="text-sm text-[var(--color-ink-muted)]">AI paper-trading portfolio manager</p>
      </div>
      <Card title={mode === 'login' ? 'Sign in' : 'Create an account'}>
        <form className="grid gap-3" onSubmit={submit}>
          {mode === 'register' && (
            <Field label="Display name">
              <input
                className={inputClass}
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                autoComplete="name"
              />
            </Field>
          )}
          <Field label="Email">
            <input
              type="email"
              className={inputClass}
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="email"
              required
            />
          </Field>
          <Field
            label="Password"
            hint={mode === 'register' ? 'At least 12 characters.' : undefined}
          >
            <input
              type="password"
              className={inputClass}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              minLength={mode === 'register' ? 12 : undefined}
              required
            />
          </Field>
          <ErrorNote error={error} />
          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? 'Working…' : mode === 'login' ? 'Sign in' : 'Create account'}
          </Button>
          <Button variant="ghost" onClick={() => setMode(mode === 'login' ? 'register' : 'login')}>
            {mode === 'login' ? 'Need an account?' : 'Already have one?'}
          </Button>
        </form>
      </Card>
    </main>
  )
}
