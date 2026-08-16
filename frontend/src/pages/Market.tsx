import { useMutation, useQueryClient } from '@tanstack/react-query'
import { createChart, type IChartApi } from 'lightweight-charts'
import { useEffect, useRef, useState } from 'react'

import { api } from '@/api/client'
import { useBars, useInstruments } from '@/api/hooks'
import { Button, Card, Cell, Empty, Field, Row, Table, inputClass } from '@/components/ui'
import { ago, money, num } from '@/lib/format'

interface SyncResult {
  asset_class: string
  instruments: number
  bars_written: number | null
  failed: string[]
}

function SyncPanel() {
  const client = useQueryClient()
  const [assetClass, setAssetClass] = useState('etf')
  const [limit, setLimit] = useState(50)

  const sync = useMutation({
    mutationFn: () =>
      api<SyncResult>('/market/sync', {
        method: 'POST',
        body: JSON.stringify({ asset_class: assetClass, limit, refresh_bars: true }),
      }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['instruments'] }),
  })

  return (
    <Card title="Track instruments">
      <p className="mb-3 text-sm text-[var(--color-ink-muted)]">
        The engine only ever considers instruments it already tracks with stored bars — nothing
        is discovered automatically. Sync once to populate the universe; a daily job keeps the
        bars current after that. A name needs about 260 sessions of history before the momentum
        rank and the 200-day filter are defined, so a fresh sync may screen out everything for a
        moment.
      </p>
      <div className="grid items-end gap-3 sm:grid-cols-4">
        <Field label="Asset class">
          <select
            className={inputClass}
            value={assetClass}
            onChange={(event) => setAssetClass(event.target.value)}
          >
            <option value="etf">etf</option>
            <option value="stock">stock</option>
            <option value="crypto">crypto</option>
            <option value="commodity">commodity</option>
          </select>
        </Field>
        <Field label="Max symbols">
          <input
            type="number"
            min={1}
            max={500}
            className={inputClass}
            value={limit}
            onChange={(event) => setLimit(Number(event.target.value))}
          />
        </Field>
        <Button variant="primary" disabled={sync.isPending} onClick={() => sync.mutate()}>
          {sync.isPending ? 'Fetching…' : 'Sync and fetch bars'}
        </Button>
      </div>

      {sync.error && (
        <p className="mt-3 text-sm text-[var(--color-loss)]">{(sync.error as Error).message}</p>
      )}
      {sync.data && (
        <p className="mt-3 text-sm">
          Tracked {sync.data.instruments} instruments, wrote {sync.data.bars_written ?? 0} bars.
          {sync.data.failed.length > 0 && ` Failed: ${sync.data.failed.join(', ')}`}
        </p>
      )}
    </Card>
  )
}

export function MarketExplorer() {
  const [symbol, setSymbol] = useState<string | null>(null)
  const instruments = useInstruments()
  const bars = useBars(symbol)

  return (
    <div className="grid gap-4">
      <SyncPanel />
      <div className="grid gap-4 lg:grid-cols-[1fr_2fr]">
      <Card title="Instruments">
        {instruments.data?.length ? (
          <div className="max-h-[32rem] overflow-y-auto">
            <Table head={['Symbol', 'Class', 'Last', 'Quoted']}>
              {instruments.data.map((row) => (
                <Row key={row.id} onClick={() => setSymbol(row.symbol)}>
                  <Cell mono>{row.symbol}</Cell>
                  <Cell>{row.asset_class}</Cell>
                  <Cell mono>{row.last_quote_price ? money(row.last_quote_price) : '—'}</Cell>
                  <Cell className="text-xs">{ago(row.last_quote_at)}</Cell>
                </Row>
              ))}
            </Table>
          </div>
        ) : (
          <Empty>No instruments tracked.</Empty>
        )}
      </Card>

      <Card title={symbol ? `${symbol} — daily` : 'Select an instrument'}>
        {bars.data?.length ? (
          <CandleChart
            data={bars.data.map((bar) => ({
              time: bar.bar_date,
              open: num(bar.open),
              high: num(bar.high),
              low: num(bar.low),
              close: num(bar.close),
            }))}
          />
        ) : (
          <Empty>{symbol ? 'No stored bars for this instrument.' : 'Pick a symbol to chart it.'}</Empty>
        )}
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

    const instance = createChart(container.current, {
      height: 380,
      layout: {
        background: { color: 'transparent' },
        textColor: getComputedStyle(document.documentElement).getPropertyValue('--color-ink-muted'),
      },
      grid: {
        vertLines: { color: 'rgba(127,127,127,0.1)' },
        horzLines: { color: 'rgba(127,127,127,0.1)' },
      },
      timeScale: { borderVisible: false },
      rightPriceScale: { borderVisible: false },
    })
    const series = instance.addCandlestickSeries()
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
