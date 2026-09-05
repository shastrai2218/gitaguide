import { useEffect, useState } from 'react'

/**
 * Staged patience copy while a turn is in flight.
 *
 * Real latency is 3-21s and occasionally worse under NIM load. A single
 * spinner for 20 seconds reads as a hang, so the copy escalates and, at 12s,
 * tells the truth about why it is slow rather than pretending it is not.
 */
const STAGES = [
  { at: 0, text: 'Reading your message…' },
  { at: 4000, text: 'Consulting the verses…' },
  { at: 12000, text: 'The model is under load — this can take up to half a minute.' },
]

export default function WaitingIndicator() {
  const [stage, setStage] = useState(0)

  useEffect(() => {
    const timers = STAGES.slice(1).map((entry, index) =>
      setTimeout(() => setStage(index + 1), entry.at),
    )
    return () => timers.forEach(clearTimeout)
  }, [])

  return (
    <div className="turn">
      <div className="waiting" role="status" aria-live="polite">
        <span className="diya" aria-hidden="true" />
        <span>{STAGES[stage].text}</span>
      </div>
    </div>
  )
}
