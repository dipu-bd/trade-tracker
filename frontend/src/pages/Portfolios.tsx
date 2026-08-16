import { useState } from 'react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import {
  useCreatePortfolio,
  useFills,
  useLedger,
  useOrders,
  usePortfolio,
  usePortfolios,
  usePositions,
  useReconcile,
  useRunCycle,
  useSnapshots,
} from '@/api/hooks'
import { Badge, Button, Card, Cell, Empty, Field, Row, Stat, Table, inputClass } from '@/components/ui'
import { money, num, percent, qty, tone, when } from '@/lib/format'

export function PortfolioList({ onOpen }: { onOpen: (id: number) => void }) {
  const portfolios = usePortfolios()
  const create = useCreatePortfolio()
  const [name, setName] = useState('')
  const [capital, setCapital] = useState('100000')

  return (
    <div className="grid gap-4">
      <Card title="Portfolios">
        {portfolios.data?.length ? (
          <Table head={['Name', 'Benchmark', 'Initial capital', 'Created', '']}>
            {portfolios.data.map((row) => (
              <Row key={row.id} onClick={() => onOpen(row.id)}>
                <Cell>{row.name}</Cell>
                <Cell mono>{row.base_currency}</Cell>
                <Cell mono>{money(row.initial_capital)}</Cell>
                <Cell>{when(row.created_at)}</Cell>
                <Cell>
                  <Badge tone={row.is_active ? 'ok' : 'muted'}>
                    {row.is_active ? 'active' : 'paused'}
                  </Badge>
                </Cell>
              </Row>
            ))}
          </Table>
        ) : (
          <Empty>No portfolios yet. Create one below to begin paper trading.</Empty>
        )}
      </Card>

      <Card title="New portfolio">
        <form
          className="grid gap-3 sm:grid-cols-3"
          onSubmit={(event) => {
            event.preventDefault()
            create.mutate(
              { name, initial_capital: capital, allow_fractional: true },
              { onSuccess: () => setName('') },
            )
          }}
        >
          <Field label="Name">
            <input
              className={inputClass}
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
            />
          </Field>
          <Field label="Initial capital">
            <input
              className={inputClass}
              value={capital}
              onChange={(event) => setCapital(event.target.value)}
              required
            />
          </Field>
          <div className="flex items-end">
            <Button type="submit" variant="primary" disabled={create.isPending || !name}>
              {create.isPending ? 'Creating…' : 'Create'}
            </Button>
          </div>
        </form>
        {create.error && (
          <p className="mt-2 text-sm text-[var(--color-loss)]">{(create.error as Error).message}</p>
        )}
      </Card>
    </div>
  )
}

export function PortfolioDetailPage({ id }: { id: number }) {
  const detail = usePortfolio(id)
  const positions = usePositions(id)
  const snapshots = useSnapshots(id)
  const runCycle = useRunCycle(id)
  const reconcile = useReconcile(id)

  const curve = (snapshots.data ?? []).map((row) => ({
    date: row.snap_date,
    equity: num(row.equity),
  }))

  const equity = num(detail.data?.equity)
  const initial = num(detail.data?.initial_capital)
  const growth = initial > 0 ? equity / initial - 1 : 0

  return (
    <div className="grid gap-4">
      <Card
        title={detail.data?.name ?? 'Portfolio'}
        action={
          <div className="flex gap-2">
            <Button onClick={() => reconcile.mutate()}>Reconcile</Button>
            <Button variant="primary" onClick={() => runCycle.mutate()} disabled={runCycle.isPending}>
              {runCycle.isPending ? 'Running…' : 'Run cycle'}
            </Button>
          </div>
        }
      >
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
          <Stat label="Equity" value={money(detail.data?.equity)} className={tone(growth)} />
          <Stat label="Growth" value={percent(growth)} className={tone(growth)} />
          <Stat label="Cash" value={money(detail.data?.cash)} />
          <Stat label="Buying power" value={money(detail.data?.buying_power)} />
          <Stat label="Open positions" value={detail.data?.open_positions ?? 0} />
        </div>

        {reconcile.data && (
          <p className="mt-3 text-sm">
            {reconcile.data.ok ? (
              <Badge tone="ok">ledger reconciles</Badge>
            ) : (
              <Badge tone="bad">{reconcile.data.problems.join('; ')}</Badge>
            )}
          </p>
        )}
        {runCycle.data && (
          <p className="mt-3 text-sm text-[var(--color-ink-muted)]">
            Cycle #{runCycle.data.run_id}: regime {runCycle.data.regime || '—'},{' '}
            {runCycle.data.entries} entries, {runCycle.data.exits} exits,{' '}
            {runCycle.data.orders_placed} orders.
          </p>
        )}
      </Card>

      <Card title="Equity curve">
        {curve.length > 1 ? (
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={curve}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-subtle)" />
                <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} domain={['auto', 'auto']} width={70} />
                <Tooltip formatter={(value: number) => money(value)} />
                <Line
                  type="monotone"
                  dataKey="equity"
                  stroke="var(--color-accent)"
                  dot={false}
                  strokeWidth={2}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <Empty>Not enough snapshots yet. Equity points accumulate daily.</Empty>
        )}
      </Card>

      <Card title="Positions">
        {positions.data?.length ? (
          <Table head={['Instrument', 'Qty', 'Avg cost', 'Market value', 'Unrealized', 'Realized']}>
            {positions.data.map((row) => (
              <Row key={row.id}>
                <Cell mono>#{row.instrument_id}</Cell>
                <Cell mono>{qty(row.qty)}</Cell>
                <Cell mono>{money(row.avg_cost)}</Cell>
                <Cell mono>{money(row.market_value)}</Cell>
                <Cell mono className={tone(num(row.unrealized_pnl))}>
                  {money(row.unrealized_pnl)}
                </Cell>
                <Cell mono className={tone(num(row.realized_pnl))}>
                  {money(row.realized_pnl)}
                </Cell>
              </Row>
            ))}
          </Table>
        ) : (
          <Empty>No open positions.</Empty>
        )}
      </Card>
    </div>
  )
}

export function Blotter({ id }: { id: number }) {
  const orders = useOrders(id)
  const fills = useFills(id)
  const ledger = useLedger(id)

  return (
    <div className="grid gap-4">
      <Card title="Orders">
        {orders.data?.length ? (
          <Table head={['#', 'Side', 'Qty', 'Type', 'Status', 'Avg fill', 'Reason']}>
            {orders.data.map((row) => (
              <Row key={row.id}>
                <Cell mono>{row.id}</Cell>
                <Cell>
                  <Badge tone={row.side === 'BUY' ? 'ok' : 'warn'}>{row.side}</Badge>
                </Cell>
                <Cell mono>{qty(row.qty)}</Cell>
                <Cell>{row.order_type}</Cell>
                <Cell>
                  <Badge tone={row.status === 'REJECTED' ? 'bad' : 'muted'}>{row.status}</Badge>
                </Cell>
                <Cell mono>{money(row.avg_fill_price)}</Cell>
                <Cell className="text-xs text-[var(--color-ink-muted)]">
                  {row.reject_reason ?? ''}
                </Cell>
              </Row>
            ))}
          </Table>
        ) : (
          <Empty>No orders yet.</Empty>
        )}
      </Card>

      <Card title="Fills">
        {fills.data?.length ? (
          <Table head={['#', 'Order', 'Qty', 'Price', 'Fee', 'Slippage', 'When']}>
            {fills.data.map((row) => (
              <Row key={row.id}>
                <Cell mono>{row.id}</Cell>
                <Cell mono>{row.order_id}</Cell>
                <Cell mono>{qty(row.qty)}</Cell>
                <Cell mono>{money(row.price)}</Cell>
                <Cell mono>{money(row.fee)}</Cell>
                <Cell mono>{money(row.slippage_amount)}</Cell>
                <Cell>{when(row.executed_at)}</Cell>
              </Row>
            ))}
          </Table>
        ) : (
          <Empty>No fills yet.</Empty>
        )}
      </Card>

      <Card title="Ledger">
        {ledger.data?.length ? (
          <Table head={['When', 'Type', 'Amount', 'Balance after', 'Memo']}>
            {ledger.data.map((row) => (
              <Row key={row.id}>
                <Cell>{when(row.at)}</Cell>
                <Cell>{row.entry_type}</Cell>
                <Cell mono className={tone(num(row.amount))}>
                  {money(row.amount)}
                </Cell>
                <Cell mono>{money(row.balance_after)}</Cell>
                <Cell className="text-xs text-[var(--color-ink-muted)]">{row.memo ?? ''}</Cell>
              </Row>
            ))}
          </Table>
        ) : (
          <Empty>The ledger is empty.</Empty>
        )}
      </Card>
    </div>
  )
}
