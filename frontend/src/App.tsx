import { useState } from 'react'

import { usePortfolios } from '@/api/hooks'
import { Button, Card, Field, inputClass } from '@/components/ui'
import { AICallLog } from '@/pages/AICallLog'
import { Backtest } from '@/pages/Backtest'
import { AnalystChat } from '@/pages/Chat'
import { MarketExplorer } from '@/pages/Market'
import { EventFeed, PriceTracking, ProviderHealthPage } from '@/pages/Observability'
import { Blotter, PortfolioDetailPage, PortfolioList } from '@/pages/Portfolios'
import { Settings } from '@/pages/Settings'
import { StrategyPage } from '@/pages/Strategy'
import { useAuth } from '@/auth'

const TABS = [
  'Portfolios',
  'Detail',
  'Blotter',
  'Strategy',
  'AI log',
  'Backtest',
  'Chat',
  'Market',
  'Prices',
  'Providers',
  'Events',
  'Settings',
] as const

type Tab = (typeof TABS)[number]

export function App() {
  const { user, ready } = useAuth()

  if (!ready) {
    return <Splash>Loading…</Splash>
  }
  return user ? <Shell /> : <LoginScreen />
}

function Splash({ children }: { children: React.ReactNode }) {
  return (
    <main className="flex min-h-full items-center justify-center p-8">
      <p className="text-[var(--color-ink-muted)]">{children}</p>
    </main>
  )
}

function Shell() {
  const { user, logout } = useAuth()
  const portfolios = usePortfolios()
  const [tab, setTab] = useState<Tab>('Portfolios')
  const [portfolioId, setPortfolioId] = useState<number | null>(null)

  const active = portfolioId ?? portfolios.data?.[0]?.id ?? null
  const needsPortfolio = ['Detail', 'Blotter', 'Strategy', 'AI log', 'Backtest', 'Chat'].includes(
    tab,
  )

  return (
    <div className="min-h-full">
      <header className="border-b border-[var(--color-border-subtle)] px-6 py-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold tracking-tight">Tradebot</h1>
            <p className="text-xs text-[var(--color-ink-muted)]">
              AI paper-trading portfolio manager
            </p>
          </div>
          <div className="flex items-center gap-3 text-sm">
            {portfolios.data && portfolios.data.length > 0 && (
              <select
                value={active ?? ''}
                onChange={(event) => setPortfolioId(Number(event.target.value))}
                className="rounded border border-[var(--color-border-subtle)] bg-[var(--color-surface-base)] px-2 py-1"
              >
                {portfolios.data.map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.name}
                  </option>
                ))}
              </select>
            )}
            <span className="text-[var(--color-ink-muted)]">{user?.display_name}</span>
            <Button variant="ghost" onClick={() => void logout()}>
              Sign out
            </Button>
          </div>
        </div>

        <nav className="mt-3 flex flex-wrap gap-1">
          {TABS.map((item) => (
            <button
              key={item}
              type="button"
              onClick={() => setTab(item)}
              className={`rounded px-2.5 py-1 text-sm transition ${
                tab === item
                  ? 'bg-[var(--color-surface-raised)] font-medium'
                  : 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]'
              }`}
            >
              {item}
            </button>
          ))}
        </nav>
      </header>

      <main className="p-6">
        {needsPortfolio && active === null ? (
          <Card>
            <p className="text-sm text-[var(--color-ink-muted)]">
              Create a portfolio first — this view needs one.
            </p>
          </Card>
        ) : (
          <>
            {tab === 'Portfolios' && (
              <PortfolioList
                onOpen={(id) => {
                  setPortfolioId(id)
                  setTab('Detail')
                }}
              />
            )}
            {tab === 'Detail' && active !== null && <PortfolioDetailPage id={active} />}
            {tab === 'Blotter' && active !== null && <Blotter id={active} />}
            {tab === 'Strategy' && active !== null && <StrategyPage id={active} />}
            {tab === 'AI log' && active !== null && <AICallLog portfolioId={active} />}
            {tab === 'Backtest' && active !== null && <Backtest id={active} />}
            {tab === 'Chat' && active !== null && <AnalystChat id={active} />}
            {tab === 'Market' && <MarketExplorer />}
            {tab === 'Prices' && <PriceTracking />}
            {tab === 'Providers' && <ProviderHealthPage />}
            {tab === 'Events' && <EventFeed />}
            {tab === 'Settings' && <Settings />}
          </>
        )}
      </main>
    </div>
  )
}

function LoginScreen() {
  const { login, register } = useAuth()
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      if (mode === 'login') await login(email, password)
      else await register(email, password, displayName || email)
    } catch (failure) {
      setError((failure as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="mx-auto flex min-h-full max-w-md flex-col justify-center p-8">
      <Card title={mode === 'login' ? 'Sign in' : 'Create an account'}>
        <form className="grid gap-3" onSubmit={submit}>
          {mode === 'register' && (
            <Field label="Display name">
              <input
                className={inputClass}
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
              />
            </Field>
          )}
          <Field label="Email">
            <input
              type="email"
              className={inputClass}
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </Field>
          <Field label="Password">
            <input
              type="password"
              className={inputClass}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              minLength={mode === 'register' ? 12 : undefined}
              required
            />
            {mode === 'register' && (
              <p className="mt-1 text-xs text-[var(--color-muted)]">At least 12 characters.</p>
            )}
          </Field>
          {error && <p className="text-sm text-[var(--color-loss)]">{error}</p>}
          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? 'Working…' : mode === 'login' ? 'Sign in' : 'Create account'}
          </Button>
          <Button
            variant="ghost"
            onClick={() => setMode(mode === 'login' ? 'register' : 'login')}
          >
            {mode === 'login' ? 'Need an account?' : 'Already have one?'}
          </Button>
        </form>
      </Card>
    </main>
  )
}
