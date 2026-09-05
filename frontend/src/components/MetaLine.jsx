/**
 * The explainability strip under each assistant turn.
 *
 * This is a research prototype: what the pipeline decided is part of the
 * output, not a debug view. Fields the backend cannot supply arrive as `null`
 * and are omitted here — never rendered as a zero, which would read as a
 * measurement.
 */
function fmt(value, digits = 2) {
  return value === null || value === undefined ? null : value.toFixed(digits)
}

/** Short stage names, so the breakdown stays one readable line. */
const STAGE_LABELS = {
  emotion: 'emo',
  intent: 'int',
  safety_input: 'gate',
  memory: 'mem',
  retrieval: 'ret',
  generation: 'gen',
  safety_output: 'out',
  persist: 'db',
}

/**
 * Where the turn's wall clock went, largest first.
 *
 * Only stages worth a reader's attention are shown: anything under 10ms is
 * noise next to a multi-second generation call, and listing it would bury the
 * one number that explains a slow turn.
 */
function stageBreakdown(stages) {
  if (!stages) return null
  const entries = Object.entries(stages)
    .filter(([, ms]) => typeof ms === 'number' && ms >= 10)
    .sort((a, b) => b[1] - a[1])
  if (!entries.length) return null
  return entries
    .map(([stage, ms]) => {
      const label = STAGE_LABELS[stage] || stage
      return ms >= 1000 ? `${label} ${(ms / 1000).toFixed(1)}s` : `${label} ${Math.round(ms)}ms`
    })
    .join(' ')
}

export default function MetaLine({ turn }) {
  const parts = []

  if (turn.emotion.label) {
    const score = fmt(turn.emotion.score)
    parts.push(`emotion ${turn.emotion.label}${score ? ` ${score}` : ''}`)
  }
  if (turn.intent.label) {
    const crisis = fmt(turn.intent.crisisScore)
    parts.push(`intent ${turn.intent.label}${crisis ? ` (crisis ${crisis})` : ''}`)
  }

  const memoryBits = []
  // "msg", not "turn": the backend counts persisted messages, so one exchange
  // advances it by two (2n-1 at exchange n). Labelling it "turn" read as though
  // memory were an exchange behind.
  if (turn.memory.turnCount !== null) memoryBits.push(`msg ${turn.memory.turnCount}`)
  if (turn.memory.recentTurnsInjected !== null) {
    memoryBits.push(`${turn.memory.recentTurnsInjected} recent`)
  }
  if (turn.memory.summaryIncluded !== null) {
    memoryBits.push(turn.memory.summaryIncluded ? 'summary in' : 'no summary')
  }
  if (memoryBits.length) parts.push(`memory ${memoryBits.join(', ')}`)

  if (turn.meta.model) parts.push(turn.meta.model)
  else if (turn.meta.provider) parts.push(turn.meta.provider)

  if (turn.meta.latencyMs !== null) {
    parts.push(`${(turn.meta.latencyMs / 1000).toFixed(1)}s`)
  }

  const breakdown = stageBreakdown(turn.meta.stageLatencyMs)
  if (breakdown) parts.push(breakdown)

  if (!parts.length) return null

  return (
    <p className="meta">
      {parts.map((part) => (
        <span key={part}>{part}</span>
      ))}
    </p>
  )
}
