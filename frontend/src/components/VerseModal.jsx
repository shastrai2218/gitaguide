import { useEffect } from 'react'

const SOURCE_LABELS = {
  bhagavad_gita: 'Bhagavad Gita',
  arthashastra: 'Arthashastra',
}

export default function VerseModal({ citation, onClose }) {
  useEffect(() => {
    function handleKeyDown(event) {
      if (event.key === 'Escape') {
        onClose()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  if (!citation) return null

  const sourceLabel =
    SOURCE_LABELS[citation.source] ||
    (citation.ref?.startsWith('AS') || citation.id?.startsWith('AS')
      ? 'Arthashastra'
      : 'Bhagavad Gita')
  const heading = citation.label || citation.ref || citation.id

  return (
    <div
      className="verse-modal-overlay"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="verse-modal-card"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="verse-modal-title"
      >
        <div className="verse-modal-header">
          <div className="verse-modal-title-group">
            <h3 id="verse-modal-title" className="verse-modal-title">
              {heading}
            </h3>
            {sourceLabel && (
              <span className="verse-modal-source">· {sourceLabel}</span>
            )}
          </div>
          <button
            type="button"
            className="verse-modal-close"
            onClick={onClose}
            aria-label="Close verse modal"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        <div className="verse-modal-body">
          {citation.text_sanskrit && (
            <p className="verse-modal-sanskrit">{citation.text_sanskrit}</p>
          )}

          {citation.text ? (
            <blockquote className="verse-modal-text">
              {citation.text}
            </blockquote>
          ) : (
            <p className="verse-modal-empty">
              Verse text not directly available in context for this reference.
            </p>
          )}

          {citation.why && (
            <div className="verse-modal-why-box">
              <span className="verse-modal-why-label">Why retrieved:</span>
              <p className="verse-modal-why">{citation.why}</p>
            </div>
          )}

          {citation.url && (
            <div className="verse-modal-footer-link">
              <a
                href={citation.url}
                target="_blank"
                rel="noopener noreferrer"
                className="verse-modal-external-link"
              >
                Read {heading} on vedabase.io →
              </a>
            </div>
          )}
        </div>

        <div className="verse-modal-actions">
          <button
            type="button"
            className="verse-modal-btn-close"
            onClick={onClose}
          >
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
