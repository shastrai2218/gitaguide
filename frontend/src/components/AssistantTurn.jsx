import { SHOW_META } from '../api.js'
import Commentary from './Commentary.jsx'
import FeedbackControl from './FeedbackControl.jsx'
import MetaLine from './MetaLine.jsx'
import VerseLeaf from './VerseLeaf.jsx'

/**
 * A completed assistant turn.
 *
 * Two renderings, chosen by `turn.blocked`:
 *
 * * **blocked** — the input safety gate short-circuited before retrieval and
 *   generation ran. The backend's own crisis text is shown in a calm panel with
 *   the helplines beneath it. There are no citations to show, and the component
 *   does not render any even if some were somehow present. It also never offers
 *   Regenerate: a crisis response is not a draft to reroll.
 * * **normal** — commentary against the marigold rule, with the cited verses
 *   inset beneath it as leaves.
 *
 * `onRegenerate` is only ever passed for the most recent turn (App.jsx decides
 * that), so at most one turn in the transcript can show the control.
 */
export default function AssistantTurn({ turn, onRegenerate, busy }) {
  if (turn.blocked) {
    return (
      <div className="turn">
        <div className="crisis">
          <Commentary text={turn.reply} />
          <p className="crisis-help">
            <strong>Tele-MANAS</strong> 14416 · <strong>KIRAN</strong>{' '}
            1800-599-0019 · <strong>Emergency</strong> 112
          </p>
        </div>
        {SHOW_META && <MetaLine turn={turn} />}
      </div>
    )
  }

  return (
    <div className="turn">
      <div className="commentary">
        <Commentary text={turn.reply} />
        {turn.citations.map((citation, index) => (
          <VerseLeaf key={`${citation.ref}-${index}`} citation={citation} />
        ))}
      </div>
      {SHOW_META && <MetaLine turn={turn} />}
      <div className="turn-footer">
        <FeedbackControl messageId={turn.messageId} />
        {onRegenerate && (
          <button
            type="button"
            className="regenerate-button"
            onClick={onRegenerate}
            disabled={busy}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M21 12a9 9 0 1 1-3-6.7" />
              <path d="M21 3v6h-6" />
            </svg>
            Regenerate
          </button>
        )}
      </div>
    </div>
  )
}
