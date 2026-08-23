/** One retrieved verse: text, per-channel scores, and the graph paths. */

import { useState } from 'react'

import GraphPathView from './GraphPathView'
import { SOURCE_LABELS, type VerseOut } from '../types'

interface Props {
  verse: VerseOut
  rank: number
  cited: boolean
}

function ScoreRow({ label, value, hint }: { label: string; value: number; hint: string }) {
  return (
    <div className="flex items-center gap-2" title={hint}>
      <span className="w-16 shrink-0 text-[10px] uppercase tracking-wide text-slate-500">
        {label}
      </span>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/10">
        <div
          className="h-full rounded-full bg-slate-400"
          style={{ width: `${Math.round(Math.min(1, Math.max(0, value)) * 100)}%` }}
        />
      </div>
      <span className="w-10 shrink-0 text-right font-mono text-[10px] text-slate-400">
        {value.toFixed(2)}
      </span>
    </div>
  )
}

export default function VerseCard({ verse, rank, cited }: Props) {
  const [open, setOpen] = useState(rank === 1)

  return (
    <li
      className={`rounded-lg border p-3 transition-colors ${
        cited ? 'border-saffron-400/50 bg-saffron-400/5' : 'border-white/10 bg-white/[0.02]'
      }`}
    >
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-start justify-between gap-2 text-left"
        aria-expanded={open}
      >
        <span>
          <span className="font-mono text-sm font-semibold text-saffron-100">{verse.id}</span>
          <span className="ml-2 text-[11px] text-slate-400">{SOURCE_LABELS[verse.source]}</span>
          {cited && (
            <span className="ml-2 rounded bg-saffron-600/30 px-1.5 py-0.5 text-[10px] text-saffron-100">
              cited
            </span>
          )}
        </span>
        <span className="shrink-0 font-mono text-xs text-slate-400">
          {verse.final_score.toFixed(3)} {open ? '−' : '+'}
        </span>
      </button>

      <p className="mt-2 font-serif text-sm leading-relaxed text-slate-200">
        {verse.text_english}
      </p>

      {open && (
        <div className="mt-3 space-y-3">
          {verse.text_sanskrit && (
            <p className="text-sm leading-relaxed text-slate-400">{verse.text_sanskrit}</p>
          )}

          <div className="space-y-1">
            <ScoreRow label="vector" value={verse.vector_score} hint="MiniLM cosine similarity" />
            <ScoreRow
              label="fulltext"
              value={verse.fulltext_score}
              hint="idf-weighted lexical overlap"
            />
            <ScoreRow
              label="graph"
              value={verse.graph_score}
              hint="1-2 hop expansion over emotion and theme edges"
            />
            <ScoreRow
              label="intent"
              value={verse.intent_boost}
              hint="intent-conditioned scripture prior"
            />
          </div>

          {verse.graph_paths.length > 0 && (
            <div className="space-y-1.5">
              <p className="label">Why this verse</p>
              {verse.graph_paths.map((path, index) => (
                <GraphPathView key={index} path={path} />
              ))}
            </div>
          )}

          {verse.graph_paths.length === 0 && (
            <p className="text-[11px] text-slate-500">{verse.why}</p>
          )}

          <div className="flex flex-wrap gap-1">
            {verse.themes.map((theme) => (
              <span key={theme} className="chip">
                #{theme}
              </span>
            ))}
            {verse.emotions_addressed.map((emotion) => (
              <span key={emotion} className="chip text-rose-200">
                {emotion}
              </span>
            ))}
          </div>

          <p className="text-[10px] text-slate-500">
            channels: {verse.channels.join(' + ') || 'vector'}
          </p>
        </div>
      )}
    </li>
  )
}
