import { useState } from 'react'

import { sendFeedback } from '../api.js'

/**
 * Was this turn any good?
 *
 * The whole point of the closed beta is to find out, and the only instrument is
 * the tester's own verdict — so the vote is recorded the moment it is clicked,
 * before any comment is written. A flow that only saves on "submit" loses the
 * rating of everyone who does not bother to type.
 *
 * Quiet by design: two small outline buttons under the meta line. This is a
 * reflection, not a product survey, and a prominent rating widget under a
 * passage about someone's grief would be tasteless.
 */
export default function FeedbackControl({ messageId }) {
  const [rating, setRating] = useState(null)
  const [comment, setComment] = useState('')
  const [commentSent, setCommentSent] = useState(false)
  const [error, setError] = useState(null)

  if (!messageId) return null

  async function vote(value) {
    setError(null)
    const previous = rating
    setRating(value)
    try {
      await sendFeedback(messageId, value, '')
    } catch {
      setRating(previous)
      setError('Could not record that — please try again.')
    }
  }

  async function submitComment(event) {
    event.preventDefault()
    const text = comment.trim()
    if (!text || !rating) return
    setError(null)
    try {
      await sendFeedback(messageId, rating, text)
      setCommentSent(true)
    } catch {
      setError('Could not send that — please try again.')
    }
  }

  return (
    <div className="feedback">
      <div className="feedback-row">
        <button
          type="button"
          className={`feedback-button${rating === 'up' ? ' is-chosen' : ''}`}
          aria-pressed={rating === 'up'}
          onClick={() => vote('up')}
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8.5 1.5a1 1 0 00-1.8-.1L4.6 6H2a1 1 0 00-1 1v6a1 1 0 001 1h3.2a2 2 0 001.4-.6l3.3-3.3a1 1 0 00.3-.7V4.5a1 1 0 00-.3-.7L8.5 1.5zM13 6h1.5a.5.5 0 01.5.5v7a.5.5 0 01-.5.5H13V6z"/></svg>
          Helpful
        </button>
        <button
          type="button"
          className={`feedback-button${rating === 'down' ? ' is-chosen' : ''}`}
          aria-pressed={rating === 'down'}
          onClick={() => vote('down')}
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true" style={{transform: 'rotate(180deg)'}}><path d="M8.5 1.5a1 1 0 00-1.8-.1L4.6 6H2a1 1 0 00-1 1v6a1 1 0 001 1h3.2a2 2 0 001.4-.6l3.3-3.3a1 1 0 00.3-.7V4.5a1 1 0 00-.3-.7L8.5 1.5zM13 6h1.5a.5.5 0 01.5.5v7a.5.5 0 01-.5.5H13V6z"/></svg>
          Not helpful
        </button>
        {rating && !error && <span className="feedback-thanks">Thanks — recorded.</span>}
      </div>

      {rating && !commentSent && (
        <form className="feedback-comment" onSubmit={submitComment}>
          <label className="sr-only" htmlFor={`comment-${messageId}`}>
            Anything you want to add
          </label>
          <input
            id={`comment-${messageId}`}
            type="text"
            maxLength={500}
            value={comment}
            placeholder="Anything you want to add? (optional)"
            onChange={(event) => setComment(event.target.value)}
          />
          <button type="submit" className="feedback-button" disabled={!comment.trim()}>
            Send
          </button>
        </form>
      )}

      {commentSent && <p className="feedback-thanks">Thanks — recorded.</p>}
      {error && <p className="feedback-error">{error}</p>}
    </div>
  )
}
