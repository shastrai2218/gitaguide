import { useState } from 'react'
import { getPracticeForEmotion } from '../content/practices.js'

export default function PracticeCard({ emotion }) {
  const [open, setOpen] = useState(false)
  const practice = getPracticeForEmotion(emotion?.label)

  return (
    <div className="practice-card-container">
      <button
        type="button"
        className={`practice-card-toggle ${open ? 'open' : ''}`}
        onClick={() => setOpen((prev) => !prev)}
        aria-expanded={open}
      >
        <span className="practice-card-sparkle">✦</span>
        <span className="practice-card-title">
          Daily Reflection & Practice · <em>{practice.title}</em> ({practice.duration})
        </span>
        <svg
          className={`practice-chevron ${open ? 'rotated' : ''}`}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {open && (
        <div className="practice-card-content">
          <p className="practice-quote">"{practice.quote}"</p>
          <ol className="practice-steps">
            {practice.steps.map((step, idx) => (
              <li key={idx} className="practice-step-item">
                {step}
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  )
}
