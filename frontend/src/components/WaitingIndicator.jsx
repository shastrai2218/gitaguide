import { useEffect, useState } from 'react'

/**
 * Staged patience copy while a turn is in flight.
 *
 * Rewritten 9 Sep 2026. Tester item 9 put "taking long time for response" in
 * the same sentence as the disconnect complaint, so the copy has to keep
 * earning the wait for longer than it used to.
 *
 * The stages are pinned to measured latency, not guessed (kg_measurements.md
 * section 13): warm turns ran 11.5–37.2s, the slowest of them spending 18.1s
 * in one NIM call under upstream 503s, and a cold instance held a request for
 * 52.5s before answering.
 *
 * The old 35s stage said "Waking the server" — which was a guess, and a wrong
 * one for a warm turn that legitimately took 37s. The 30s stage now names both
 * possible causes instead of asserting the one we cannot distinguish from the
 * browser. Being honest about not knowing is better than being confidently
 * wrong at a tester who is already waiting.
 */
const STAGES = [
  { at: 0, text: 'Reading your message…' },
  { at: 4000, text: 'Consulting the verses…' },
  { at: 12000, text: 'The model is busy — this usually takes under half a minute.' },
  {
    at: 30000,
    text:
      'Still working. Either the model is under load or the server is waking up — '
      + 'free hosting sleeps when quiet, and the first message after a pause can '
      + 'take up to a minute.',
  },
  {
    at: 75000,
    text:
      'This is taking longer than usual. It may still arrive; if it does not, your '
      + 'message is kept so you can send it again.',
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
        <span className="diya-group" aria-hidden="true">
          <span className="diya-dot" />
          <span className="diya-dot" />
          <span className="diya-dot" />
        </span>
        <span>{STAGES[stage].text}</span>
      </div>
    </div>
  )
}
