import { useState } from 'react'
import { SHOW_META } from '../api.js'
import Commentary from './Commentary.jsx'
import FeedbackControl from './FeedbackControl.jsx'
import MetaLine from './MetaLine.jsx'
import PracticeCard from './PracticeCard.jsx'
import VerseModal from './VerseModal.jsx'
import { downloadShareCard } from '../utils/shareCard.js'

/**
 * A completed assistant turn.
 */
export default function AssistantTurn({ turn, onRegenerate, busy }) {
  const [selectedVerse, setSelectedVerse] = useState(null)
  const [isDownloading, setIsDownloading] = useState(false)

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

  function handleDownloadCard() {
    setIsDownloading(true)
    try {
      const primaryVerse = turn.citations && turn.citations[0]
      downloadShareCard({
        replyText: turn.reply,
        verse: primaryVerse,
        emotionLabel: turn.emotion?.label,
      })
    } catch (e) {
      console.error('Failed to generate card:', e)
    } finally {
      setIsDownloading(false)
    }
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

      {/* Practical micro-reflection card tailored to the emotional state */}
      {!turn.blocked && (
        <PracticeCard emotion={turn.emotion} />
      )}

      {selectedVerse && (
        <VerseModal
          citation={selectedVerse}
          onClose={() => setSelectedVerse(null)}
        />
      )}

      {SHOW_META && <MetaLine turn={turn} />}

      <div className="turn-footer">
        <FeedbackControl messageId={turn.messageId} />

        <button
          type="button"
          className="share-card-button"
          onClick={handleDownloadCard}
          disabled={isDownloading}
          title="Download a beautiful wisdom card to share or save"
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
            <path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8" />
            <polyline points="16 6 12 2 8 6" />
            <line x1="12" y1="2" x2="12" y2="15" />
          </svg>
          {isDownloading ? 'Saving…' : 'Share Card'}
        </button>

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
