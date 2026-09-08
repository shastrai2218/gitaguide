const SOURCE_LABELS = {
  bhagavad_gita: 'Bhagavad Gita',
  arthashastra: 'Arthashastra',
}

/**
 * One cited verse: the reference, a link to it, and the text one tap away.
 *
 * Rewritten 9 Sep 2026 from tester feedback. It used to print the whole verse
 * inline under every answer — for one turn that was five leaves, one of them a
 * 200-word Arthashastra passage — which is both halves of what testers
 * complained about: answers too long (item 1), and *"Dont show direct slog
 * directly just give it reference link"* (item 4).
 *
 * So the verse text is still here, in full, but collapsed: the reference and
 * the path that surfaced it are what you see, and `<details>` holds the text
 * for anyone who wants to check it. Nothing is hidden from the reader, it is
 * just no longer pushed at them.
 *
 * The link goes to the authoritative edition on vedabase.io for Gita verses
 * (decision b′ — we link BBT's edition rather than storing it) and is absent
 * for the Arthashastra, which has no canonical per-sutra URL. `url` arrives as
 * a validated field from the API, never parsed out of model output, so a model
 * cannot put a link on this page.
 *
 * `why` is the graph path that retrieved the verse. It moved here when the
 * "Why this verse" section left the reply: the explanation now sits beside the
 * citation it explains, which is where it was always most useful.
 */
export default function VerseLeaf({ citation }) {
  const sourceLabel = SOURCE_LABELS[citation.source] || citation.source
  const heading = citation.label || citation.ref

  return (
    <figure className="leaf">
      <p className="leaf-ref">
        {citation.url ? (
          <a
            href={citation.url}
            target="_blank"
            rel="noopener noreferrer"
            title={`Read ${heading} on vedabase.io`}
          >
            {heading}
          </a>
        ) : (
          heading
        )}
        {sourceLabel && <span className="leaf-source"> · {sourceLabel}</span>}
      </p>

      {citation.why && <p className="leaf-why">{citation.why}</p>}

      {citation.text && (
        <details className="leaf-details">
          <summary>Read the verse</summary>
          <blockquote className="leaf-text">{citation.text}</blockquote>
        </details>
      )}
    </figure>
  )
}
