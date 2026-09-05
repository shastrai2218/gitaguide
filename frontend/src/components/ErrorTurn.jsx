import { useEffect, useState } from 'react'

/**
 * A failed turn.
 *
 * Deliberately not red: the palette has no alarm colour. The retry resends the
 * exact text the user wrote, which is kept on the turn so nothing is retyped.
 *
 * When the failure is a rate limit the wait is shown and counted down, and
 * "Try again" is disabled until it reaches zero — a retry button that is going
 * to be refused is worse than no button.
 */
export default function ErrorTurn({ message, retryAfterSeconds, onRetry, disabled }) {
  const [remaining, setRemaining] = useState(retryAfterSeconds ?? 0)

  useEffect(() => {
    setRemaining(retryAfterSeconds ?? 0)
    if (!retryAfterSeconds) return undefined
    const timer = setInterval(
      () => setRemaining((value) => (value > 1 ? value - 1 : 0)),
      1000,
    )
    return () => clearInterval(timer)
  }, [retryAfterSeconds])

  const waiting = remaining > 0

  return (
    <div className="turn">
      <div className="error-turn">
        <p>{message}</p>
        {waiting && (
          <p className="error-wait" role="status" aria-live="polite">
            You can try again in {remaining}s.
          </p>
        )}
        <button
          type="button"
          className="retry-button"
          onClick={onRetry}
          disabled={disabled || waiting}
        >
          Try again
        </button>
      </div>
    </div>
  )
}
