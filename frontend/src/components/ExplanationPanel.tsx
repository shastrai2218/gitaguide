/**
 * The explanation panel - the research output of the interface.
 *
 * For the selected assistant turn it shows, in pipeline order:
 *   1. detected emotion + intensity bar (with the gate threshold marked)
 *   2. intent with its distribution over the six classes
 *   3. the safety verdict (which layers fired, which categories)
 *   4. retrieved verses with per-channel scores and their graph paths
 *   5. the stage timeline, latency and which provider answered
 */

import IntensityBar from './IntensityBar'
import VerseCard from './VerseCard'
import { INTENT_LABELS, type ChatResponse } from '../types'

interface Props {
  trace: ChatResponse | null
  threshold?: number
}

const STAGE_LABELS: Record<string, string> = {
  emotion: 'Emotion',
  intent: 'Intent',
  safety_input: 'Safety (in)',
  memory: 'Memory',
  retrieval: 'Retrieval',
  generation: 'Generation',
  safety_output: 'Safety (out)',
  persist: 'Persist',
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="card">
      <h3 className="mb-3 text-sm font-semibold text-slate-200">{title}</h3>
      {children}
    </section>
  )
}

export default function ExplanationPanel({ trace, threshold = 0.85 }: Props) {
  if (!trace) {
    return (
      <aside className="flex h-full items-center justify-center p-6 text-center">
        <p className="max-w-xs text-sm text-slate-500">
          Ask something real. This panel will show the detected emotion, the intent, the safety
          verdict, and the exact verses and graph paths the answer was built from.
        </p>
      </aside>
    )
  }

  const { emotion, intent, safety, retrieval } = trace
  const intentScores = Object.entries(intent.scores).sort((a, b) => b[1] - a[1])

  return (
    <aside className="scroll-thin h-full space-y-4 overflow-y-auto p-4">
      {/* ---- emotion ---- */}
      <Section title="1 · Emotion">
        <div className="mb-3 flex items-baseline gap-2">
          <span className="text-lg font-semibold text-saffron-100">{emotion.dominant}</span>
          <span className="font-mono text-xs text-slate-400">
            {emotion.dominant_score.toFixed(2)}
          </span>
        </div>
        <IntensityBar value={emotion.intensity} threshold={threshold} />
        <div className="mt-3 flex flex-wrap gap-1">
          {emotion.labels.map((item) => (
            <span key={item.emotion} className="chip">
              {item.emotion}
              <span className="font-mono text-slate-500">{item.score.toFixed(2)}</span>
            </span>
          ))}
        </div>
        <p className="mt-2 text-[10px] text-slate-500">
          backend: {emotion.backend} · {emotion.latency_ms.toFixed(1)} ms
        </p>
      </Section>

      {/* ---- intent ---- */}
      <Section title="2 · Intent">
        <div className="mb-2 flex items-baseline justify-between">
          <span className="text-base font-semibold text-saffron-100">
            {INTENT_LABELS[intent.intent]}
          </span>
          <span className="font-mono text-xs text-slate-400">
            {(intent.confidence * 100).toFixed(0)}%
          </span>
        </div>
        <div className="space-y-1">
          {intentScores.map(([label, score]) => (
            <div key={label} className="flex items-center gap-2">
              <span className="w-36 shrink-0 truncate text-[11px] text-slate-400">{label}</span>
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/10">
                <div
                  className={`h-full rounded-full ${
                    label === intent.intent ? 'bg-saffron-400' : 'bg-slate-500'
                  }`}
                  style={{ width: `${Math.round(score * 100)}%` }}
                />
              </div>
              <span className="w-9 text-right font-mono text-[10px] text-slate-500">
                {score.toFixed(2)}
              </span>
            </div>
          ))}
        </div>
        {intent.rationale && (
          <p className="mt-2 text-[11px] italic text-slate-500">{intent.rationale}</p>
        )}
      </Section>

      {/* ---- safety ---- */}
      <Section title="3 · Safety gate">
        <div className="flex items-center gap-2">
          <span
            className={`h-2.5 w-2.5 rounded-full ${
              safety.blocked ? 'bg-red-500' : 'bg-emerald-400'
            }`}
          />
          <span className="text-sm text-slate-200">
            {safety.blocked ? 'Blocked - pipeline short-circuited' : 'Passed'}
          </span>
        </div>
        {safety.triggers.length > 0 && (
          <ul className="mt-2 space-y-1.5">
            {safety.triggers.map((trigger, index) => (
              <li key={index} className="rounded border border-red-500/30 bg-red-500/5 p-2">
                <p className="text-[11px] text-red-200">
                  <span className="font-semibold uppercase">{trigger.layer}</span> ·{' '}
                  {trigger.category}
                </p>
                <p className="mt-0.5 text-[11px] text-slate-400">{trigger.detail}</p>
                {trigger.matched_text && (
                  <p className="mt-0.5 font-mono text-[10px] text-slate-500">
                    matched: “{trigger.matched_text}”
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
        {trace.output_safety.blocked && (
          <p className="mt-2 text-[11px] text-amber-200">
            The output check also fired: the generated answer was replaced.
          </p>
        )}
      </Section>

      {/* ---- retrieval ---- */}
      <Section title="4 · Retrieved verses">
        {retrieval ? (
          <>
            <p className="mb-2 text-[11px] text-slate-500">
              {retrieval.candidates_considered} candidates → top {retrieval.verses.length} ·{' '}
              {retrieval.backend} · {retrieval.latency_ms.toFixed(1)} ms
            </p>
            {retrieval.emotions_used.length > 0 && (
              <p className="mb-2 text-[11px] text-slate-400">
                graph seeded by: {retrieval.emotions_used.join(', ')}
              </p>
            )}
            <ul className="space-y-2">
              {retrieval.verses.map((verse, index) => (
                <VerseCard
                  key={verse.id}
                  verse={verse}
                  rank={index + 1}
                  cited={trace.citations.includes(verse.id)}
                />
              ))}
            </ul>
          </>
        ) : (
          <p className="text-sm text-slate-400">
            Retrieval did not run: the safety gate short-circuited this turn, by design.
          </p>
        )}
      </Section>

      {/* ---- pipeline ---- */}
      <Section title="5 · Pipeline">
        <ol className="flex flex-wrap gap-1">
          {trace.stages_run.map((stage) => (
            <li key={stage} className="chip">
              {STAGE_LABELS[stage] ?? stage}
            </li>
          ))}
        </ol>
        <dl className="mt-3 space-y-1 text-[11px] text-slate-400">
          <div className="flex justify-between">
            <dt>total latency</dt>
            <dd className="font-mono">{trace.total_latency_ms.toFixed(1)} ms</dd>
          </div>
          <div className="flex justify-between">
            <dt>llm provider</dt>
            <dd className="font-mono">{trace.llm_provider || '-'}</dd>
          </div>
          <div className="flex justify-between">
            <dt>grounded</dt>
            <dd className={trace.grounded ? 'text-emerald-300' : 'text-amber-300'}>
              {trace.grounded ? 'yes' : 'no'}
            </dd>
          </div>
          <div className="flex justify-between">
            <dt>citations</dt>
            <dd className="font-mono">{trace.citations.join(', ') || '-'}</dd>
          </div>
        </dl>
        {trace.memory_summary && (
          <div className="mt-3">
            <p className="label">Rolling summary in prompt</p>
            <p className="mt-1 text-[11px] leading-relaxed text-slate-400">
              {trace.memory_summary}
            </p>
          </div>
        )}
      </Section>
    </aside>
  )
}
