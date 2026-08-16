import { useQuery } from '@tanstack/react-query'

import { api } from '@/api/client'
import { useProviders } from '@/api/hooks'
import { Badge, Card, Cell, Empty, Row, Table } from '@/components/ui'
import { when } from '@/lib/format'

interface Credential {
  id: number
  provider_key: string
  field: string
  masked: string
  created_at: string
}

export function Settings() {
  const credentials = useQuery({
    queryKey: ['credentials'],
    queryFn: () => api<Credential[]>('/credentials'),
  })
  const providers = useProviders()

  return (
    <div className="grid gap-4">
      <Card title="Credentials">
        <p className="mb-3 text-sm text-[var(--color-ink-muted)]">
          Values are encrypted at rest and only ever shown masked. They are never returned by the
          API, written to a log, or stored on an audit row.
        </p>
        {credentials.data?.length ? (
          <Table head={['Provider', 'Field', 'Value', 'Added']}>
            {credentials.data.map((row) => (
              <Row key={row.id}>
                <Cell mono>{row.provider_key}</Cell>
                <Cell>{row.field}</Cell>
                <Cell mono>{row.masked}</Cell>
                <Cell>{when(row.created_at)}</Cell>
              </Row>
            ))}
          </Table>
        ) : (
          <Empty>No credentials stored.</Empty>
        )}
      </Card>

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
    </div>
  )
}
