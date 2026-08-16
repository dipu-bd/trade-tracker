import { useState } from 'react'

import { useChat } from '@/api/hooks'
import { Badge, Button, Card, Empty, inputClass } from '@/components/ui'

interface Turn {
  role: string
  content: string
}

export function AnalystChat({ id }: { id: number }) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [draft, setDraft] = useState('')
  const [sources, setSources] = useState<string[]>([])
  const chat = useChat(id)

  function send() {
    const message = draft.trim()
    if (!message) return

    const history = turns.slice(-6)
    setTurns((current) => [...current, { role: 'user', content: message }])
    setDraft('')

    chat.mutate(
      { message, history },
      {
        onSuccess: (reply) => {
          setSources(reply.grounded_on)
          setTurns((current) => [...current, { role: 'analyst', content: reply.reply }])
        },
        onError: (error) => {
          setTurns((current) => [
            ...current,
            { role: 'analyst', content: `Unavailable: ${(error as Error).message}` },
          ])
        },
      },
    )
  }

  return (
    <Card
      title="Analyst chat"
      action={
        <span className="text-xs text-[var(--color-ink-muted)]">
          read-only · cannot place trades
        </span>
      }
    >
      <div className="mb-3 max-h-96 space-y-3 overflow-y-auto">
        {turns.length === 0 && (
          <Empty>Ask about positions, decisions or the ledger. It answers only from stored facts.</Empty>
        )}
        {turns.map((turn, index) => (
          <div key={index} className={turn.role === 'user' ? 'text-right' : ''}>
            <div
              className={`inline-block max-w-[85%] whitespace-pre-wrap rounded px-3 py-2 text-sm ${
                turn.role === 'user'
                  ? 'bg-[var(--color-accent)] text-[var(--color-surface-base)]'
                  : 'bg-[var(--color-surface-sunken)]'
              }`}
            >
              {turn.content}
            </div>
          </div>
        ))}
        {chat.isPending && <p className="text-sm text-[var(--color-ink-muted)]">Thinking…</p>}
      </div>

      {sources.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1">
          <span className="text-xs text-[var(--color-ink-muted)]">grounded on:</span>
          {sources.map((source) => (
            <Badge key={source}>{source}</Badge>
          ))}
        </div>
      )}

      <form
        className="flex gap-2"
        onSubmit={(event) => {
          event.preventDefault()
          send()
        }}
      >
        <input
          className={inputClass}
          value={draft}
          placeholder="Why did it buy that?"
          onChange={(event) => setDraft(event.target.value)}
        />
        <Button type="submit" variant="primary" disabled={chat.isPending || !draft.trim()}>
          Ask
        </Button>
      </form>
    </Card>
  )
}
