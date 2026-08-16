import type { components } from './schema'

type S = components['schemas']

export type Portfolio = S['PortfolioOut']
export type PortfolioDetail = S['PortfolioDetail']
export type Position = S['PositionOut']
export type Order = S['OrderOut']
export type Fill = S['FillOut']
export type LedgerEntry = S['LedgerEntryOut']
export type Snapshot = S['SnapshotOut']
export type Reconciliation = S['ReconciliationOut']

export type StrategySummary = S['StrategySummary']
export type DecisionRun = S['DecisionRunOut']
export type DecisionRunDetail = S['DecisionRunDetail']
export type CycleTriggered = S['CycleTriggered']
export type ScheduledJob = S['ScheduledJob']

export type AICall = S['AICallOut']
export type AICallDetail = S['AICallDetail']
export type CycleTimeline = S['CycleTimeline']
export type GuardrailRow = S['GuardrailRow']
export type AISpend = S['AISpend']
export type Lesson = S['LessonOut']
export type ChatReply = S['ChatReply']

export type Instrument = S['InstrumentOut']
export type Bar = S['BarOut']
export type ProviderHealth = S['ProviderStatusOut']
export type EventRow = S['EventOut']

export interface Preset {
  key: string
  name: string
  summary: string
  benchmark: string
  cadence: string
  quality: string
  deliberation: string
  strategy: Record<string, unknown>
  universe: Record<string, unknown>
}

export interface AISummary {
  ai_enabled: boolean
  quality: string
  deliberation: string
  configured: boolean
  cycles_sampled: number
  cycles_with_ai: number
  guardrail_clamps: number
}

export interface AuthTokens {
  access_token: string
  token_type: string
}
