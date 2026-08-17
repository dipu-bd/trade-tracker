import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createChart, type IChartApi } from 'lightweight-charts'
import { useEffect, useRef, useState } from 'react'

import { api } from '@/api/client'
import { useBars, useInstruments } from '@/api/hooks'
import {
  Button,
  Card,
  Cell,
  ErrorNote,
  Field,
  QueryState,
  Row,
  Table,
  inputClass,
} from '@/components/ui'
import { ago, money, num } from '@/lib/format'

interface SyncStatus {
  label: string
  running: boolean
  started_at: string | null
  done: number
  total: number
  current: string
  error: string | null
  instruments: number
  bars_written: number
  quotes_updated: number
  skipped_fresh: number
  failed: string[]
}

const ASSET_CLASSES = ['etf', 'stock', 'crypto', 'commodity']

function Progress({ status }: { status: SyncStatus }) {
  const pct = status.total > 0 ? Math.round((status.done / status.total) * 100) : 0
  return (
    <div className="mt-3">
      {status.running && (
        <>
          <div className="h-1.5 w-full overflow-hidden rounded bg-[var(--color-border-subtle)]">
            <div className="h-full bg-[var(--color-accent)]" style={{ width: `${pct}%` }} />
          </div>
          <p className="mt-2 text-sm text-[var(--color-ink-muted)]">
            {status.label} — {status.current || 'starting'} ({status.done}/{status.total})
          </p>
        </>
      )}
      <p className="mt-2 text-sm">
        {status.instruments} instruments, {status.bars_written} bars, {status.quotes_updated}{' '}
        prices, {status.skipped_fresh} already fresh.
        {status.failed.length > 0 && (
          <span className="text-[var(--color-loss)]"> Failed: {status.failed.join('; ')}</span>
        )}
      </p>
      {status.error && <p className="mt-2 text-sm text-[var(--color-loss)]">{status.error}</p>}
    </div>
  )
}

function SyncPanel() {
  const client = useQueryClient()
  const [classes, setClasses] = useState<string[]>(ASSET_CLASSES)
  const [limit, setLimit] = useState(200)
  const [symbols, setSymbols] = useState('')

  const status = useQuery({
    queryKey: ['market-sync'],
    queryFn: () => api<SyncStatus>('/market/sync'),
    refetchInterval: (query) => (query.state.data?.running ? 1000 : false),
  })

  useEffect(() => {
    if (status.data && !status.data.running) {
      void client.invalidateQueries({ queryKey: ['instruments'] })
    }
  }, [client, status.data?.running])

  const start = useMutation({
    mutationFn: (path: string) =>
      api<SyncStatus>(path, {
        method: 'POST',
        body:
          path === '/market/sync'
            ? JSON.stringify({
                asset_classes: classes,
                limit,
                symbols: symbols
                  .split(/[,\s]+/)
                  .map((item) => item.trim().toUpperCase())
                  .filter(Boolean),
              })
            : undefined,
      }),
    onSuccess: (data) => client.setQueryData(['market-sync'], data),
  })

  const running = status.data?.running ?? false
  const toggle = (name: string) =>
    setClasses((current) =>
      current.includes(name) ? current.filter((item) => item !== name) : [...current, name],
    )

  return (
    <Card title="Market sync">
      <p className="mb-2 text-sm text-[var(--color-ink-muted)]">
        One pass over every asset class you tick: the universe, the daily bars and the last price.
        It runs in the background, so closing this page does not stop it.
      </p>
      <details className="mb-3 text-sm text-[var(--color-ink-muted)]">
        <summary className="cursor-pointer text-[var(--color-ink-faint)] hover:text-[var(--color-ink-muted)]">
          How the universe is chosen
        </summary>
        <p className="mt-2">
          Leave the symbol box empty to walk each class&rsquo;s ranked listing (most-actives and
          similar), or name symbols to pull exactly those: anything outside those listings — SPGI,
          say — is only reachable by name. A name needs about 260 sessions of history before the
          momentum rank and the 200-day filter are defined. A background job repeats this on its
          own schedule.
        </p>
      </details>

      <div className="mb-3 flex flex-wrap gap-3">
        {ASSET_CLASSES.map((name) => (
          <label key={name} className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={classes.includes(name)}
              onChange={() => toggle(name)}
            />
            {name}
          </label>
        ))}
      </div>

      <div className="grid items-end gap-3 sm:grid-cols-3">
        <div className="sm:col-span-2">
          <Field label="Symbols (blank for the provider listing)">
            <input
              className={inputClass}
              value={symbols}
              onChange={(event) => setSymbols(event.target.value)}
              placeholder="SPGI, MSFT, COST"
            />
          </Field>
        </div>
        <Field label="Max symbols per class (listing only)">
          <input
            type="number"
            min={1}
            max={5000}
            className={inputClass}
            value={limit}
            onChange={(event) => setLimit(Number(event.target.value))}
          />
        </Field>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Button
          variant="primary"
          disabled={running || classes.length === 0}
          onClick={() => start.mutate('/market/sync')}
        >
          {running ? 'Syncing…' : 'Sync now'}
        </Button>
        <Button disabled={running} onClick={() => start.mutate('/market/refresh')}>
          Refresh everything tracked
        </Button>
      </div>

      <ErrorNote error={start.error} className="mt-3" />
      {status.data?.started_at && <Progress status={status.data} />}
    </Card>
  )
}

export function MarketExplorer() {
  const [symbol, setSymbol] = useState<string | null>(null)
  const instruments = useInstruments()
  const bars = useBars(symbol)
  const selectedName = instruments.data?.find((row) => row.symbol === symbol)?.name ?? ''

  return (
    <div className="grid gap-4">
      <SyncPanel />
      <div className="grid gap-4 lg:grid-cols-[1fr_2fr]">
      <Card title="Instruments">
        <QueryState query={instruments} empty="No instruments tracked.">
          <div className="max-h-[32rem] overflow-y-auto">
            <Table head={['Symbol', 'Name', 'Class', 'Last', 'Quoted']}>
              {(instruments.data ?? []).map((row) => (
                <Row
                  key={row.id}
                  onClick={() => setSymbol(row.symbol)}
                  selected={row.symbol === symbol}
                >
                  <Cell mono>{row.symbol}</Cell>
                  <Cell className="max-w-[16rem] truncate" title={row.name || undefined}>
                    {row.name || <span className="text-[var(--color-ink-muted)]">—</span>}
                  </Cell>
                  <Cell>{row.asset_class}</Cell>
                  <Cell mono>{row.last_quote_price ? money(row.last_quote_price) : '—'}</Cell>
                  <Cell className="text-xs">{ago(row.last_quote_at)}</Cell>
                </Row>
              ))}
            </Table>
          </div>
        </QueryState>
      </Card>

      <Card
        title={
          symbol
            ? `${symbol}${selectedName ? ` — ${selectedName}` : ''} — daily`
            : 'Select an instrument'
        }
      >
        <QueryState query={bars} empty={<>{symbol ? 'No stored bars for this instrument.' : 'Pick a symbol to chart it.'}</>}>
          <CandleChart
            data={(bars.data ?? []).map((bar) => ({
              time: bar.bar_date,
              open: num(bar.open),
              high: num(bar.high),
              low: num(bar.low),
              close: num(bar.close),
            }))}
          />
        </QueryState>
        </Card>
      </div>
    </div>
  )
}

interface Candle {
  time: string
  open: number
  high: number
  low: number
  close: number
}

function CandleChart({ data }: { data: Candle[] }) {
  const container = useRef<HTMLDivElement>(null)
  const chart = useRef<IChartApi | null>(null)

  useEffect(() => {
    if (!container.current) return

    const token = (name: string) =>
      getComputedStyle(document.documentElement).getPropertyValue(name).trim()

    const instance = createChart(container.current, {
      height: 380,
      layout: {
        background: { color: 'transparent' },
        textColor: token('--color-chart-ink'),
      },
      grid: {
        vertLines: { color: token('--color-chart-grid') },
        horzLines: { color: token('--color-chart-grid') },
      },
      timeScale: { borderVisible: false },
      rightPriceScale: { borderVisible: false },
    })
    const up = token('--color-chart-gain')
    const down = token('--color-chart-loss')
    const series = instance.addCandlestickSeries({
      upColor: up,
      downColor: down,
      borderUpColor: up,
      borderDownColor: down,
      wickUpColor: up,
      wickDownColor: down,
    })
    series.setData(data)
    instance.timeScale().fitContent()
    chart.current = instance

    const resize = () => {
      if (container.current) instance.applyOptions({ width: container.current.clientWidth })
    }
    resize()
    window.addEventListener('resize', resize)

    return () => {
      window.removeEventListener('resize', resize)
      instance.remove()
      chart.current = null
    }
  }, [data])

  return <div ref={container} className="w-full" />
}
