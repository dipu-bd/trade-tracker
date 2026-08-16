import { useEffect, useMemo, useState } from 'react'

import { subscribeToEvents } from '@/api/client'
import { useEvents, useInstruments, useProviders } from '@/api/hooks'
import type { EventRow } from '@/api/types'
import { Badge, Card, Cell, Empty, Row, Stat, Table } from '@/components/ui'
import { ago, money, num, percent, when } from '@/lib/format'

const STALE_SECONDS = 900

export function PriceTracking() {
  const instruments = useInstruments()
  const rows = instruments.data ?? []

  const stale = rows.filter(
    (row) => row.last_quote_at && (Date.now() - new Date(row.last_quote_at).getTime()) / 1000 > STALE_SECONDS,
  ).length

  return (
    <div className="grid gap-4">
      <Card title="Coverage">
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat label="Instruments" value={rows.length} />
          <Stat label="Quoted" value={rows.filter((row) => row.last_quote_price).length} />
          <Stat label="Stale" value={stale} hint={`older than ${STALE_SECONDS / 60}m`} />
          <Stat label="With bars" value={rows.filter((row) => row.last_bar_date).length} />
        </div>
      </Card>

      <Card title="Price tracking">
        {rows.length ? (
          <Table head={['Symbol', 'Class', 'Last price', 'Source', 'Staleness', 'Bars']}>
            {rows.map((row) => {
              const seconds = row.last_quote_at
                ? (Date.now() - new Date(row.last_quote_at).getTime()) / 1000
                : null
              return (
                <Row key={row.id}>
                  <Cell mono>{row.symbol}</Cell>
                  <Cell>{row.asset_class}</Cell>
                  <Cell mono>{row.last_quote_price ? money(row.last_quote_price) : '—'}</Cell>
                  <Cell>{row.last_quote_source ?? '—'}</Cell>
                  <Cell>
                    {seconds === null ? (
                      <Badge tone="muted">never</Badge>
                    ) : (
                      <Badge tone={seconds > STALE_SECONDS ? 'warn' : 'ok'}>
                        {ago(row.last_quote_at)}
                      </Badge>
                    )}
                  </Cell>
                  <Cell mono>
                    {row.first_bar_date ? `${row.first_bar_date} → ${row.last_bar_date}` : '—'}
                  </Cell>
                </Row>
              )
            })}
          </Table>
        ) : (
          <Empty>No instruments tracked yet. Sync a universe from the market page.</Empty>
        )}
      </Card>
    </div>
  )
}

export function ProviderHealthPage() {
  const providers = useProviders()

  return (
    <Card title="Provider health">
      {providers.data?.length ? (
        <Table head={['Provider', 'State', 'Capabilities', 'Error rate', 'p50 / p95', 'Detail']}>
          {providers.data.map((row) => {
            const health = (row.health ?? {}) as Record<string, unknown>
            const circuit = String(health.circuit ?? health.state ?? 'closed')
            return (
              <Row key={row.provider}>
                <Cell mono>{row.provider}</Cell>
                <Cell>
                  {row.available ? (
                    <Badge tone={circuit === 'open' ? 'bad' : 'ok'}>
                      {circuit === 'open' ? 'circuit open' : 'available'}
                    </Badge>
                  ) : (
                    <Badge tone={row.configured ? 'warn' : 'muted'}>
                      {row.configured ? 'unavailable' : 'no credentials'}
                    </Badge>
                  )}
                </Cell>
                <Cell className="text-xs">{row.capabilities.join(', ')}</Cell>
                <Cell mono>{percent(num(health.error_rate))}</Cell>
                <Cell mono>
                  {num(health.latency_p50).toFixed(2)}s / {num(health.latency_p95).toFixed(2)}s
                </Cell>
                <Cell className="text-xs text-[var(--color-ink-muted)]">
                  {row.missing_credentials.length
                    ? `missing: ${row.missing_credentials.join(', ')}`
                    : String(health.last_error ?? '')}
                </Cell>
              </Row>
            )
          })}
        </Table>
      ) : (
        <Empty>No providers registered.</Empty>
      )}
    </Card>
  )
}

const SEVERITY_TONE: Record<string, 'ok' | 'warn' | 'bad' | 'muted'> = {
  info: 'muted',
  warning: 'warn',
  error: 'bad',
  critical: 'bad',
}

export function EventFeed() {
  const [domain, setDomain] = useState('')
  const [severity, setSeverity] = useState('')
  const [live, setLive] = useState<EventRow[]>([])
  const stored = useEvents({ domain: domain || undefined, severity: severity || undefined })

  useEffect(() => {
    return subscribeToEvents((event) => {
      setLive((current) => [event as EventRow, ...current].slice(0, 200))
    })
  }, [])

  const rows = useMemo(() => {
    const merged = [...live, ...(stored.data ?? [])]
    return merged
      .filter((row) => (domain ? row.domain === domain : true))
      .filter((row) => (severity ? row.severity === severity : true))
      .slice(0, 200)
  }, [live, stored.data, domain, severity])

  return (
    <Card
      title="Event feed"
      action={
        <div className="flex gap-2 text-sm">
          <select
            value={domain}
            onChange={(event) => setDomain(event.target.value)}
            className="rounded border border-[var(--color-border-subtle)] bg-[var(--color-surface-base)] px-2 py-1"
          >
            <option value="">all domains</option>
            {['market', 'provider', 'ai', 'engine', 'broker', 'risk', 'auth'].map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
          <select
            value={severity}
            onChange={(event) => setSeverity(event.target.value)}
            className="rounded border border-[var(--color-border-subtle)] bg-[var(--color-surface-base)] px-2 py-1"
          >
            <option value="">all severities</option>
            {['info', 'warning', 'error'].map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </div>
      }
    >
      {rows.length ? (
        <Table head={['When', 'Domain', 'Kind', 'Severity', 'Message', 'Correlation']}>
          {rows.map((row, index) => (
            <Row key={`${row.id ?? 'live'}-${index}`}>
              <Cell mono>{when(row.created_at)}</Cell>
              <Cell>{row.domain}</Cell>
              <Cell mono>{row.kind}</Cell>
              <Cell>
                <Badge tone={SEVERITY_TONE[row.severity] ?? 'muted'}>{row.severity}</Badge>
              </Cell>
              <Cell className="text-xs">{row.message ?? ''}</Cell>
              <Cell mono className="text-xs">
                {row.correlation_id?.slice(0, 8) ?? '—'}
              </Cell>
            </Row>
          ))}
        </Table>
      ) : (
        <Empty>Nothing yet. Events appear here live as the system works.</Empty>
      )}
    </Card>
  )
}
