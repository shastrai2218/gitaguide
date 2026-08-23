/**
 * Wire types mirroring `app/api/schemas.py`.
 *
 * The explanation panel is a first-class output of this project, so the whole
 * pipeline trace is typed here rather than treated as opaque debug data.
 */

export type IntentLabel =
  | 'seek_guidance'
  | 'emotional_support'
  | 'professional_dilemma'
  | 'scripture_question'
  | 'crisis'
  | 'other'

export interface EmotionScore {
  emotion: string
  score: number
}

export interface EmotionResult {
  labels: EmotionScore[]
  intensity: number
  backend: string
  latency_ms: number
  dominant: string
  dominant_score: number
}

export interface IntentResult {
  intent: IntentLabel
  confidence: number
  scores: Record<string, number>
  rationale: string
  backend: string
}

export interface SafetyTrigger {
  layer: 'rule' | 'model' | 'output'
  category: string
  detail: string
  matched_text: string
  score: number
}

export interface SafetyVerdict {
  blocked: boolean
  triggers: SafetyTrigger[]
  response: string | null
  categories: string[]
  layers: string[]
}

export interface GraphPath {
  nodes: string[]
  relationships: string[]
  description: string
  hops: number
}

export interface VerseOut {
  id: string
  citation: string
  source: 'bhagavad_gita' | 'arthashastra'
  chapter: number
  verse: number
  book: number | null
  text_english: string
  text_sanskrit: string
  themes: string[]
  emotions_addressed: string[]
  vector_score: number
  fulltext_score: number
  graph_score: number
  intent_boost: number
  final_score: number
  channels: string[]
  graph_paths: GraphPath[]
  why: string
}

export interface RetrievalOut {
  query: string
  expanded_query: string
  emotions_used: string[]
  themes_used: string[]
  candidates_considered: number
  backend: string
  latency_ms: number
  verses: VerseOut[]
}

export interface ChatResponse {
  session_id: string
  message_id: string
  answer: string
  crisis: boolean
  emotion: EmotionResult
  intent: IntentResult
  safety: SafetyVerdict
  output_safety: SafetyVerdict
  retrieval: RetrievalOut | null
  citations: string[]
  why_this_verse: Record<string, string>
  grounded: boolean
  memory_summary: string
  stages_run: string[]
  total_latency_ms: number
  llm_provider: string
}

export interface HealthResponse {
  status: string
  app: string
  version: string
  mock_mode: boolean
  components: Record<string, unknown>
}

/** A rendered chat turn. `trace` is present on assistant turns only. */
export interface ChatTurn {
  id: string
  role: 'user' | 'assistant'
  content: string
  crisis?: boolean
  trace?: ChatResponse
  pending?: boolean
  error?: boolean
}

export const INTENT_LABELS: Record<IntentLabel, string> = {
  seek_guidance: 'Seeking guidance',
  emotional_support: 'Emotional support',
  professional_dilemma: 'Professional dilemma',
  scripture_question: 'Scripture question',
  crisis: 'Crisis',
  other: 'Other',
}

export const SOURCE_LABELS: Record<VerseOut['source'], string> = {
  bhagavad_gita: 'Bhagavad Gita',
  arthashastra: 'Arthashastra',
}
