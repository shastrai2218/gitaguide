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
  if (turn.memory.turnCount !== null) memoryBits.push(`turn ${turn.memory.turnCount}`)
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

  if (!parts.length) return null

  return (
    <p className="meta">
      {parts.map((part) => (
        <span key={part}>{part}</span>
      ))}
    </p>
  )
}
