/**
 * Render assistant text with interactive verse hyperlinks.
 *
 * Emits clean formatting for:
 * - Markdown bold runs (**bold**)
 * - Bullet lists (- or *)
 * - Headings (# Heading)
 * - Verse citations (BG-X.Y, AS-X.Y.Z, etc.), rendered as clickable pills
 *   that open the full verse in a pop-up modal.
 */

const HEADING = /^#{1,6}\s+(.+)$/
const BULLET = /^[-*]\s+/

// Regex matching BG-2.47, BG 2.47, AS-1.2.3, AS 1.2.3, Gita 2.47, Bhagavad Gita 2.47, Arthashastra 1.2.3
const VERSE_MATCH_RE =
  /\b((?:BG|AS)[ -]?\d+(?:\.\d+)*|(?:Bhagavad\s+Gita|Gita)\s+\d+\.\d+|Arthashastra\s+\d+(?:\.\d+)*)\b/gi

function normalizeVerseRef(ref) {
  if (!ref) return ''
  const trimmed = String(ref).trim()
  const gitaMatch = trimmed.match(/(?:Bhagavad\s+Gita|Gita)\s+(\d+\.\d+)/i)
  if (gitaMatch) return `BG-${gitaMatch[1]}`

  const arthaMatch = trimmed.match(/Arthashastra\s+(\d+(?:\.\d+)*)/i)
  if (arthaMatch) return `AS-${arthaMatch[1]}`

  const shortMatch = trimmed.match(/^(BG|AS)[ -]?(\d+(?:\.\d+)*)$/i)
  if (shortMatch) {
    return `${shortMatch[1].toUpperCase()}-${shortMatch[2]}`
  }
  return trimmed.toUpperCase().replace(/\s+/g, '-')
}

function renderVerseLinks(content, keyPrefix, citationsMap, onSelectVerse) {
  if (typeof content !== 'string') return content
  const segments = content.split(VERSE_MATCH_RE)
  if (segments.length === 1) return content

  return segments.map((seg, sIdx) => {
    if (sIdx % 2 === 1) {
      const normKey = normalizeVerseRef(seg)
      const citation = (citationsMap && citationsMap.get(normKey)) || {
        id: normKey,
        label: normKey.replace('-', ' '),
        ref: normKey,
        source: normKey.startsWith('BG') ? 'bhagavad_gita' : 'arthashastra',
        text: '',
        why: '',
        url: normKey.startsWith('BG')
          ? `https://vedabase.io/en/library/bg/${normKey.split('-')[1]?.replace('.', '/')}/`
          : '',
      }

      return (
        <button
          key={`${keyPrefix}-v-${sIdx}`}
          type="button"
          className="verse-link-pill"
          onClick={(e) => {
            e.preventDefault()
            e.stopPropagation()
            if (onSelectVerse) onSelectVerse(citation)
          }}
          title={`Click to read verse ${seg}`}
        >
          {seg}
        </button>
      )
    }
    return seg
  })
}

/** Split a line on `**bold**` runs and return React children with verse hyperlinks. */
function inline(text, keyPrefix, citationsMap, onSelectVerse) {
  return text.split(/\*\*(.+?)\*\*/g).map((part, index) => {
    const rendered = renderVerseLinks(
      part,
      `${keyPrefix}-${index}`,
      citationsMap,
      onSelectVerse
    )
    return index % 2 === 1 ? (
      <strong key={`${keyPrefix}-${index}`}>{rendered}</strong>
    ) : (
      rendered
    )
  })
}

function renderBlock(block, index, citationsMap, onSelectVerse) {
  const lines = block.split('\n')

  // A list: every non-empty line begins with a bullet marker.
  if (lines.every((line) => BULLET.test(line))) {
    return (
      <ul key={index}>
        {lines.map((line, lineIndex) => (
          <li key={lineIndex}>
            {inline(
              line.replace(BULLET, ''),
              `${index}-${lineIndex}`,
              citationsMap,
              onSelectVerse
            )}
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
        {body && <p>{inline(body, index, citationsMap, onSelectVerse)}</p>}
      </div>
    )
  }

  return (
    <p key={index}>
      {inline(block, index, citationsMap, onSelectVerse)}
    </p>
  )
}

export default function Commentary({
  text,
  citations = [],
  onSelectVerse,
}) {
  const citationsMap = new Map()
  if (Array.isArray(citations)) {
    for (const c of citations) {
      if (!c) continue
      if (c.id) citationsMap.set(normalizeVerseRef(c.id), c)
      if (c.ref) citationsMap.set(normalizeVerseRef(c.ref), c)
      if (c.label) citationsMap.set(normalizeVerseRef(c.label), c)
    }
  }

  const blocks = (text || '')
    .split(/\n{2,}/)
    .map((block) => block.trim())
    .filter(Boolean)

  return (
    <>
      {blocks.map((block, idx) =>
        renderBlock(block, idx, citationsMap, onSelectVerse)
      )}
    </>
  )
}
