import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { Pause, Play, Trash2, X } from 'lucide-react'
import { useState } from 'react'

import { api } from '@/api/client'
import { useToast } from '@/components/toast'
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
  useCancelOrder,
  useCreatePortfolio,
  useDeletePortfolio,
  useFills,
  useLedger,
  useMatchOrders,
  useOrders,
  usePortfolio,
  usePortfolios,
  usePositions,
  useReconcile,
  useRunCycle,
  useSnapshots,
  useUpdatePortfolio,
} from '@/api/hooks'
import {
  Badge,
  Button,
  Card,
  Cell,
  Empty,
  ErrorNote,
  Field,
  QueryState,
  Row,
  Stat,
  Table,
  inputClass,
} from '@/components/ui'
import type { Order, Portfolio } from '@/api/types'
import { money, num, percent, qty, tone, when } from '@/lib/format'

function DeletePortfolio({
  portfolio,
  onDeleted,
}: {
  portfolio: Portfolio
  onDeleted?: () => void
}) {
  const toast = useToast()
  const remove = useDeletePortfolio()
  const [confirming, setConfirming] = useState(false)

  const destroy = () =>
    remove.mutate(portfolio.id, {
      onSuccess: () => {
        setConfirming(false)
        toast(`Deleted ${portfolio.name}.`, 'ok')
        onDeleted?.()
      },
      onError: (error) => toast((error as Error).message, 'bad'),
    })

  if (!confirming) {
    return (
      <Button variant="ghost" onClick={() => setConfirming(true)}>
        <Trash2 className="h-4 w-4" aria-hidden />
        Delete
      </Button>
    )
  }

  return (
    <span className="flex items-center gap-2 whitespace-nowrap">
      <span className="text-xs text-[var(--color-ink-muted)]">Erase it and its history?</span>
      <Button variant="danger" onClick={destroy} disabled={remove.isPending}>
        {remove.isPending ? 'Deleting…' : 'Delete'}
      </Button>
      <Button variant="ghost" onClick={() => setConfirming(false)} disabled={remove.isPending}>
        Cancel
      </Button>
    </span>
  )
}

function PauseResume({ portfolio }: { portfolio: Portfolio }) {
  const toast = useToast()
  const update = useUpdatePortfolio(portfolio.id)
  const paused = !portfolio.is_active

  const toggle = () =>
    update.mutate(
      { is_active: paused },
      {
        onSuccess: () =>
          toast(paused ? `Resumed ${portfolio.name}.` : `Paused ${portfolio.name}.`, 'ok'),
        onError: (error) => toast((error as Error).message, 'bad'),
      },
    )

  return (
    <Button
      variant="ghost"
      onClick={toggle}
      disabled={update.isPending}
      title={
        paused
          ? 'Resume scheduled cycles and order matching'
          : 'Stop scheduled cycles and order matching. Resting orders are left alone.'
      }
    >
      {paused ? <Play className="h-4 w-4" aria-hidden /> : <Pause className="h-4 w-4" aria-hidden />}
      {paused ? 'Resume' : 'Pause'}
    </Button>
  )
}

function PortfolioSettings({ portfolio }: { portfolio: Portfolio }) {
  const toast = useToast()
  const update = useUpdatePortfolio(portfolio.id)
  const [name, setName] = useState(portfolio.name)
  const [slippage, setSlippage] = useState(String(portfolio.slippage_bps))
  const [commission, setCommission] = useState(String(portfolio.commission_bps))
  const [minCommission, setMinCommission] = useState(String(portfolio.min_commission))
  const [fractional, setFractional] = useState(portfolio.allow_fractional)

  return (
    <Card title="Settings">
      <p className="mb-3 text-sm text-[var(--color-ink-muted)]">
        Costs apply to fills from here on. Trades already executed keep the prices they got, so
        the equity curve behind you does not move. Initial capital is the ledger’s first entry
        and cannot be restated.
      </p>
      <form
        className="grid items-end gap-3 sm:grid-cols-3"
        onSubmit={(event) => {
          event.preventDefault()
          update.mutate(
            {
              name,
              slippage_bps: slippage,
              commission_bps: commission,
              min_commission: minCommission,
              allow_fractional: fractional,
            },
            { onSuccess: () => toast('Settings saved.', 'ok') },
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
        <Field label="Slippage (bps)">
          <input
            className={inputClass}
            value={slippage}
            onChange={(event) => setSlippage(event.target.value)}
            required
          />
        </Field>
        <Field label="Commission (bps)">
          <input
            className={inputClass}
            value={commission}
            onChange={(event) => setCommission(event.target.value)}
            required
          />
        </Field>
        <Field label="Minimum commission">
          <input
            className={inputClass}
            value={minCommission}
            onChange={(event) => setMinCommission(event.target.value)}
            required
          />
        </Field>
        <Field label="Fractional units">
          <select
            className={inputClass}
            value={fractional ? 'yes' : 'no'}
            onChange={(event) => setFractional(event.target.value === 'yes')}
          >
            <option value="yes">Allowed</option>
            <option value="no">Whole units only</option>
          </select>
        </Field>
        <Button type="submit" variant="primary" disabled={update.isPending || !name}>
          {update.isPending ? 'Saving…' : 'Save settings'}
        </Button>
      </form>
      <ErrorNote error={update.error} className="mt-3" />
    </Card>
  )
}

export function PortfolioList() {
  const navigate = useNavigate()
  const portfolios = usePortfolios()
  const create = useCreatePortfolio()
  const [name, setName] = useState('')
  const [capital, setCapital] = useState('100000')

  return (
    <div className="grid gap-4">
      <Card title="Portfolios">
        <QueryState query={portfolios} empty="No portfolios yet. Create one below to begin paper trading.">
          <Table head={['Name', 'Benchmark', 'Initial capital', 'Created', 'Status', '']}>
            {(portfolios.data ?? []).map((row) => (
              <Row
                key={row.id}
                onClick={() => void navigate({ to: `/portfolios/${row.id}/detail` })}
              >
                <Cell>{row.name}</Cell>
                <Cell mono>{row.base_currency}</Cell>
                <Cell mono>{money(row.initial_capital)}</Cell>
                <Cell>{when(row.created_at)}</Cell>
                <Cell>
                  <Badge tone={row.is_active ? 'ok' : 'muted'}>
                    {row.is_active ? 'active' : 'paused'}
                  </Badge>
                </Cell>
                <Cell className="text-right">
                  {/* The row navigates on click, so these controls must not bubble. */}
                  <span
                    className="inline-flex"
                    onClickCapture={(event) => event.stopPropagation()}
                  >
                    <PauseResume portfolio={row} />
                    <DeletePortfolio portfolio={row} />
                  </span>
                </Cell>
              </Row>
            ))}
          </Table>
        </QueryState>
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
        <ErrorNote error={create.error} className="mt-3" />
      </Card>
    </div>
  )
}

export function PortfolioDetailPage({ id }: { id: number }) {
  const navigate = useNavigate()
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
  const paused = detail.data ? !detail.data.is_active : false

  return (
    <div className="grid gap-4">
      <Card
        title={detail.data?.name ?? 'Portfolio'}
        action={
          <div className="flex flex-wrap gap-2">
            {detail.data && <PauseResume portfolio={detail.data} />}
            {detail.data && (
              <DeletePortfolio
                portfolio={detail.data}
                onDeleted={() => void navigate({ to: '/portfolios' })}
              />
            )}
            <Button onClick={() => reconcile.mutate()}>Reconcile</Button>
            <Button
              variant="primary"
              onClick={() => runCycle.mutate()}
              disabled={runCycle.isPending || paused}
            >
              {runCycle.isPending ? 'Running…' : 'Run cycle'}
            </Button>
          </div>
        }
      >
        {paused && (
          <p className="mb-3 text-sm text-[var(--color-warn)]">
            Paused. Scheduled cycles and order matching are both stopped, and new orders are
            rejected. Resting orders are untouched and resume working when you do.
          </p>
        )}
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
        <QueryState query={positions} empty="No open positions.">
          <Table
            head={['Symbol', 'Name', 'Qty', 'Avg cost', 'Market value', 'Unrealized', 'Realized']}
          >
            {(positions.data ?? []).map((row) => (
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
        </QueryState>
      </Card>

      {/* Keyed on the id so switching portfolios refills the form rather than keeping the
          previous one's values in its state. */}
      {detail.data && <PortfolioSettings key={detail.data.id} portfolio={detail.data} />}
    </div>
  )
}


const ORDER_TYPES = [
  { value: 'MARKET', label: 'Market' },
  { value: 'LIMIT', label: 'Limit' },
  { value: 'STOP', label: 'Stop' },
  { value: 'STOP_LIMIT', label: 'Stop limit' },
] as const

const NEEDS_LIMIT = new Set(['LIMIT', 'STOP_LIMIT'])
const NEEDS_STOP = new Set(['STOP', 'STOP_LIMIT'])

function TradeCard({ id }: { id: number }) {
  const toast = useToast()
  const client = useQueryClient()
  const [mode, setMode] = useState<'order' | 'holding'>('order')
  const [symbol, setSymbol] = useState('')
  const [side, setSide] = useState('BUY')
  const [orderType, setOrderType] = useState('MARKET')
  const [timeInForce, setTimeInForce] = useState('DAY')
  const [limitPrice, setLimitPrice] = useState('')
  const [stopPrice, setStopPrice] = useState('')
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
            body: JSON.stringify({
              symbol: symbol.toUpperCase(),
              side,
              qty,
              order_type: orderType,
              time_in_force: timeInForce,
              limit_price: NEEDS_LIMIT.has(orderType) ? limitPrice : null,
              stop_price: NEEDS_STOP.has(orderType) ? stopPrice : null,
            }),
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
    onSuccess: (result) => {
      setSymbol('')
      setQty('')
      setCost('')
      setLimitPrice('')
      setStopPrice('')
      refresh()
      // A rejected order comes back 201 with its reason, so success is not the same as filled.
      const order = result as Order | undefined
      if (mode === 'order' && order?.status === 'REJECTED') {
        toast(`Order rejected: ${order.reject_reason ?? 'no reason given'}`, 'bad')
        return
      }
      toast(mode === 'order' ? 'Order placed.' : 'Holding recorded.', 'ok')
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
          <>
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
            <Field label="Type">
              <select
                className={inputClass}
                value={orderType}
                onChange={(event) => setOrderType(event.target.value)}
              >
                {ORDER_TYPES.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Time in force">
              <select
                className={inputClass}
                value={timeInForce}
                onChange={(event) => setTimeInForce(event.target.value)}
              >
                <option value="DAY">Day</option>
                <option value="GTC">Good till cancelled</option>
                <option value="IOC">Immediate or cancel</option>
              </select>
            </Field>
          </>
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
        {mode === 'order' && NEEDS_STOP.has(orderType) && (
          <Field label="Stop price">
            <input
              className={inputClass}
              value={stopPrice}
              onChange={(event) => setStopPrice(event.target.value)}
              placeholder="95.00"
              required
            />
          </Field>
        )}
        {mode === 'order' && NEEDS_LIMIT.has(orderType) && (
          <Field label="Limit price">
            <input
              className={inputClass}
              value={limitPrice}
              onChange={(event) => setLimitPrice(event.target.value)}
              placeholder="100.00"
              required
            />
          </Field>
        )}
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

      <ErrorNote error={submit.error} className="mt-3" />
    </Card>
  )
}

const OPEN_STATUSES = new Set(['ACCEPTED', 'PARTIALLY_FILLED'])

const STATUS_TONES: Record<string, 'ok' | 'bad' | 'warn' | 'muted'> = {
  FILLED: 'ok',
  REJECTED: 'bad',
  ACCEPTED: 'warn',
  PARTIALLY_FILLED: 'warn',
}

function CancelOrder({ id, order }: { id: number; order: Order }) {
  const toast = useToast()
  const cancel = useCancelOrder(id)

  if (!OPEN_STATUSES.has(order.status)) return null

  return (
    <Button
      variant="ghost"
      disabled={cancel.isPending}
      onClick={() =>
        cancel.mutate(order.id, {
          onSuccess: () => toast(`Cancelled order #${order.id}.`, 'ok'),
          onError: (error) => toast((error as Error).message, 'bad'),
        })
      }
      title="Cancel this order and release the cash it reserves"
    >
      <X className="h-4 w-4" aria-hidden />
      Cancel
    </Button>
  )
}

export function Blotter({ id }: { id: number }) {
  const orders = useOrders(id)
  const fills = useFills(id)
  const ledger = useLedger(id)
  const match = useMatchOrders(id)
  const waiting = Object.entries(match.data?.waiting ?? {})

  return (
    <div className="grid gap-4">
      <TradeCard id={id} />

      <Card
        title="Orders"
        action={
          <Button onClick={() => match.mutate()} disabled={match.isPending}>
            {match.isPending ? 'Matching…' : 'Match now'}
          </Button>
        }
      >
        {match.data && (
          <p className="mb-3 text-sm text-[var(--color-ink-muted)]">
            {match.data.filled} filled, {match.data.expired} expired, {match.data.stops} stopped
            out.
            {waiting.length > 0 && (
              <>
                {' '}
                Still resting:{' '}
                {waiting.map(([symbol, reason], index) => (
                  <span key={symbol}>
                    {index > 0 && ', '}
                    <span className="font-mono text-xs">{symbol}</span> ({reason})
                  </span>
                ))}
                .
              </>
            )}
          </p>
        )}
        <ErrorNote error={match.error} className="mb-3" />
        <QueryState query={orders} empty="No orders yet.">
          <Table
            head={['#', 'Symbol', 'Name', 'Side', 'Qty', 'Type', 'Status', 'Avg fill', 'Reason', '']}
          >
            {(orders.data ?? []).map((row) => (
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
                  <Badge tone={STATUS_TONES[row.status] ?? 'muted'}>{row.status}</Badge>
                </Cell>
                <Cell mono>{money(row.avg_fill_price)}</Cell>
                <Cell className="text-xs text-[var(--color-ink-muted)]">
                  {row.reject_reason ?? ''}
                </Cell>
                <Cell className="text-right">
                  <CancelOrder id={id} order={row} />
                </Cell>
              </Row>
            ))}
          </Table>
        </QueryState>
      </Card>

      <Card title="Fills">
        <QueryState query={fills} empty="No fills yet.">
          <Table head={['#', 'Order', 'Qty', 'Price', 'Fee', 'Slippage', 'When']}>
            {(fills.data ?? []).map((row) => (
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
        </QueryState>
      </Card>

      <Card title="Ledger">
        <QueryState query={ledger} empty="The ledger is empty.">
          <Table head={['When', 'Type', 'Amount', 'Balance after', 'Memo']}>
            {(ledger.data ?? []).map((row) => (
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
        </QueryState>
      </Card>
    </div>
  )
}
