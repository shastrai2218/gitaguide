import { MOCK } from '../api.js'

/**
 * Wordmark, beta tag, and - on narrow viewports only - the control that opens
 * the saved-chats sidebar. "New chat" itself now lives in the sidebar, next
 * to the list it adds to, rather than duplicated up here.
 *
 * The MOCK badge is rendered from the same flag the API layer switches on, so
 * mock responses can never appear without the badge appearing with them.
 */
export default function Header({ onToggleSidebar }) {
  return (
    <header className="header">
      <div className="column header-inner">
        <button
          type="button"
          className="hamburger-button"
          onClick={onToggleSidebar}
          aria-label="Toggle saved chats"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
            <path d="M3 6h18M3 12h18M3 18h18" />
          </svg>
        </button>
        <h1 className="wordmark">
          <span className="wordmark-diya" aria-hidden="true" />
          Gita<em>Guide</em>
        </h1>
        <span className="tag">closed beta</span>
        {MOCK && (
          <span className="tag tag-mock" title="No model is running; responses are canned.">
            mock
          </span>
        )}
        <div className="header-spacer" />
      </div>
    </header>
  )
}
