import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

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
  profile_id: number | null
  profile_name: string
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
      <ProfilesCard />
      {portfolioId === null ? (
        <Card title="This portfolio">
          <Empty>Create a portfolio first — the model profile is chosen per portfolio.</Empty>
        </Card>
      ) : (
        <>
          <ModelsCard portfolioId={portfolioId} />
          <NotificationsCard portfolioId={portfolioId} />
        </>
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

interface ModelProfile {
  id: number
  name: string
  quick: EndpointSettings | null
  quick_fallback: EndpointSettings | null
  deep: EndpointSettings | null
  deep_fallback: EndpointSettings | null
  quality: string
  deliberation: string
  missing_credentials: string[]
  used_by: number
}

function emptyProfile(): ModelProfile {
  return {
    id: 0,
    name: '',
    quick: null,
    quick_fallback: null,
    deep: null,
    deep_fallback: null,
    quality: 'balanced',
    deliberation: 'firm_debate',
    missing_credentials: [],
    used_by: 0,
  }
}

interface NotificationState {
  configured: boolean
  masked: string
  kinds: string[]
}

function NotificationsCard({ portfolioId }: { portfolioId: number }) {
  const client = useQueryClient()
  const key = ['notifications', portfolioId]
  const state = useQuery({
    queryKey: key,
    queryFn: () => api<NotificationState>(`/portfolios/${portfolioId}/notifications`),
  })
  const [url, setUrl] = useState('')

  const invalidate = () => void client.invalidateQueries({ queryKey: key })

  const save = useMutation({
    mutationFn: () =>
      api<NotificationState>(`/portfolios/${portfolioId}/notifications`, {
        method: 'PUT',
        body: JSON.stringify({ webhook_url: url }),
      }),
    onSuccess: () => {
      setUrl('')
      invalidate()
    },
  })

  const clear = useMutation({
    mutationFn: () =>
      api<void>(`/portfolios/${portfolioId}/notifications`, { method: 'DELETE' }),
    onSuccess: invalidate,
  })

  const test = useMutation({
    mutationFn: () =>
      api<NotificationState>(`/portfolios/${portfolioId}/notifications/test`, { method: 'POST' }),
  })

  return (
    <Card title="Slack notifications">
      <p className="mb-3 text-sm text-[var(--color-ink-muted)]">
        One webhook per portfolio. The URL is stored encrypted like an API key and is never
        returned by the API. Posts on: {state.data?.kinds.join(', ') ?? '—'}.
      </p>

      <div className="grid items-end gap-3 sm:grid-cols-4">
        <div className="sm:col-span-2">
          <Field label="Incoming webhook URL">
            <input
              type="password"
              className={inputClass}
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="https://hooks.slack.com/services/..."
              autoComplete="off"
            />
          </Field>
        </div>
        <Button variant="primary" disabled={save.isPending || !url} onClick={() => save.mutate()}>
          {save.isPending ? 'Saving…' : 'Save webhook'}
        </Button>
        <div className="flex gap-2">
          <Button
            disabled={!state.data?.configured || test.isPending}
            onClick={() => test.mutate()}
          >
            {test.isPending ? 'Sending…' : 'Send test'}
          </Button>
          {state.data?.configured && (
            <Button variant="ghost" onClick={() => clear.mutate()}>
              Remove
            </Button>
          )}
        </div>
      </div>

      <p className="mt-3 text-sm">
        {state.data?.configured ? (
          <>
            Configured — <code>{state.data.masked}</code>
          </>
        ) : (
          <span className="text-[var(--color-ink-muted)]">Not configured.</span>
        )}
      </p>
      {save.error && (
        <p className="mt-2 text-sm text-[var(--color-loss)]">{(save.error as Error).message}</p>
      )}
      {test.error && (
        <p className="mt-2 text-sm text-[var(--color-loss)]">{(test.error as Error).message}</p>
      )}
      {test.isSuccess && !test.isPending && (
        <p className="mt-2 text-sm text-[var(--color-ink-muted)]">Test message sent.</p>
      )}
    </Card>
  )
}

function ProfilesCard() {
  const client = useQueryClient()
  const profiles = useQuery({
    queryKey: ['model-profiles'],
    queryFn: () => api<ModelProfile[]>('/model-profiles'),
  })
  const [draft, setDraft] = useState<ModelProfile | null>(null)

  const invalidate = () => {
    void client.invalidateQueries({ queryKey: ['model-profiles'] })
    void client.invalidateQueries({ queryKey: ['ai-models'] })
  }

  const save = useMutation({
    mutationFn: (body: ModelProfile) => {
      const payload = {
        name: body.name,
        quick: body.quick,
        quick_fallback: body.quick_fallback,
        deep: body.deep,
        deep_fallback: body.deep_fallback,
        quality: body.quality,
        deliberation: body.deliberation,
      }
      return body.id
        ? api<ModelProfile>(`/model-profiles/${body.id}`, {
            method: 'PUT',
            body: JSON.stringify(payload),
          })
        : api<ModelProfile>('/model-profiles', {
            method: 'POST',
            body: JSON.stringify(payload),
          })
    },
    onSuccess: () => {
      setDraft(null)
      invalidate()
    },
  })

  const remove = useMutation({
    mutationFn: (id: number) => api<void>(`/model-profiles/${id}`, { method: 'DELETE' }),
    onSuccess: invalidate,
  })

  return (
    <Card title="Model profiles">
      <p className="mb-3 text-sm text-[var(--color-ink-muted)]">
        Configure a model setup once and point any number of portfolios at it. Editing a profile
        changes every portfolio using it.
      </p>

      {profiles.data?.length ? (
        <Table head={['Name', 'Deep model', 'Quality', 'Strategy', 'Used by', '']}>
          {profiles.data.map((row) => (
            <Row key={row.id}>
              <Cell mono>{row.name}</Cell>
              <Cell mono>{row.deep?.model ?? '—'}</Cell>
              <Cell>{row.quality}</Cell>
              <Cell className="text-xs">{row.deliberation}</Cell>
              <Cell mono>{row.used_by}</Cell>
              <Cell>
                <div className="flex gap-1">
                  <Button variant="ghost" onClick={() => setDraft({ ...row })}>
                    Edit
                  </Button>
                  <Button variant="ghost" onClick={() => remove.mutate(row.id)}>
                    Delete
                  </Button>
                </div>
              </Cell>
            </Row>
          ))}
        </Table>
      ) : (
        <Empty>No model profiles yet.</Empty>
      )}

      {draft === null ? (
        <div className="mt-3">
          <Button variant="primary" onClick={() => setDraft(emptyProfile())}>
            New profile
          </Button>
        </div>
      ) : (
        <div className="mt-4 border-t border-[var(--color-border-subtle)] pt-4">
          <div className="mb-3 grid gap-3 sm:grid-cols-3">
            <Field label="Profile name">
              <input
                className={inputClass}
                value={draft.name}
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                placeholder="Gemini free tier"
              />
            </Field>
            <Field label="Quality">
              <select
                className={inputClass}
                value={draft.quality}
                onChange={(event) => setDraft({ ...draft, quality: event.target.value })}
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
                onChange={(event) => setDraft({ ...draft, deliberation: event.target.value })}
              >
                <option value="single_call">single_call</option>
                <option value="firm_debate">firm_debate</option>
                <option value="multi_round_debate">multi_round_debate</option>
              </select>
            </Field>
          </div>

          <div className="grid gap-4">
            {TIERS.map((tier) => (
              <EndpointForm
                key={tier}
                tier={tier}
                value={draft[tier]}
                onChange={(next) => setDraft({ ...draft, [tier]: next })}
              />
            ))}
          </div>

          {save.error && (
            <p className="mt-3 text-sm text-[var(--color-loss)]">
              {(save.error as Error).message}
            </p>
          )}
          <div className="mt-4 flex gap-3">
            <Button variant="primary" disabled={save.isPending} onClick={() => save.mutate(draft)}>
              {save.isPending ? 'Saving…' : draft.id ? 'Update profile' : 'Create profile'}
            </Button>
            <Button variant="ghost" onClick={() => setDraft(null)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </Card>
  )
}

function ModelsCard({ portfolioId }: { portfolioId: number }) {
  const client = useQueryClient()
  const key = ['ai-models', portfolioId]
  const stored = useQuery({
    queryKey: key,
    queryFn: () => api<ModelSummary>(`/portfolios/${portfolioId}/ai/models`),
  })
  const profiles = useQuery({
    queryKey: ['model-profiles'],
    queryFn: () => api<ModelProfile[]>('/model-profiles'),
  })

  const select = useMutation({
    mutationFn: (body: { profile_id: number | null; ai_enabled: boolean }) =>
      api<ModelSummary>(`/portfolios/${portfolioId}/ai/profile`, {
        method: 'PUT',
        body: JSON.stringify(body),
      }),
    onSuccess: () => void client.invalidateQueries({ queryKey: key }),
  })

  const current = stored.data
  if (!current) return <Card title="This portfolio">Loading…</Card>

  return (
    <Card title="This portfolio">
      <div className="grid items-end gap-3 sm:grid-cols-3">
        <Field label="Model profile">
          <select
            className={inputClass}
            value={current.profile_id ?? ''}
            onChange={(event) =>
              select.mutate({
                profile_id: event.target.value ? Number(event.target.value) : null,
                ai_enabled: current.ai_enabled,
              })
            }
          >
            <option value="">— none (rules only) —</option>
            {profiles.data?.map((row) => (
              <option key={row.id} value={row.id}>
                {row.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="AI enabled">
          <label className="flex items-center gap-2 py-2 text-sm">
            <input
              type="checkbox"
              checked={current.ai_enabled}
              disabled={select.isPending}
              onChange={(event) =>
                select.mutate({
                  profile_id: current.profile_id,
                  ai_enabled: event.target.checked,
                })
              }
            />
            Let the model meta-label trades
          </label>
        </Field>
        <div className="text-sm text-[var(--color-ink-muted)]">
          {current.configured
            ? `Deep: ${current.deep?.model ?? '—'} (${current.quality})`
            : 'No deep model — cycles run rules-only.'}
        </div>
      </div>

      {current.missing_credentials.length > 0 && (
        <p className="mt-3 text-sm text-[var(--color-loss)]">
          No stored credential named: {current.missing_credentials.join(', ')}
        </p>
      )}
      {select.error && (
        <p className="mt-3 text-sm text-[var(--color-loss)]">{(select.error as Error).message}</p>
      )}
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
