import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from './client'
import type {
  AICall,
  AICallDetail,
  AISpend,
  AISummary,
  Bar,
  ChatReply,
  CycleTimeline,
  CycleTriggered,
  DecisionRun,
  EventRow,
  Fill,
  Instrument,
  LedgerEntry,
  Lesson,
  Order,
  Portfolio,
  PortfolioDetail,
  Position,
  Preset,
  ProviderHealth,
  ScheduledJob,
  Snapshot,
  StrategySummary,
} from './types'

const LIVE = { refetchInterval: 15_000 }

/** Query keys shaped `[name, portfolioId]`, so they can be dropped when one is deleted. */
const PORTFOLIO_SCOPED = [
  'portfolio',
  'positions',
  'orders',
  'fills',
  'ledger',
  'snapshots',
  'strategy',
  'cycles',
  'aicalls',
  'aispend',
  'aisummary',
  'lessons',
]

export function usePortfolios() {
  return useQuery({ queryKey: ['portfolios'], queryFn: () => api<Portfolio[]>('/portfolios') })
}

export function usePortfolio(id: number) {
  return useQuery({
    queryKey: ['portfolio', id],
    queryFn: () => api<PortfolioDetail>(`/portfolios/${id}`),
    ...LIVE,
  })
}

export function usePositions(id: number) {
  return useQuery({
    queryKey: ['positions', id],
    queryFn: () => api<Position[]>(`/portfolios/${id}/positions`),
    ...LIVE,
  })
}

export function useOrders(id: number) {
  return useQuery({
    queryKey: ['orders', id],
    queryFn: () => api<Order[]>(`/portfolios/${id}/orders`),
    ...LIVE,
  })
}

export function useFills(id: number) {
  return useQuery({ queryKey: ['fills', id], queryFn: () => api<Fill[]>(`/portfolios/${id}/fills`) })
}

export function useLedger(id: number) {
  return useQuery({
    queryKey: ['ledger', id],
    queryFn: () => api<LedgerEntry[]>(`/portfolios/${id}/ledger`),
  })
}

export function useSnapshots(id: number) {
  return useQuery({
    queryKey: ['snapshots', id],
    queryFn: () => api<Snapshot[]>(`/portfolios/${id}/snapshots`),
  })
}

export function useStrategy(id: number) {
  return useQuery({
    queryKey: ['strategy', id],
    queryFn: () => api<StrategySummary>(`/portfolios/${id}/strategy`),
  })
}

export function useCycles(id: number) {
  return useQuery({
    queryKey: ['cycles', id],
    queryFn: () => api<DecisionRun[]>(`/portfolios/${id}/cycles`),
    ...LIVE,
  })
}

export function useTimeline(portfolioId: number, runId: number | null) {
  return useQuery({
    queryKey: ['timeline', portfolioId, runId],
    queryFn: () => api<CycleTimeline>(`/portfolios/${portfolioId}/cycles/${runId}/timeline`),
    enabled: runId !== null,
  })
}

export function useAICalls(id: number) {
  return useQuery({
    queryKey: ['aicalls', id],
    queryFn: () => api<AICall[]>(`/portfolios/${id}/ai/calls`),
    ...LIVE,
  })
}

export function useAICall(portfolioId: number, callId: number | null) {
  return useQuery({
    queryKey: ['aicall', portfolioId, callId],
    queryFn: () => api<AICallDetail>(`/portfolios/${portfolioId}/ai/calls/${callId}`),
    enabled: callId !== null,
  })
}

export function useAISpend(id: number) {
  return useQuery({
    queryKey: ['aispend', id],
    queryFn: () => api<AISpend>(`/portfolios/${id}/ai/spend`),
  })
}

export function useAISummary(id: number) {
  return useQuery({
    queryKey: ['aisummary', id],
    queryFn: () => api<AISummary>(`/portfolios/${id}/ai/summary`),
  })
}

export function useLessons(id: number) {
  return useQuery({
    queryKey: ['lessons', id],
    queryFn: () => api<Lesson[]>(`/portfolios/${id}/ai/lessons`),
  })
}

export function useInstruments(assetClass?: string) {
  return useQuery({
    queryKey: ['instruments', assetClass ?? 'all'],
    queryFn: () =>
      api<Instrument[]>(`/market/instruments${assetClass ? `?asset_class=${assetClass}` : ''}`),
    ...LIVE,
  })
}

export function useBars(symbol: string | null) {
  return useQuery({
    queryKey: ['bars', symbol],
    queryFn: () => api<Bar[]>(`/market/instruments/${symbol}/bars?limit=400`),
    enabled: Boolean(symbol),
  })
}

export function useProviders() {
  return useQuery({
    queryKey: ['providers'],
    queryFn: () => api<ProviderHealth[]>('/market/providers'),
    ...LIVE,
  })
}

export function useEvents(filters: { domain?: string; severity?: string } = {}) {
  const query = new URLSearchParams()
  if (filters.domain) query.set('domain', filters.domain)
  if (filters.severity) query.set('severity', filters.severity)
  return useQuery({
    queryKey: ['events', filters.domain ?? '', filters.severity ?? ''],
    queryFn: () => api<EventRow[]>(`/events?${query.toString()}`),
  })
}

export function usePresets() {
  return useQuery({ queryKey: ['presets'], queryFn: () => api<Preset[]>('/engine/presets') })
}

export function useSchedule() {
  return useQuery({ queryKey: ['schedule'], queryFn: () => api<ScheduledJob[]>('/engine/schedule') })
}

export function useRunCycle(id: number) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api<CycleTriggered>(`/portfolios/${id}/cycles`, { method: 'POST' }),
    onSuccess: () => {
      for (const key of ['cycles', 'orders', 'positions', 'portfolio', 'aicalls', 'aispend']) {
        void client.invalidateQueries({ queryKey: [key, id] })
      }
    },
  })
}

export function useApplyPreset(id: number) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (key: string) =>
      api<StrategySummary>(`/portfolios/${id}/strategy/preset/${key}`, { method: 'POST' }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['strategy', id] }),
  })
}

export function useCreatePortfolio() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: { name: string; initial_capital: string; allow_fractional: boolean }) =>
      api<Portfolio>('/portfolios', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['portfolios'] }),
  })
}

export function useDeletePortfolio() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api<void>(`/portfolios/${id}`, { method: 'DELETE' }),
    onSuccess: (_result, id) => {
      // Dropping the per-portfolio caches rather than invalidating them: a refetch of a
      // portfolio that no longer exists is a 404 rendered as an error, not an empty page.
      for (const key of PORTFOLIO_SCOPED) {
        client.removeQueries({ queryKey: [key, id] })
      }
      void client.invalidateQueries({ queryKey: ['portfolios'] })
    },
  })
}

export function useChat(id: number) {
  return useMutation({
    mutationFn: (body: { message: string; history: { role: string; content: string }[] }) =>
      api<ChatReply>(`/portfolios/${id}/chat`, { method: 'POST', body: JSON.stringify(body) }),
  })
}

export function useReconcile(id: number) {
  return useMutation({
    mutationFn: () =>
      api<{ ok: boolean; problems: string[] }>(`/portfolios/${id}/reconcile`, { method: 'POST' }),
  })
}
