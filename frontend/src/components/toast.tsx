import { clsx } from 'clsx'
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'

type Tone = 'ok' | 'bad' | 'info'

interface Toast {
  id: number
  tone: Tone
  message: string
}

const ToastContext = createContext<(message: string, tone?: Tone) => void>(() => {})

const TONES: Record<Tone, string> = {
  ok: 'border-[var(--color-gain)] text-[var(--color-gain)]',
  bad: 'border-[var(--color-loss)] text-[var(--color-loss)]',
  info: 'border-[var(--color-border-strong)] text-[var(--color-ink)]',
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])

  const push = useCallback((message: string, tone: Tone = 'info') => {
    const id = Date.now() + Math.random()
    setToasts((current) => [...current, { id, tone, message }])
    window.setTimeout(() => setToasts((current) => current.filter((t) => t.id !== id)), 5000)
  }, [])

  const value = useMemo(() => push, [push])

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        aria-live="polite"
        className="pointer-events-none fixed inset-x-3 bottom-3 z-50 flex flex-col items-center gap-2 sm:inset-x-auto sm:right-4 sm:items-end"
      >
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={clsx(
              'pointer-events-auto w-full max-w-sm rounded-lg border bg-[var(--color-surface-overlay)] px-3 py-2 text-sm shadow-lg',
              TONES[toast.tone],
            )}
          >
            {toast.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  return useContext(ToastContext)
}
