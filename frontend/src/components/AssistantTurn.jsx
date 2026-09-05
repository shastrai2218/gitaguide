import { SHOW_META } from '../api.js'
import Commentary from './Commentary.jsx'
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
 *   does not render any even if some were somehow present.
 * * **normal** — commentary against the marigold rule, with the cited verses
 *   inset beneath it as leaves.
 */
export default function AssistantTurn({ turn }) {
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
    </div>
  )
}
