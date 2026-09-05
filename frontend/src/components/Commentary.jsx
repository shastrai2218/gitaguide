/**
 * Render assistant text.
 *
 * Two producers write this text and both emit a little markdown: the generator
 * ends with a "### Why this verse" heading, and the crisis response is a short
 * paragraph plus a `- **Name** - number` helpline list. This renders exactly
 * those three constructs — heading, bullet list, bold span — and treats
 * everything else as literal text.
 *
 * There is deliberately no markdown library here. Model output must not be
 * able to introduce arbitrary structure, links, or images into the page; a
 * fixed, tiny grammar is the safe version of this feature.
 */
const HEADING = /^#{1,6}\s+(.+)$/
const BULLET = /^[-*]\s+/

/** Split a line on `**bold**` runs and return React children. */
function inline(text, keyPrefix) {
  return text.split(/\*\*(.+?)\*\*/g).map((part, index) =>
    index % 2 === 1 ? (
      <strong key={`${keyPrefix}-${index}`}>{part}</strong>
    ) : (
      part
    ),
  )
}

function renderBlock(block, index) {
  const lines = block.split('\n')

  // A list: every non-empty line begins with a bullet marker.
  if (lines.every((line) => BULLET.test(line))) {
    return (
      <ul key={index}>
        {lines.map((line, lineIndex) => (
          <li key={lineIndex}>
            {inline(line.replace(BULLET, ''), `${index}-${lineIndex}`)}
          </li>
        ))}
      </ul>
    )
  }

  const heading = lines[0].match(HEADING)
  if (heading) {
    const body = lines.slice(1).join('\n').trim()
    return (
      <div key={index}>
        <h2 className="commentary-heading">{heading[1].trim()}</h2>
        {body && <p>{inline(body, index)}</p>}
      </div>
    )
  }

  return <p key={index}>{inline(block, index)}</p>
}

export default function Commentary({ text }) {
  const blocks = text
    .split(/\n{2,}/)
    .map((block) => block.trim())
    .filter(Boolean)

  return <>{blocks.map(renderBlock)}</>
}
