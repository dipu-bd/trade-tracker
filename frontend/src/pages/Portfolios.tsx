import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { api } from '@/api/client'
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
          <Table
            head={['Symbol', 'Name', 'Qty', 'Avg cost', 'Market value', 'Unrealized', 'Realized']}
          >
            {positions.data.map((row) => (
              <Row key={row.id}>
                <Cell mono>{row.symbol || `#${row.instrument_id}`}</Cell>
                <Cell className="max-w-[16rem] truncate" title={row.name || undefined}>
                  {row.name || <span className="text-[var(--color-ink-muted)]">—</span>}
                </Cell>
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


function TradeCard({ id }: { id: number }) {
  const client = useQueryClient()
  const [mode, setMode] = useState<'order' | 'holding'>('order')
  const [symbol, setSymbol] = useState('')
  const [side, setSide] = useState('BUY')
  const [qty, setQty] = useState('')
  const [cost, setCost] = useState('')
  const [openedAt, setOpenedAt] = useState('')

  const refresh = () => {
    for (const key of ['orders', 'fills', 'ledger', 'portfolio', 'positions']) {
      void client.invalidateQueries({ queryKey: [key, id] })
    }
  }

  const submit = useMutation({
    mutationFn: () =>
      mode === 'order'
        ? api(`/portfolios/${id}/orders`, {
            method: 'POST',
            body: JSON.stringify({ symbol: symbol.toUpperCase(), side, qty }),
          })
        : api(`/portfolios/${id}/holdings`, {
            method: 'POST',
            body: JSON.stringify({
              symbol: symbol.toUpperCase(),
              qty,
              cost_basis: cost,
              opened_at: openedAt ? new Date(openedAt).toISOString() : null,
            }),
          }),
    onSuccess: () => {
      setSymbol('')
      setQty('')
      setCost('')
      refresh()
    },
  })

  return (
    <Card title="Trade manually">
      <div className="mb-3 flex gap-1">
        {(['order', 'holding'] as const).map((item) => (
          <button
            key={item}
            type="button"
            onClick={() => setMode(item)}
            className={`rounded px-2.5 py-1 text-sm transition ${
              mode === item
                ? 'bg-[var(--color-surface-raised)] font-medium'
                : 'text-[var(--color-ink-muted)]'
            }`}
          >
            {item === 'order' ? 'Place an order' : 'Seed a holding I already own'}
          </button>
        ))}
      </div>

      <p className="mb-3 text-sm text-[var(--color-ink-muted)]">
        {mode === 'order'
          ? 'A market order alongside whatever the engine does. It fills against the next quote and pays the portfolio’s slippage and commission.'
          : 'Records a position bought elsewhere at the price you actually paid. Cash is debited so the equity curve stays honest, but no slippage or commission is charged — that broker already took its cut.'}
      </p>

      <form
        className="grid items-end gap-3 sm:grid-cols-5"
        onSubmit={(event) => {
          event.preventDefault()
          submit.mutate()
        }}
      >
        <Field label="Symbol">
          <input
            className={inputClass}
            value={symbol}
            onChange={(event) => setSymbol(event.target.value)}
            placeholder="AAPL"
            required
          />
        </Field>
        {mode === 'order' && (
          <Field label="Side">
            <select
              className={inputClass}
              value={side}
              onChange={(event) => setSide(event.target.value)}
            >
              <option value="BUY">BUY</option>
              <option value="SELL">SELL</option>
            </select>
          </Field>
        )}
        <Field label="Quantity">
          <input
            className={inputClass}
            value={qty}
            onChange={(event) => setQty(event.target.value)}
            placeholder="10"
            required
          />
        </Field>
        {mode === 'holding' && (
          <>
            <Field label="Cost basis per unit">
              <input
                className={inputClass}
                value={cost}
                onChange={(event) => setCost(event.target.value)}
                placeholder="182.50"
                required
              />
            </Field>
            <Field label="Bought on">
              <input
                type="date"
                className={inputClass}
                value={openedAt}
                onChange={(event) => setOpenedAt(event.target.value)}
              />
            </Field>
          </>
        )}
        <Button type="submit" variant="primary" disabled={submit.isPending}>
          {submit.isPending ? 'Working…' : mode === 'order' ? 'Submit order' : 'Add holding'}
        </Button>
      </form>

      {submit.error && (
        <p className="mt-3 text-sm text-[var(--color-loss)]">{(submit.error as Error).message}</p>
      )}
      {submit.isSuccess && !submit.isPending && (
        <p className="mt-3 text-sm text-[var(--color-ink-muted)]">Done — see the blotter below.</p>
      )}
    </Card>
  )
}

export function Blotter({ id }: { id: number }) {
  const orders = useOrders(id)
  const fills = useFills(id)
  const ledger = useLedger(id)

  return (
    <div className="grid gap-4">
      <TradeCard id={id} />

      <Card title="Orders">
        {orders.data?.length ? (
          <Table head={['#', 'Symbol', 'Name', 'Side', 'Qty', 'Type', 'Status', 'Avg fill', 'Reason']}>
            {orders.data.map((row) => (
              <Row key={row.id}>
                <Cell mono>{row.id}</Cell>
                <Cell mono>{row.symbol || `#${row.instrument_id}`}</Cell>
                <Cell className="max-w-[14rem] truncate" title={row.name || undefined}>
                  {row.name || <span className="text-[var(--color-ink-muted)]">—</span>}
                </Cell>
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
