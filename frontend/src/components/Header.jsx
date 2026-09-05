import { MOCK } from '../api.js'

/**
 * Wordmark, beta tag, and the New session control.
 *
 * The MOCK badge is rendered from the same flag the API layer switches on, so
 * mock responses can never appear without the badge appearing with them.
 */
export default function Header({ onNewSession }) {
  return (
    <header className="header">
      <div className="column header-inner">
        <h1 className="wordmark">
          Gita<em>Guide</em>
        </h1>
        <span className="tag">closed beta</span>
        {MOCK && (
          <span className="tag tag-mock" title="No model is running; responses are canned.">
            mock
          </span>
        )}
        <div className="header-spacer" />
        <button type="button" className="link-button" onClick={onNewSession}>
          New session
        </button>
      </div>
    </header>
  )
}
