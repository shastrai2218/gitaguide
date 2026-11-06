import versesDb from '../content/verses_db.json'

/**
 * Render assistant text with interactive verse hyperlinks.
 *
 * Emits clean formatting for:
 * - Markdown bold runs (**bold**)
 * - Bullet lists (- or *)
 * - Headings (# Heading)
 * - Verse citations (BG-X.Y, AS-X.Y.Z, etc.), rendered as clickable pills
 *   that open the full verse in a pop-up modal directly in the app.
 */

const HEADING = /^#{1,6}\s+(.+)$/
const BULLET = /^[-*]\s+/

// Regex matching BG-2.47, BG 2.47, AS-1.2.3, AS 1.2.3, Gita 2.47, Bhagavad Gita 2.47, Arthashastra 1.2.3
const VERSE_MATCH_RE =
  /\b((?:BG|AS)[ -]?\d+(?:\.\d+)*|(?:Bhagavad\s+Gita|Gita)\s+\d+\.\d+|Arthashastra\s+\d+(?:\.\d+)*)\b/gi

export function normalizeVerseRef(ref) {
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

function resolveVerseCitation(normKey, rawSeg, citationsMap) {
  const fromTurn = citationsMap && citationsMap.get(normKey)
  const fromDb = versesDb && versesDb[normKey]

  const text = (fromTurn && fromTurn.text) || (fromDb && fromDb.text) || ''
  const textSanskrit =
    (fromTurn && (fromTurn.text_sanskrit || fromTurn.sanskrit)) ||
    (fromDb && (fromDb.text_sanskrit || fromDb.sanskrit)) ||
    ''
  const transliteration =
    (fromTurn && fromTurn.transliteration) ||
    (fromDb && fromDb.transliteration) ||
    ''
  const source =
    (fromTurn && fromTurn.source) ||
    (fromDb && fromDb.source) ||
    (normKey.startsWith('BG') ? 'bhagavad_gita' : 'arthashastra')
  const label =
    (fromTurn && fromTurn.label) ||
    (fromDb && fromDb.label) ||
    rawSeg ||
    normKey.replace('-', ' ')
  const why = (fromTurn && fromTurn.why) || ''

  return {
    id: normKey,
    label,
    ref: (fromTurn && fromTurn.ref) || normKey,
    source,
    text,
    text_sanskrit: textSanskrit,
    transliteration,
    why,
  }
}

function renderVerseLinks(content, keyPrefix, citationsMap, onSelectVerse) {
  if (typeof content !== 'string') return content
  const segments = content.split(VERSE_MATCH_RE)
  if (segments.length === 1) return content

  return segments.map((seg, sIdx) => {
    if (sIdx % 2 === 1) {
      const normKey = normalizeVerseRef(seg)
      const citation = resolveVerseCitation(normKey, seg, citationsMap)

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
          title={`Click to read verse ${seg} directly here`}
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
  if (!text) return null
  const parts = text.split(/\*\*([^*]+)\*\*/g)
  return parts.map((part, index) => {
    if (index % 2 === 1) {
      return (
        <strong key={`${keyPrefix}-${index}`}>
          {renderVerseLinks(part, `${keyPrefix}-b-${index}`, citationsMap, onSelectVerse)}
        </strong>
      )
    }
    return renderVerseLinks(part, `${keyPrefix}-t-${index}`, citationsMap, onSelectVerse)
  })
}

/**
 * Render one paragraph block:
 * - A block that starts with `- ` or `* ` becomes a `<ul>` of items.
 * - A block that starts with `#` becomes a heading.
 * - Everything else becomes a `<p>`.
 */
function renderBlock(block, index, citationsMap, onSelectVerse) {
  const lines = block.split('\n')
  const isList = lines.every((l) => BULLET.test(l.trim()))

  if (isList) {
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
    .map((b) => b.trim())
    .filter(Boolean)

  if (blocks.length === 0) {
    return null
  }

  return <>{blocks.map((block, i) => renderBlock(block, i, citationsMap, onSelectVerse))}</>
}
