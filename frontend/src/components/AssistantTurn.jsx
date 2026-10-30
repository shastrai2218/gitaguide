import { useState } from 'react'
import { SHOW_META } from '../api.js'
import Commentary from './Commentary.jsx'
import FeedbackControl from './FeedbackControl.jsx'
import MetaLine from './MetaLine.jsx'
import VerseModal from './VerseModal.jsx'

/**
 * A completed assistant turn.
 *
 * Two renderings, chosen by `turn.blocked`:
 *
 * * **blocked** — the input safety gate short-circuited before retrieval and
 *   generation ran. The backend's own crisis text is shown in a calm panel with
 *   the helplines beneath it. There are no citations to show.
 * * **normal** — commentary against the marigold rule. Verse numbers mentioned
 *   in the text are interactive hyperlinks that open a pop-up modal with the
 *   full verse text, sanskrit, and context on click. No separate section is
 *   rendered beneath the paragraph.
 *
 * `onRegenerate` is only ever passed for the most recent turn (App.jsx decides
 * that), so at most one turn in the transcript can show the control.
 */
export default function AssistantTurn({ turn, onRegenerate, busy }) {
  const [selectedVerse, setSelectedVerse] = useState(null)

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
        <Commentary
          text={turn.reply}
          citations={turn.citations}
          onSelectVerse={setSelectedVerse}
        />
      </div>

      {selectedVerse && (
        <VerseModal
          citation={selectedVerse}
          onClose={() => setSelectedVerse(null)}
        />
      )}

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
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
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
