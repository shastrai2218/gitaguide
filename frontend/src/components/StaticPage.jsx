import { useEffect } from 'react'

import disclaimer from '../content/DISCLAIMER.md?raw'
import privacy from '../content/PRIVACY.md?raw'

/**
 * The Privacy Notice and the Disclaimer, served as pages of the app.
 *
 * The text is the tester pack itself: `frontend/src/content/*.md` are
 * byte-for-byte copies of `docs/tester-pack/*.md`, and `tests/test_tester_pack.py`
 * fails the build if they drift. One notice, two places it is read from, no
 * second wording that quietly diverges from what a tester agreed to.
 *
 * Rendering follows Commentary.jsx: a fixed, tiny grammar - headings,
 * paragraphs, bullet lists, bold, italic - and everything else is literal
 * text. The notices contain nothing else, and a markdown library would be a
 * dependency whose whole feature set this page must not need.
 */
const DOCS = {
  privacy: { title: 'Privacy Notice', text: privacy },
  disclaimer: { title: 'Disclaimer & Beta Terms', text: disclaimer },
}

/** Which notice a URL path names, or null for the app itself. */
export function docForPath(pathname) {
  const path = (pathname || '/').replace(/\/+$/, '') || '/'
  if (path === '/privacy') return 'privacy'
  if (path === '/disclaimer') return 'disclaimer'
  return null
}

const HEADING = /^(#{1,3})\s+(.+)$/
const BULLET = /^-\s+/

/** `*italic*` runs inside text that has already had its bold runs removed. */
function italics(text, keyPrefix) {
  return text.split(/\*([^*]+)\*/g).map((part, index) =>
    index % 2 === 1 ? <em key={`${keyPrefix}-i${index}`}>{part}</em> : part,
  )
}

/** `**bold**` first, then italics in the plain runs. Returns React children. */
function inline(text, keyPrefix) {
  return text.split(/\*\*(.+?)\*\*/g).map((part, index) =>
    index % 2 === 1 ? (
      <strong key={`${keyPrefix}-b${index}`}>{part}</strong>
    ) : (
      italics(part, `${keyPrefix}-${index}`)
    ),
  )
}

/**
 * Bullet items may wrap onto indented continuation lines in the source, so a
 * block is folded back into one string per item before rendering.
 */
function listItems(lines) {
  const items = []
  for (const line of lines) {
    if (BULLET.test(line)) items.push(line.replace(BULLET, '').trim())
    else if (items.length) items[items.length - 1] += ` ${line.trim()}`
  }
  return items
}

function renderBlock(block, index) {
  const lines = block.split('\n')

  const heading = lines[0].match(HEADING)
  if (heading) {
    const level = heading[1].length
    const text = heading[2].trim()
    // The document's own H1 is the page title, so it is rendered as such.
    if (level === 1) return <h1 key={index}>{inline(text, index)}</h1>
    return <h2 key={index}>{inline(text, index)}</h2>
  }

  if (BULLET.test(lines[0])) {
    return (
      <ul key={index}>
        {listItems(lines).map((item, itemIndex) => (
          <li key={itemIndex}>{inline(item, `${index}-${itemIndex}`)}</li>
        ))}
      </ul>
    )
  }

  return <p key={index}>{inline(lines.map((line) => line.trim()).join(' '), index)}</p>
}

export default function StaticPage({ doc }) {
  const entry = DOCS[doc]

  useEffect(() => {
    const previous = document.title
    document.title = `${entry.title} · GitaGuide`
    return () => {
      document.title = previous
    }
  }, [entry.title])

  const blocks = entry.text
    .split(/\n{2,}/)
    .map((block) => block.trim())
    .filter(Boolean)

  return (
    <div className="app">
      <header className="header">
        <div className="column header-inner">
          <h1 className="wordmark">
            Gita<em>Guide</em>
          </h1>
          <span className="tag">closed beta</span>
          <div className="header-spacer" />
          <a className="link-button" href="/">
            Back to GitaGuide
          </a>
        </div>
      </header>

      <main className="column static-page">
        <article>{blocks.map(renderBlock)}</article>
        <p className="static-page-foot">
          The same text is in the tester pack you were sent. Questions: message Kunj.
        </p>
      </main>
    </div>
  )
}
