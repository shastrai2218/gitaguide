/**
 * One chat turn. Assistant turns are selectable: clicking one loads its trace
 * into the explanation panel, so any past answer can be audited.
 *
 * Markdown handling is deliberately minimal (bold, headings, bullets) - the
 * project ships no markdown dependency to keep the bundle and the audit surface
 * small.
 */

import { type ChatTurn } from '../types'

interface Props {
  turn: ChatTurn
  selected: boolean
  onSelect: () => void
}

function renderInline(text: string, keyPrefix: string) {
  // Split on **bold** and leave everything else as plain text.
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return (
        <strong key={`${keyPrefix}-${index}`} className="font-semibold text-saffron-100">
          {part.slice(2, -2)}
        </strong>
      )
    }
    return <span key={`${keyPrefix}-${index}`}>{part}</span>
  })
}

function renderBody(content: string) {
  return content.split('\n').map((line, index) => {
    const key = `line-${index}`
    if (!line.trim()) return <div key={key} className="h-2" />
    if (line.startsWith('### ')) {
      return (
        <h4 key={key} className="mt-3 text-xs font-semibold uppercase tracking-wider text-slate-400">
          {line.replace('### ', '')}
        </h4>
      )
    }
    if (line.startsWith('- ')) {
      return (
        <li key={key} className="ml-4 list-disc text-sm leading-relaxed">
          {renderInline(line.slice(2), key)}
        </li>
      )
    }
    return (
      <p key={key} className="text-sm leading-relaxed">
        {renderInline(line, key)}
      </p>
    )
  })
}

export default function MessageBubble({ turn, selected, onSelect }: Props) {
  const isUser = turn.role === 'user'

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-saffron-600/25 px-4 py-2.5">
          <p className="text-sm leading-relaxed text-slate-100">{turn.content}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex justify-start">
      <button
        type="button"
        onClick={onSelect}
        className={`max-w-[92%] rounded-2xl rounded-bl-sm border px-4 py-3 text-left transition-colors ${
          turn.crisis
            ? 'border-red-500/50 bg-red-500/10'
            : selected
              ? 'border-saffron-400/50 bg-ink-800'
              : 'border-white/10 bg-ink-800/60 hover:border-white/25'
        }`}
        aria-pressed={selected}
      >
        {turn.crisis && (
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-red-300">
            Safety gate · scripture withheld
          </p>
        )}
        {turn.pending ? (
          <span className="flex items-center gap-2 text-sm text-slate-400">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-saffron-400" />
            reading the texts…
          </span>
        ) : (
          <div className={turn.error ? 'text-sm text-red-300' : 'space-y-0.5 text-slate-200'}>
            {turn.error ? turn.content : renderBody(turn.content)}
          </div>
        )}

        {turn.trace && !turn.pending && (
          <p className="mt-2 text-[10px] text-slate-500">
            {turn.trace.citations.length} citation
            {turn.trace.citations.length === 1 ? '' : 's'} ·{' '}
            {turn.trace.emotion.dominant} ({turn.trace.emotion.intensity.toFixed(2)}) ·{' '}
            {turn.trace.total_latency_ms.toFixed(0)} ms · click to inspect
          </p>
        )}
      </button>
    </div>
  )
}
