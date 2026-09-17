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
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3" /></svg>
          Helpful
        </button>
        <button
          type="button"
          className={`feedback-button${rating === 'down' ? ' is-chosen' : ''}`}
          aria-pressed={rating === 'down'}
          onClick={() => vote('down')}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17" /></svg>
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
