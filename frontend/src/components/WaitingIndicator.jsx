import { useEffect, useState } from 'react'

/**
 * Staged patience copy while a turn is in flight.
 *
 * Real latency is 3-21s and occasionally worse under NIM load. A single
 * spinner for 20 seconds reads as a hang, so the copy escalates and, at 12s,
 * tells the truth about why it is slow rather than pretending it is not.
 *
 * The 35s stage is for a different cause entirely. The API runs on Render's
 * free plan, which sleeps after ~15 minutes idle; the next request then waits
 * 30-60s for the instance to wake and load both ONNX models. Past 35s that is
 * overwhelmingly what is happening, so the copy names it. "Free hosting sleeps
 * when quiet" is a thing a tester can understand and forgive; an unexplained
 * minute of silence is not.
 */
const STAGES = [
  { at: 0, text: 'Reading your message…' },
  { at: 4000, text: 'Consulting the verses…' },
  { at: 12000, text: 'The model is under load — this can take up to half a minute.' },
  {
    at: 35000,
    text: 'Waking the server — free hosting sleeps when quiet. This can take up to a minute.',
  },
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
