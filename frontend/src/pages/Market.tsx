import { createChart, type IChartApi } from 'lightweight-charts'
import { useEffect, useRef, useState } from 'react'

import { useBars, useInstruments } from '@/api/hooks'
import { Card, Cell, Empty, Row, Table } from '@/components/ui'
import { ago, money, num } from '@/lib/format'

export function MarketExplorer() {
  const [symbol, setSymbol] = useState<string | null>(null)
  const instruments = useInstruments()
  const bars = useBars(symbol)

  return (
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
