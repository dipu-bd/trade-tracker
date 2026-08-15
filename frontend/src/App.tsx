import { useQuery } from '@tanstack/react-query'

import { api } from '@/api/client'

interface Health {
  status: string
  env: string
  database: string
}

export function App() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['health'],
    queryFn: () => api<Health>('/health'),
  })

  return (
    <main className="mx-auto flex min-h-full max-w-2xl flex-col justify-center gap-6 p-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Tradebot</h1>
        <p className="text-[var(--color-ink-muted)]">AI paper-trading portfolio manager</p>
      </header>

      <section className="rounded-lg border border-[var(--color-border-subtle)] bg-[var(--color-surface-raised)] p-5">
        <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-[var(--color-ink-muted)]">
          Backend
        </h2>
        {isLoading && <p className="text-[var(--color-ink-muted)]">Checking…</p>}
        {error && <p className="text-[var(--color-loss)]">Unreachable</p>}
        {data && (
          <dl className="grid grid-cols-3 gap-4 text-sm">
            <div>
              <dt className="text-[var(--color-ink-muted)]">Status</dt>
              <dd className="text-[var(--color-gain)]">{data.status}</dd>
            </div>
            <div>
              <dt className="text-[var(--color-ink-muted)]">Environment</dt>
              <dd>{data.env}</dd>
            </div>
            <div>
              <dt className="text-[var(--color-ink-muted)]">Database</dt>
              <dd>{data.database}</dd>
            </div>
          </dl>
        )}
      </section>
    </main>
  )
}
