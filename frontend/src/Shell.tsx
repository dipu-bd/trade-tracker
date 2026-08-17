import { Link, Outlet, useNavigate, useRouterState } from '@tanstack/react-router'
import { clsx } from 'clsx'
import {
  Activity,
  Bot,
  ClipboardList,
  FlaskConical,
  LineChart,
  Menu,
  MessageSquare,
  PlugZap,
  ScrollText,
  Settings as SettingsIcon,
  Target,
  Wallet,
  X,
} from 'lucide-react'
import { useEffect, useState, type ComponentType } from 'react'

import { usePortfolios } from '@/api/hooks'
import { Button, Card } from '@/components/ui'
import { useAuth } from '@/auth'

interface NavItem {
  label: string
  path: string
  icon: ComponentType<{ className?: string }>
  scoped?: boolean
}

const GROUPS: { heading: string; items: NavItem[] }[] = [
  {
    heading: 'Portfolios',
    items: [
      { label: 'All portfolios', path: '/portfolios', icon: Wallet },
      { label: 'Overview', path: 'detail', icon: LineChart, scoped: true },
      { label: 'Blotter', path: 'blotter', icon: ClipboardList, scoped: true },
      { label: 'Strategy', path: 'strategy', icon: Target, scoped: true },
      { label: 'AI log', path: 'ai', icon: Bot, scoped: true },
      { label: 'Backtest', path: 'backtest', icon: FlaskConical, scoped: true },
      { label: 'Chat', path: 'chat', icon: MessageSquare, scoped: true },
    ],
  },
  {
    heading: 'Market',
    items: [
      { label: 'Explorer', path: '/market', icon: LineChart },
      { label: 'Prices', path: '/prices', icon: Activity },
      { label: 'Providers', path: '/providers', icon: PlugZap },
      { label: 'Events', path: '/events', icon: ScrollText },
    ],
  },
  {
    heading: 'Account',
    items: [{ label: 'Settings', path: '/settings', icon: SettingsIcon }],
  },
]

function useCurrentPath() {
  return useRouterState({ select: (state) => state.location.pathname })
}

export function Shell() {
  const { user, logout } = useAuth()
  const portfolios = usePortfolios()
  const navigate = useNavigate()
  const path = useCurrentPath()
  const [drawerOpen, setDrawerOpen] = useState(false)

  const scopedMatch = /^\/portfolios\/(\d+)\/([a-z]+)/.exec(path)
  const activeId = scopedMatch ? Number(scopedMatch[1]) : (portfolios.data?.[0]?.id ?? null)
  const subpage = scopedMatch?.[2] ?? 'detail'

  useEffect(() => setDrawerOpen(false), [path])

  const href = (item: NavItem) =>
    item.scoped ? `/portfolios/${activeId}/${item.path}` : item.path

  const nav = (
    <nav className="grid gap-5">
      {GROUPS.map((group) => (
        <div key={group.heading}>
          <p className="px-2 pb-1 text-[0.7rem] font-medium uppercase tracking-wider text-[var(--color-ink-faint)]">
            {group.heading}
          </p>
          <ul className="grid gap-0.5">
            {group.items.map((item) => {
              const target = href(item)
              const disabled = item.scoped && activeId === null
              const current = path === target
              return (
                <li key={item.label}>
                  {disabled ? (
                    <span className="flex items-center gap-2 rounded px-2 py-1.5 text-sm text-[var(--color-ink-faint)]">
                      <item.icon className="size-4" />
                      {item.label}
                    </span>
                  ) : (
                    <Link
                      to={target}
                      aria-current={current ? 'page' : undefined}
                      className={clsx(
                        'flex items-center gap-2 rounded px-2 py-1.5 text-sm transition',
                        current
                          ? 'bg-[var(--color-surface-overlay)] font-medium text-[var(--color-ink)]'
                          : 'text-[var(--color-ink-muted)] hover:bg-[var(--color-surface-sunken)] hover:text-[var(--color-ink)]',
                      )}
                    >
                      <item.icon className="size-4 shrink-0" />
                      {item.label}
                    </Link>
                  )}
                </li>
              )
            })}
          </ul>
        </div>
      ))}
    </nav>
  )

  return (
    <div className="flex min-h-full flex-col lg:flex-row">
      <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-[var(--color-border-subtle)] bg-[var(--color-surface-base)] px-4 py-2.5 lg:hidden">
        <Button variant="ghost" onClick={() => setDrawerOpen(true)}>
          <Menu className="size-5" />
          <span className="sr-only">Open navigation</span>
        </Button>
        <span className="font-semibold tracking-tight">Tradebot</span>
      </header>

      {drawerOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            type="button"
            aria-label="Close navigation"
            className="absolute inset-0 bg-black/60"
            onClick={() => setDrawerOpen(false)}
          />
          <aside className="relative flex h-full w-72 max-w-[85%] flex-col gap-4 overflow-y-auto border-r border-[var(--color-border-subtle)] bg-[var(--color-surface-raised)] p-4">
            <div className="flex items-center justify-between">
              <span className="font-semibold tracking-tight">Tradebot</span>
              <Button variant="ghost" onClick={() => setDrawerOpen(false)}>
                <X className="size-5" />
                <span className="sr-only">Close navigation</span>
              </Button>
            </div>
            {nav}
          </aside>
        </div>
      )}

      <aside className="hidden w-60 shrink-0 flex-col gap-5 border-r border-[var(--color-border-subtle)] bg-[var(--color-surface-raised)] p-4 lg:flex">
        <div>
          <h1 className="text-base font-semibold tracking-tight">Tradebot</h1>
          <p className="text-xs text-[var(--color-ink-muted)]">AI paper trading</p>
        </div>
        {nav}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--color-border-subtle)] px-4 py-2.5 sm:px-6">
          {portfolios.data && portfolios.data.length > 0 ? (
            <select
              aria-label="Active portfolio"
              value={activeId ?? ''}
              onChange={(event) =>
                void navigate(
                  path.startsWith('/settings')
                    ? { to: '/settings', search: { portfolio: Number(event.target.value) } }
                    : { to: `/portfolios/${event.target.value}/${subpage}` },
                )
              }
              className="min-h-9 rounded border border-[var(--color-border-subtle)] bg-[var(--color-surface-base)] px-2 py-1 text-sm"
            >
              {portfolios.data.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.name}
                </option>
              ))}
            </select>
          ) : (
            <span className="text-sm text-[var(--color-ink-muted)]">No portfolios yet</span>
          )}
          <div className="flex items-center gap-2 text-sm">
            <span className="hidden text-[var(--color-ink-muted)] sm:inline">
              {user?.display_name}
            </span>
            <Button variant="ghost" onClick={() => void logout()}>
              Sign out
            </Button>
          </div>
        </div>

        <main className="min-w-0 flex-1 p-4 sm:p-6">
          {scopedMatch && activeId === null ? (
            <Card>
              <p className="text-sm text-[var(--color-ink-muted)]">
                Create a portfolio first — this view needs one.
              </p>
            </Card>
          ) : (
            <Outlet />
          )}
        </main>
      </div>
    </div>
  )
}
