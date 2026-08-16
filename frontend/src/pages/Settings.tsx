import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { api } from '@/api/client'
import { useProviders } from '@/api/hooks'
import { Badge, Button, Card, Cell, Empty, Field, Row, Table, inputClass } from '@/components/ui'
import { when } from '@/lib/format'

interface Credential {
  id: number
  provider_key: string
  field: string
  masked: string
  created_at: string
}

interface EndpointSettings {
  base_url: string
  model: string
  credential: string
  label: string
  limits: { rpm: number; rpd: number; tpm: number; concurrency: number }
}

interface ModelSummary {
  quick: EndpointSettings | null
  quick_fallback: EndpointSettings | null
  deep: EndpointSettings | null
  deep_fallback: EndpointSettings | null
  ai_enabled: boolean
  quality: string
  deliberation: string
  configured: boolean
  missing_credentials: string[]
}

const TIERS = ['quick', 'quick_fallback', 'deep', 'deep_fallback'] as const
type Tier = (typeof TIERS)[number]

const TIER_HELP: Record<Tier, string> = {
  quick: 'Analyst and reflection passes. Cheap and frequent.',
  quick_fallback: 'Used when the quick tier is rate-limited or down.',
  deep: 'The decision call. Required to enable the AI.',
  deep_fallback: 'Used when the deep tier is rate-limited or down.',
}

export function Settings({ portfolioId }: { portfolioId: number | null }) {
  return (
    <div className="grid gap-4">
      <CredentialsCard />
      {portfolioId === null ? (
        <Card title="AI models">
          <Empty>Create a portfolio first — model configuration is per portfolio.</Empty>
        </Card>
      ) : (
        <ModelsCard portfolioId={portfolioId} />
      )}
      <ProvidersCard />
    </div>
  )
}

function CredentialsCard() {
  const client = useQueryClient()
  const credentials = useQuery({
    queryKey: ['credentials'],
    queryFn: () => api<Credential[]>('/credentials'),
  })

  const [providerKey, setProviderKey] = useState('')
  const [fieldName, setFieldName] = useState('api_key')
  const [secret, setSecret] = useState('')

  const invalidate = () => {
    void client.invalidateQueries({ queryKey: ['credentials'] })
    void client.invalidateQueries({ queryKey: ['providers'] })
  }

  const store = useMutation({
    mutationFn: () =>
      api<Credential>('/credentials', {
        method: 'POST',
        body: JSON.stringify({
          provider_key: providerKey.trim().toLowerCase(),
          field: fieldName.trim(),
          secret,
        }),
      }),
    onSuccess: () => {
      setSecret('')
      setProviderKey('')
      invalidate()
    },
  })

  const remove = useMutation({
    mutationFn: (id: number) => api<void>(`/credentials/${id}`, { method: 'DELETE' }),
    onSuccess: invalidate,
  })

  return (
    <Card title="Credentials">
      <p className="mb-3 text-sm text-[var(--color-ink-muted)]">
        Values are encrypted at rest and only ever shown masked. They are never returned by the
        API, written to a log, or stored on an audit row. Use the provider key for market data
        (<code>alpaca</code>, <code>fmp</code>, <code>finnhub</code>, <code>polygon</code>,{' '}
        <code>alphavantage</code>) or any name you like for a model endpoint — whatever you enter
        here is what the model configuration below refers to.
      </p>

      <form
        className="mb-4 grid items-end gap-3 sm:grid-cols-4"
        onSubmit={(event) => {
          event.preventDefault()
          store.mutate()
        }}
      >
        <Field label="Provider key">
          <input
            className={inputClass}
            value={providerKey}
            onChange={(event) => setProviderKey(event.target.value)}
            placeholder="openrouter"
            required
          />
        </Field>
        <Field label="Field">
          <input
            className={inputClass}
            value={fieldName}
            onChange={(event) => setFieldName(event.target.value)}
            required
          />
        </Field>
        <Field label="Secret">
          <input
            type="password"
            className={inputClass}
            value={secret}
            onChange={(event) => setSecret(event.target.value)}
            autoComplete="off"
            required
          />
        </Field>
        <Button type="submit" variant="primary" disabled={store.isPending}>
          {store.isPending ? 'Saving…' : 'Store key'}
        </Button>
      </form>

      {store.error && (
        <p className="mb-3 text-sm text-[var(--color-loss)]">{(store.error as Error).message}</p>
      )}

      {credentials.data?.length ? (
        <Table head={['Provider', 'Field', 'Value', 'Added', '']}>
          {credentials.data.map((row) => (
            <Row key={row.id}>
              <Cell mono>{row.provider_key}</Cell>
              <Cell>{row.field}</Cell>
              <Cell mono>{row.masked}</Cell>
              <Cell>{when(row.created_at)}</Cell>
              <Cell>
                <Button variant="ghost" onClick={() => remove.mutate(row.id)}>
                  Remove
                </Button>
              </Cell>
            </Row>
          ))}
        </Table>
      ) : (
        <Empty>No credentials stored.</Empty>
      )}
    </Card>
  )
}

function blank(): EndpointSettings {
  return {
    base_url: '',
    model: '',
    credential: '',
    label: '',
    limits: { rpm: 0, rpd: 0, tpm: 0, concurrency: 4 },
  }
}

function ModelsCard({ portfolioId }: { portfolioId: number }) {
  const client = useQueryClient()
  const key = ['ai-models', portfolioId]
  const stored = useQuery({
    queryKey: key,
    queryFn: () => api<ModelSummary>(`/portfolios/${portfolioId}/ai/models`),
  })

  const [draft, setDraft] = useState<ModelSummary | null>(null)
  useEffect(() => {
    if (stored.data) setDraft(stored.data)
  }, [stored.data])

  const save = useMutation({
    mutationFn: (body: ModelSummary) =>
      api<ModelSummary>(`/portfolios/${portfolioId}/ai/models`, {
        method: 'PUT',
        body: JSON.stringify({
          quick: body.quick,
          quick_fallback: body.quick_fallback,
          deep: body.deep,
          deep_fallback: body.deep_fallback,
          ai_enabled: body.ai_enabled,
          quality: body.quality,
          deliberation: body.deliberation,
        }),
      }),
    onSuccess: () => void client.invalidateQueries({ queryKey: key }),
  })

  if (!draft) return <Card title="AI models">Loading…</Card>

  const patch = (changes: Partial<ModelSummary>) => setDraft({ ...draft, ...changes })

  return (
    <Card title="AI models">
      <p className="mb-4 text-sm text-[var(--color-ink-muted)]">
        Two tiers per portfolio. Any OpenAI-compatible endpoint works — base URL, model id, and
        the provider key of a credential stored above. The key itself is read from the vault at
        the point of use and never stored here.
      </p>

      {draft.missing_credentials.length > 0 && (
        <p className="mb-3 text-sm text-[var(--color-loss)]">
          No stored credential named: {draft.missing_credentials.join(', ')}
        </p>
      )}

      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <Field label="Quality">
          <select
            className={inputClass}
            value={draft.quality}
            onChange={(event) => patch({ quality: event.target.value })}
          >
            <option value="economy">economy — technical only, single call</option>
            <option value="balanced">balanced — technical + news, one debate</option>
            <option value="thorough">thorough — every analyst, two rounds</option>
          </select>
        </Field>
        <Field label="Deliberation">
          <select
            className={inputClass}
            value={draft.deliberation}
            onChange={(event) => patch({ deliberation: event.target.value })}
          >
            <option value="single_call">single_call</option>
            <option value="firm_debate">firm_debate</option>
            <option value="multi_round_debate">multi_round_debate</option>
          </select>
        </Field>
        <Field label="AI enabled">
          <label className="flex items-center gap-2 py-2 text-sm">
            <input
              type="checkbox"
              checked={draft.ai_enabled}
              onChange={(event) => patch({ ai_enabled: event.target.checked })}
            />
            Let the model meta-label trades
          </label>
        </Field>
      </div>

      <div className="grid gap-4">
        {TIERS.map((tier) => (
          <EndpointForm
            key={tier}
            tier={tier}
            value={draft[tier]}
            onChange={(next) => patch({ [tier]: next } as Partial<ModelSummary>)}
          />
        ))}
      </div>

      {save.error && (
        <p className="mt-3 text-sm text-[var(--color-loss)]">{(save.error as Error).message}</p>
      )}
      <div className="mt-4 flex items-center gap-3">
        <Button variant="primary" disabled={save.isPending} onClick={() => save.mutate(draft)}>
          {save.isPending ? 'Saving…' : 'Save models'}
        </Button>
        {save.isSuccess && !save.isPending && (
          <span className="text-sm text-[var(--color-ink-muted)]">Saved.</span>
        )}
      </div>
    </Card>
  )
}

function EndpointForm({
  tier,
  value,
  onChange,
}: {
  tier: Tier
  value: EndpointSettings | null
  onChange: (next: EndpointSettings | null) => void
}) {
  const enabled = value !== null
  const current = value ?? blank()

  return (
    <div className="rounded border border-[var(--color-border-subtle)] p-3">
      <label className="flex items-center gap-2 text-sm font-medium">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(event) => onChange(event.target.checked ? blank() : null)}
        />
        {tier}
      </label>
      <p className="mt-1 text-xs text-[var(--color-ink-muted)]">{TIER_HELP[tier]}</p>

      {enabled && (
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          <Field label="Base URL">
            <input
              className={inputClass}
              value={current.base_url}
              onChange={(event) => onChange({ ...current, base_url: event.target.value })}
              placeholder="https://openrouter.ai/api/v1"
            />
          </Field>
          <Field label="Model">
            <input
              className={inputClass}
              value={current.model}
              onChange={(event) => onChange({ ...current, model: event.target.value })}
              placeholder="google/gemini-2.0-flash-exp:free"
            />
          </Field>
          <Field label="Credential">
            <input
              className={inputClass}
              value={current.credential}
              onChange={(event) => onChange({ ...current, credential: event.target.value })}
              placeholder="openrouter"
            />
          </Field>
          <Field label="Requests/min (0 = unlimited)">
            <input
              type="number"
              min={0}
              className={inputClass}
              value={current.limits.rpm}
              onChange={(event) =>
                onChange({
                  ...current,
                  limits: { ...current.limits, rpm: Number(event.target.value) },
                })
              }
            />
          </Field>
          <Field label="Requests/day (0 = unlimited)">
            <input
              type="number"
              min={0}
              className={inputClass}
              value={current.limits.rpd}
              onChange={(event) =>
                onChange({
                  ...current,
                  limits: { ...current.limits, rpd: Number(event.target.value) },
                })
              }
            />
          </Field>
          <Field label="Tokens/min (0 = unlimited)">
            <input
              type="number"
              min={0}
              className={inputClass}
              value={current.limits.tpm}
              onChange={(event) =>
                onChange({
                  ...current,
                  limits: { ...current.limits, tpm: Number(event.target.value) },
                })
              }
            />
          </Field>
        </div>
      )}
    </div>
  )
}

function ProvidersCard() {
  const providers = useProviders()

  return (
    <Card title="Provider capabilities">
      {providers.data?.length ? (
        <Table head={['Provider', 'Keyless', 'Configured', 'Capabilities', 'Missing']}>
          {providers.data.map((row) => (
            <Row key={row.provider}>
              <Cell mono>{row.provider}</Cell>
              <Cell>{row.keyless ? 'yes' : 'no'}</Cell>
              <Cell>
                <Badge tone={row.configured ? 'ok' : 'muted'}>
                  {row.configured ? 'yes' : 'no'}
                </Badge>
              </Cell>
              <Cell className="text-xs">{row.capabilities.join(', ')}</Cell>
              <Cell className="text-xs text-[var(--color-ink-muted)]">
                {row.missing_credentials.join(', ')}
              </Cell>
            </Row>
          ))}
        </Table>
      ) : (
        <Empty>No providers registered.</Empty>
      )}
    </Card>
  )
}
