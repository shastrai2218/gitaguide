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
          Helpful
        </button>
        <button
          type="button"
          className={`feedback-button${rating === 'down' ? ' is-chosen' : ''}`}
          aria-pressed={rating === 'down'}
          onClick={() => vote('down')}
        >
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
