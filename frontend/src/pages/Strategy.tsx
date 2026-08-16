import { useState } from 'react'

import { useAISummary, useApplyPreset, useLessons, usePresets, useSchedule, useStrategy } from '@/api/hooks'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { api } from '@/api/client'
import { Badge, Button, Card, Cell, Empty, Field, Row, Stat, Table, inputClass } from '@/components/ui'
import { percent, when } from '@/lib/format'


interface UniverseSpec {
  asset_classes?: string[]
  always?: string[]
  never?: string[]
  max_symbols?: number
}

function UniverseCard({ id }: { id: number }) {
  const client = useQueryClient()
  const strategy = useStrategy(id)
  const [always, setAlways] = useState<string | null>(null)
  const [never, setNever] = useState<string | null>(null)

  const spec: UniverseSpec = (strategy.data?.universe ?? {}) as UniverseSpec
  const alwaysValue = always ?? (spec.always ?? []).join(', ')
  const neverValue = never ?? (spec.never ?? []).join(', ')

  const save = useMutation({
    mutationFn: () => {
      const parse = (text: string) =>
        text
          .split(/[,\s]+/)
          .map((item) => item.trim().toUpperCase())
          .filter(Boolean)
      return api(`/portfolios/${id}/strategy`, {
        method: 'PUT',
        body: JSON.stringify({
          benchmark: strategy.data?.benchmark ?? 'SPY',
          cadence: strategy.data?.cadence ?? 'daily',
          autopilot: strategy.data?.autopilot ?? false,
          strategy: {},
          universe: {
            ...spec,
            always: parse(alwaysValue),
            never: parse(neverValue),
          },
        }),
      })
    },
    onSuccess: () => {
      setAlways(null)
      setNever(null)
      void client.invalidateQueries({ queryKey: ['strategy', id] })
    },
  })

  return (
    <Card title="Pinned symbols">
      <p className="mb-3 text-sm text-[var(--color-ink-muted)]">
        Always: names the engine considers every cycle regardless of the screen. Never: names it
        must not buy. A held position is always considered even if listed here, or it could never
        be sold. Comma or space separated; the symbol must already be tracked.
      </p>
      <div className="grid items-end gap-3 sm:grid-cols-3">
        <Field label="Always consider">
          <input
            className={inputClass}
            value={alwaysValue}
            onChange={(event) => setAlways(event.target.value)}
            placeholder="AAPL, MSFT"
          />
        </Field>
        <Field label="Never buy">
          <input
            className={inputClass}
            value={neverValue}
            onChange={(event) => setNever(event.target.value)}
            placeholder="TQQQ"
          />
        </Field>
        <Button variant="primary" disabled={save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? 'Saving…' : 'Save pins'}
        </Button>
      </div>
      {save.error && (
        <p className="mt-3 text-sm text-[var(--color-loss)]">{(save.error as Error).message}</p>
      )}
    </Card>
  )
}

export function StrategyPage({ id }: { id: number }) {
  const strategy = useStrategy(id)
  const presets = usePresets()
  const apply = useApplyPreset(id)
  const summary = useAISummary(id)
  const lessons = useLessons(id)
  const schedule = useSchedule()
  const [chosen, setChosen] = useState<string | null>(null)

  return (
    <div className="grid gap-4">
      <UniverseCard id={id} />

      <Card title="Configuration wizard">
        <p className="mb-3 text-sm text-[var(--color-ink-muted)]">
          A preset is a starting point, not a recommendation. Every value it sets is a degree of
          freedom the backtester has to count.
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          {presets.data?.map((preset) => (
            <button
              key={preset.key}
              type="button"
              onClick={() => setChosen(preset.key)}
              className={`rounded border p-3 text-left transition ${
                chosen === preset.key
                  ? 'border-[var(--color-accent)]'
                  : 'border-[var(--color-border-subtle)] hover:bg-[var(--color-surface-sunken)]'
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="font-medium">{preset.name}</span>
                <Badge>{preset.cadence}</Badge>
              </div>
              <p className="mt-1 text-sm text-[var(--color-ink-muted)]">{preset.summary}</p>
              <p className="mt-2 text-xs text-[var(--color-ink-muted)]">
                benchmark {preset.benchmark} · {preset.quality} · {preset.deliberation}
              </p>
            </button>
          ))}
        </div>
        <div className="mt-3">
          <Button
            variant="primary"
            disabled={!chosen || apply.isPending}
            onClick={() => chosen && apply.mutate(chosen)}
          >
            {apply.isPending ? 'Applying…' : 'Apply preset'}
          </Button>
        </div>
      </Card>

      {strategy.data && (
        <Card title="Strategy in force">
          <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat label="Benchmark" value={strategy.data.benchmark} />
            <Stat label="Cadence" value={strategy.data.cadence} />
            <Stat label="Autopilot" value={strategy.data.autopilot ? 'on' : 'off'} />
            <Stat
              label="Parameters"
              value={strategy.data.parameter_count}
              hint="counted against deflated Sharpe"
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {(['sizing', 'regime', 'exits', 'turnover', 'screen', 'costs'] as const).map(
              (section) => (
                <div key={section}>
                  <h3 className="mb-1 text-xs uppercase tracking-wide text-[var(--color-ink-muted)]">
                    {section}
                  </h3>
                  <dl className="space-y-0.5 text-xs">
                    {Object.entries(strategy.data[section] as Record<string, unknown>).map(
                      ([key, value]) => (
                        <div key={key} className="flex justify-between gap-2">
                          <dt className="text-[var(--color-ink-muted)]">{key}</dt>
                          <dd className="font-mono">{formatValue(value)}</dd>
                        </div>
                      ),
                    )}
                  </dl>
                </div>
              ),
            )}
          </div>
        </Card>
      )}

      <Card title="AI layer">
        {summary.data ? (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
            <Stat label="Enabled" value={summary.data.ai_enabled ? 'yes' : 'no'} />
            <Stat label="Configured" value={summary.data.configured ? 'yes' : 'no'} />
            <Stat label="Quality" value={summary.data.quality} />
            <Stat label="Cycles with AI" value={`${summary.data.cycles_with_ai}/${summary.data.cycles_sampled}`} />
            <Stat
              label="Guardrail clamps"
              value={summary.data.guardrail_clamps}
              hint="model output corrected"
            />
          </div>
        ) : (
          <Empty>No AI summary available.</Empty>
        )}
      </Card>

      <Card title="Reflection memory">
        {lessons.data?.length ? (
          <Table head={['Symbol', 'Held', 'Return', 'Alpha', 'Lesson']}>
            {lessons.data.map((row) => (
              <Row key={row.id}>
                <Cell mono>{row.symbol}</Cell>
                <Cell mono>{row.holding_days}d</Cell>
                <Cell mono>{percent(row.realized_return)}</Cell>
                <Cell mono>{percent(row.alpha)}</Cell>
                <Cell className="text-xs">{row.text}</Cell>
              </Row>
            ))}
          </Table>
        ) : (
          <Empty>No lessons yet. They are written when a position closes.</Empty>
        )}
      </Card>

      <Card title="Schedule">
        {schedule.data?.length ? (
          <Table head={['Job', 'Cron (UTC)', 'Next run']}>
            {schedule.data.map((job) => (
              <Row key={job.id}>
                <Cell mono>{job.id}</Cell>
                <Cell mono>{job.cron}</Cell>
                <Cell>{when(job.next_run)}</Cell>
              </Row>
            ))}
          </Table>
        ) : (
          <Empty>The scheduler is not running in this process.</Empty>
        )}
      </Card>
    </div>
  )
}

function formatValue(value: unknown): string {
  if (Array.isArray(value)) return value.length ? value.join(', ') : '—'
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  if (typeof value === 'number') return String(value)
  return String(value ?? '—')
}
