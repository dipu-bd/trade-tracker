import { clsx } from 'clsx'
import type { ReactNode } from 'react'

export function Card({
  title,
  action,
  children,
  className,
}: {
  title?: ReactNode
  action?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section
      className={clsx(
        // min-w-0 because a grid or flex item defaults to min-width:auto, which lets a wide
        // table stretch its track and scroll the whole page sideways instead of itself.
        'min-w-0 rounded-lg border border-[var(--color-border-subtle)] bg-[var(--color-surface-raised)]',
        className,
      )}
    >
      {(title || action) && (
        <header className="flex items-center justify-between gap-3 border-b border-[var(--color-border-subtle)] px-4 py-3">
          <h2 className="text-sm font-medium">{title}</h2>
          {action}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  )
}

export function Stat({
  label,
  value,
  hint,
  className,
}: {
  label: string
  value: ReactNode
  hint?: ReactNode
  className?: string
}) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-[var(--color-ink-muted)]">{label}</div>
      <div className={clsx('mt-1 text-lg tabular-nums', className)}>{value}</div>
      {hint && <div className="text-xs text-[var(--color-ink-muted)]">{hint}</div>}
    </div>
  )
}

const BADGE_TONES: Record<string, string> = {
  ok: 'bg-[color-mix(in_oklab,var(--color-gain)_18%,transparent)] text-[var(--color-gain)]',
  bad: 'bg-[color-mix(in_oklab,var(--color-loss)_18%,transparent)] text-[var(--color-loss)]',
  warn: 'bg-[color-mix(in_oklab,var(--color-warn)_20%,transparent)] text-[var(--color-warn)]',
  accent: 'bg-[color-mix(in_oklab,var(--color-accent)_20%,transparent)] text-[var(--color-accent)]',
  muted: 'bg-[var(--color-surface-sunken)] text-[var(--color-ink-muted)]',
}

export function Badge({
  children,
  tone = 'muted',
  title,
}: {
  children: ReactNode
  tone?: keyof typeof BADGE_TONES
  title?: string
}) {
  return (
    <span
      title={title}
      className={clsx(
        'inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium',
        BADGE_TONES[tone],
      )}
    >
      {children}
    </span>
  )
}

export function Button({
  children,
  onClick,
  variant = 'default',
  disabled,
  type = 'button',
}: {
  children: ReactNode
  onClick?: () => void
  variant?: 'default' | 'primary' | 'ghost' | 'danger'
  disabled?: boolean
  type?: 'button' | 'submit'
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={clsx(
        'inline-flex min-h-9 items-center justify-center gap-1.5 rounded px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-40',
        variant === 'primary' &&
          'bg-[var(--color-accent)] text-[var(--color-accent-ink)] hover:opacity-90',
        variant === 'default' &&
          'border border-[var(--color-border-subtle)] hover:bg-[var(--color-surface-sunken)]',
        variant === 'ghost' && 'text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]',
        variant === 'danger' &&
          'border border-[color-mix(in_oklab,var(--color-loss)_40%,transparent)] text-[var(--color-loss)] hover:bg-[color-mix(in_oklab,var(--color-loss)_12%,transparent)]',
      )}
    >
      {children}
    </button>
  )
}

export function Table({ head, children }: { head: string[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-[var(--color-border-subtle)] text-left">
            {head.map((column) => (
              <th
                key={column}
                className="whitespace-nowrap px-2 py-2 text-xs font-medium uppercase tracking-wide text-[var(--color-ink-muted)]"
              >
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  )
}

export function Row({
  children,
  onClick,
  selected,
}: {
  children: ReactNode
  onClick?: () => void
  selected?: boolean
}) {
  return (
    <tr
      onClick={onClick}
      aria-selected={selected}
      className={clsx(
        'border-b border-[var(--color-border-subtle)] last:border-0',
        onClick && 'cursor-pointer hover:bg-[var(--color-surface-sunken)]',
        selected &&
          'bg-[var(--color-surface-raised)] font-medium shadow-[inset_3px_0_0_var(--color-accent,currentColor)]',
      )}
    >
      {children}
    </tr>
  )
}

export function Cell({
  children,
  className,
  mono,
  title,
}: {
  children: ReactNode
  className?: string
  mono?: boolean
  title?: string
}) {
  return (
    <td
      title={title}
      className={clsx('px-2 py-2', mono && 'font-mono text-xs tabular-nums', className)}
    >
      {children}
    </td>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-6 text-center text-sm text-[var(--color-ink-muted)]">{children}</p>
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: ReactNode
}) {
  return (
    <label className="block">
      <span className="text-xs uppercase tracking-wide text-[var(--color-ink-muted)]">{label}</span>
      <div className="mt-1">{children}</div>
      {hint && <span className="mt-1 block text-xs text-[var(--color-ink-muted)]">{hint}</span>}
    </label>
  )
}

export const inputClass =
  'w-full rounded border border-[var(--color-border-subtle)] bg-[var(--color-surface-base)] px-2 py-1.5 text-sm outline-none focus:border-[var(--color-accent)]'

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx('skeleton h-4 w-full', className)} />
}

export function TableSkeleton({ rows = 4, columns = 4 }: { rows?: number; columns?: number }) {
  return (
    <div className="grid gap-2 py-2">
      {Array.from({ length: rows }).map((_, row) => (
        <div key={row} className="flex gap-2">
          {Array.from({ length: columns }).map((_, column) => (
            <Skeleton key={column} className={column === 0 ? 'w-24' : 'flex-1'} />
          ))}
        </div>
      ))}
    </div>
  )
}

export function ErrorNote({ error, className }: { error: unknown; className?: string }) {
  if (!error) return null
  return (
    <p
      role="alert"
      className={clsx(
        'rounded border border-[color-mix(in_oklab,var(--color-loss)_40%,transparent)] bg-[color-mix(in_oklab,var(--color-loss)_12%,transparent)] px-3 py-2 text-sm text-[var(--color-loss)]',
        className,
      )}
    >
      {(error as Error).message || 'Something went wrong.'}
    </p>
  )
}

export function QueryState({
  query,
  empty,
  children,
  skeleton,
}: {
  query: { isPending: boolean; error: unknown; data: unknown; fetchStatus?: string }
  empty?: ReactNode
  children: ReactNode
  skeleton?: ReactNode
}) {
  // A query disabled by `enabled: false` stays pending forever, so without this the placeholder
  // is a skeleton that never resolves.
  if (query.isPending && query.fetchStatus === 'idle') {
    return <>{empty ? <Empty>{empty}</Empty> : null}</>
  }
  if (query.isPending) return <>{skeleton ?? <TableSkeleton />}</>
  if (query.error) return <ErrorNote error={query.error} />
  if (empty && Array.isArray(query.data) && query.data.length === 0) {
    return <Empty>{empty}</Empty>
  }
  return <>{children}</>
}
