/**
 * A failed turn.
 *
 * Deliberately not red: the palette has no alarm colour. The retry resends the
 * exact text the user wrote, which is kept on the turn so nothing is retyped.
 */
export default function ErrorTurn({ message, onRetry, disabled }) {
  return (
    <div className="turn">
      <div className="error-turn">
        <p>{message}</p>
        <button
          type="button"
          className="retry-button"
          onClick={onRetry}
          disabled={disabled}
        >
          Try again
        </button>
      </div>
    </div>
  )
}
