import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'

import { api } from '@/api/client'
import { Badge, Button, Card, Cell, Empty, Field, Row, Stat, Table, inputClass } from '@/components/ui'
import { num, percent } from '@/lib/format'

interface Deflated {
  observed_sharpe: number
  expected_max_sharpe: number
  probability: number
  trials: number
  significant: boolean
}

interface Arm {
  label: string
  orders: number
  error: string | null
  total_return: number
  cagr: number
  sharpe: number
  sortino: number
  max_drawdown: number
  drawdown_days: number
  trades: number
  win_rate: number
  turnover: number
  deflated: Deflated
}

interface Signal {
  name: string
  observations: number
  mean_ic: number
  weight: number
  warming_up: boolean
  reliable: boolean
}

interface Report {
  start: string | null
  end: string | null
  trials: number
  verdict: string
  strategies: Arm[]
  benchmark: Arm | null
  control: Arm | null
  signals: Signal[]
  notes: string[]
}

const TODAY = new Date().toISOString().slice(0, 10)
const YEAR_AGO = new Date(Date.now() - 365 * 86_400_000).toISOString().slice(0, 10)

export function Backtest({ id }: { id: number }) {
  const [start, setStart] = useState(YEAR_AGO)
  const [end, setEnd] = useState(TODAY)

  const run = useMutation({
    mutationFn: (path: string) =>
      api<Report>(`/portfolios/${id}/backtest${path}?start=${start}&end=${end}`, {
        method: 'POST',
      }),
  })

  const report = run.data

  return (
    <div className="grid gap-4">
      <Card title="Run a backtest">
        <div className="grid items-end gap-3 sm:grid-cols-4">
          <Field label="Start">
            <input
              type="date"
              className={inputClass}
              value={start}
              onChange={(event) => setStart(event.target.value)}
            />
          </Field>
          <Field label="End">
            <input
              type="date"
              className={inputClass}
              value={end}
              onChange={(event) => setEnd(event.target.value)}
            />
          </Field>
          <Button variant="primary" disabled={run.isPending} onClick={() => run.mutate('')}>
            {run.isPending ? 'Replaying…' : 'Run replay'}
          </Button>
          <Button disabled={run.isPending} onClick={() => run.mutate('/scaling')}>
            Scaling ablation
          </Button>
        </div>
        <p className="mt-3 text-xs text-[var(--color-ink-muted)]">
          The scaling ablation runs signal alone, scaling alone, and both over identical sessions
          under one trial count — it answers which component is producing the return.
        </p>
      </Card>

      {run.error && (
        <Card title="Failed">
          <p className="text-sm text-[var(--color-loss)]">{(run.error as Error).message}</p>
        </Card>
      )}

      {report && (
        <>
          <Card title="Verdict">
            <p className="text-sm leading-relaxed">{report.verdict}</p>
            <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
              <Stat label="Window" value={`${report.start ?? '—'} → ${report.end ?? '—'}`} />
              <Stat
                label="Configurations tried"
                value={report.trials}
                hint="counted by the runner"
              />
              <Stat label="Arms" value={report.strategies.length} />
              <Stat
                label="Benchmark"
                value={
                  report.benchmark ? percent(report.benchmark.total_return) : '—'
                }
              />
            </div>
          </Card>

          <Card title="Results">
            <Table
              head={[
                'Arm',
                'Return',
                'CAGR',
                'Sharpe',
                'Deflated',
                'Max DD',
                'Trades',
                'Turnover',
              ]}
            >
              {[
                ...report.strategies,
                ...(report.benchmark ? [report.benchmark] : []),
                ...(report.control ? [report.control] : []),
              ].map((arm) => (
                <Row key={arm.label}>
                  <Cell mono>{arm.label}</Cell>
                  <Cell mono>{percent(arm.total_return)}</Cell>
                  <Cell mono>{percent(arm.cagr)}</Cell>
                  <Cell mono>{num(arm.sharpe).toFixed(2)}</Cell>
                  <Cell>
                    <Badge tone={arm.deflated.significant ? 'ok' : 'warn'}>
                      {arm.deflated.significant ? 'survives' : 'not significant'}
                    </Badge>
                  </Cell>
                  <Cell mono>{percent(arm.max_drawdown)}</Cell>
                  <Cell mono>{arm.trades}</Cell>
                  <Cell mono>{num(arm.turnover).toFixed(2)}x</Cell>
                </Row>
              ))}
            </Table>
          </Card>

          {report.signals.length > 0 && (
            <Card title="Signal quality">
              <Table head={['Signal', 'Observations', 'Mean IC', 'Influence', 'State']}>
                {report.signals.map((signal) => (
                  <Row key={signal.name}>
                    <Cell mono>{signal.name}</Cell>
                    <Cell mono>{signal.observations}</Cell>
                    <Cell mono>{num(signal.mean_ic).toFixed(4)}</Cell>
                    <Cell mono>{percent(signal.weight, 0)}</Cell>
                    <Cell>
                      <Badge
                        tone={
                          signal.warming_up ? 'muted' : signal.reliable ? 'ok' : 'bad'
                        }
                      >
                        {signal.warming_up
                          ? 'warming up'
                          : signal.reliable
                            ? 'reliable'
                            : 'de-weighted'}
                      </Badge>
                    </Cell>
                  </Row>
                ))}
              </Table>
            </Card>
          )}

          {report.notes.length > 0 && (
            <Card title="Notes">
              <ul className="space-y-2 text-sm text-[var(--color-ink-muted)]">
                {report.notes.map((note, index) => (
                  <li key={index}>{note}</li>
                ))}
              </ul>
            </Card>
          )}
        </>
      )}

      {!report && !run.isPending && (
        <Empty>No backtest has been run yet for this portfolio.</Empty>
      )}
    </div>
  )
}
