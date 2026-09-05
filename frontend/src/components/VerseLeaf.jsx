const SOURCE_LABELS = {
  bhagavad_gita: 'Bhagavad Gita',
  arthashastra: 'Arthashastra',
}

/**
 * One cited verse, inset into the commentary as a "leaf".
 *
 * The verse is the evidence for the reflection above it, so it is set apart
 * typographically (Martel, loose leading) rather than folded into the prose.
 */
export default function VerseLeaf({ citation }) {
  const sourceLabel = SOURCE_LABELS[citation.source] || citation.source

  return (
    <figure className="leaf">
      {citation.ref && <p className="leaf-ref">{citation.ref}</p>}
      <blockquote className="leaf-text">{citation.text}</blockquote>
      {sourceLabel && <figcaption className="leaf-source">{sourceLabel}</figcaption>}
    </figure>
  )
}
