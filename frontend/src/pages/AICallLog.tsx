import { useState } from 'react'

import { useAICall, useAICalls, useAISpend, useCycles, useTimeline } from '@/api/hooks'
import { Badge, Card, Cell, Empty, Row, Stat, Table } from '@/components/ui'
import { money, num, when } from '@/lib/format'

const CLAMP_TONE: Record<string, 'bad' | 'warn'> = {
  unknown_symbol: 'bad',
  not_a_candidate: 'bad',
  protective_exit: 'bad',
  breaker: 'bad',
  weight_increase: 'warn',
  weight_range: 'warn',
  confidence_range: 'warn',
}

export function AICallLog({ portfolioId }: { portfolioId: number }) {
  const calls = useAICalls(portfolioId)
  const spend = useAISpend(portfolioId)
  const cycles = useCycles(portfolioId)
  const [callId, setCallId] = useState<number | null>(null)
  const [runId, setRunId] = useState<number | null>(null)

  const detail = useAICall(portfolioId, callId)
  const timeline = useTimeline(portfolioId, runId)

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Spend" className="lg:col-span-2">
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
          <Stat label="Calls" value={spend.data?.calls ?? 0} />
          <Stat label="Cost" value={money(spend.data?.cost_usd)} hint="information, not a limit" />
          <Stat label="Prompt tokens" value={num(spend.data?.prompt_tokens).toLocaleString()} />
          <Stat label="Completion" value={num(spend.data?.completion_tokens).toLocaleString()} />
          <Stat
            label="Cached"
            value={num(spend.data?.cached_tokens).toLocaleString()}
            hint="billed cheaper"
          />
        </div>
      </Card>

      <Card title="Decision cycles">
        {cycles.data?.length ? (
          <Table head={['Run', 'Regime', 'Entries', 'Orders', 'Status']}>
            {cycles.data.map((run) => (
              <Row key={run.id} onClick={() => setRunId(run.id)}>
                <Cell mono>#{run.id}</Cell>
                <Cell>{run.regime || '—'}</Cell>
                <Cell mono>{run.entries}</Cell>
                <Cell mono>{run.orders_placed}</Cell>
                <Cell>
                  <Badge tone={run.status === 'ok' ? 'ok' : 'bad'}>{run.status}</Badge>
                </Cell>
              </Row>
            ))}
          </Table>
        ) : (
          <Empty>No cycles have run yet.</Empty>
        )}
      </Card>

      <Card title="Model calls">
        {calls.data?.length ? (
          <Table head={['Stage', 'Model', 'Rung', 'Tokens', 'Latency', 'Cost']}>
            {calls.data.map((call) => (
              <Row key={call.id} onClick={() => setCallId(call.id)}>
                <Cell>{call.stage}</Cell>
                <Cell mono>{call.model}</Cell>
                <Cell>
                  <Badge tone={call.rung === 'json_schema' ? 'ok' : 'warn'}>{call.rung}</Badge>
                </Cell>
                <Cell mono>{call.prompt_tokens + call.completion_tokens}</Cell>
                <Cell mono>{call.latency_ms}ms</Cell>
                <Cell mono>{money(call.cost_usd)}</Cell>
              </Row>
            ))}
          </Table>
        ) : (
          <Empty>No model calls recorded. The AI layer may be disabled.</Empty>
        )}
      </Card>

      {timeline.data && (
        <Card title={`Cycle #${timeline.data.run_id} — what was asked, what survived`} className="lg:col-span-2">
          <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat label="AI used" value={timeline.data.ai_used ? 'yes' : 'no'} hint={timeline.data.ai_reason} />
            <Stat label="Strategy" value={timeline.data.strategy || '—'} />
            <Stat label="Rounds" value={timeline.data.rounds} />
            <Stat label="Brief hash" value={<span className="font-mono text-xs">{timeline.data.brief_hash || '—'}</span>} />
          </div>

          <h3 className="mb-2 text-sm font-medium">Guardrail diff</h3>
          {timeline.data.guardrail.length ? (
            <Table head={['Symbol', 'Reason', 'Asked', 'Applied']}>
              {timeline.data.guardrail.map((row, index) => (
                <Row key={`${row.symbol}-${index}`}>
                  <Cell mono>{row.symbol || '(blank)'}</Cell>
                  <Cell>
                    <Badge tone={CLAMP_TONE[row.reason] ?? 'muted'}>{row.reason}</Badge>
                  </Cell>
                  <Cell mono>{row.asked}</Cell>
                  <Cell mono>{row.applied}</Cell>
                </Row>
              ))}
            </Table>
          ) : (
            <Empty>Nothing was clamped — the model stayed inside its bounds.</Empty>
          )}

          {Object.keys(timeline.data.confidence).length > 0 && (
            <>
              <h3 className="mb-2 mt-4 text-sm font-medium">Confidence that survived</h3>
              <div className="flex flex-wrap gap-2">
                {Object.entries(timeline.data.confidence).map(([symbol, value]) => (
                  <Badge key={symbol} tone={value > 0 ? 'ok' : 'muted'}>
                    {symbol} {value.toFixed(2)}
                  </Badge>
                ))}
              </div>
            </>
          )}

          {Object.keys(timeline.data.analysts_skipped).length > 0 && (
            <p className="mt-4 text-xs text-[var(--color-ink-muted)]">
              Analysts skipped:{' '}
              {Object.entries(timeline.data.analysts_skipped)
                .map(([kind, why]) => `${kind} (${why})`)
                .join(', ')}
            </p>
          )}
        </Card>
      )}

      {detail.data && (
        <Card title={`Call #${detail.data.id} — ${detail.data.stage}`} className="lg:col-span-2">
          <div className="mb-3 grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat label="Endpoint" value={<span className="font-mono text-xs">{detail.data.endpoint}</span>} />
            <Stat label="Ladder rung" value={detail.data.rung} />
            <Stat label="Latency" value={`${detail.data.latency_ms}ms`} />
            <Stat label="When" value={<span className="text-sm">{when(detail.data.created_at)}</span>} />
          </div>
          {detail.data.error && (
            <p className="mb-3 text-sm text-[var(--color-loss)]">{detail.data.error}</p>
          )}
          <Prompt label="System prompt" body={detail.data.system_prompt} />
          <Prompt label="User prompt (the brief)" body={detail.data.user_prompt} />
          <Prompt label="Raw response" body={detail.data.response} />
        </Card>
      )}
    </div>
  )
}

function Prompt({ label, body }: { label: string; body: string }) {
  return (
    <details className="mb-2 rounded border border-[var(--color-border-subtle)]">
      <summary className="cursor-pointer px-3 py-2 text-sm">{label}</summary>
      <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words bg-[var(--color-surface-sunken)] px-3 py-2 font-mono text-xs">
        {body || '(empty)'}
      </pre>
    </details>
  )
}
