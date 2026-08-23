/** The chat column: transcript, sample prompts, and the composer. */

import { useEffect, useRef } from 'react'

import MessageBubble from './MessageBubble'
import type { ChatTurn } from '../types'

interface Props {
  turns: ChatTurn[]
  input: string
  busy: boolean
  selectedId: string | null
  onInputChange: (value: string) => void
  onSubmit: () => void
  onSelect: (turnId: string) => void
}

const SAMPLE_PROMPTS = [
  'My manager took credit for my project and I am furious.',
  'I lost my father last month and I cannot focus on anything.',
  'Should I leave a stable job for something uncertain?',
  'What does the Gita mean by acting without attachment to results?',
]

export default function ChatPanel({
  turns,
  input,
  busy,
  selectedId,
  onInputChange,
  onSubmit,
  onSelect,
}: Props) {
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns])

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      onSubmit()
    }
  }

  return (
    <section className="flex h-full flex-col">
      <div className="scroll-thin flex-1 space-y-3 overflow-y-auto p-4">
        {turns.length === 0 && (
          <div className="mx-auto max-w-lg space-y-4 py-10 text-center">
            <h2 className="font-serif text-xl text-slate-200">
              What are you actually carrying today?
            </h2>
            <p className="text-sm leading-relaxed text-slate-400">
              Describe it in your own words - a decision at work, a loss, a knot you cannot
              untie. The answer will quote the Gita or the Arthashastra and show you why those
              verses were chosen.
            </p>
            <div className="flex flex-col gap-2">
              {SAMPLE_PROMPTS.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  onClick={() => onInputChange(prompt)}
                  className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-left text-sm text-slate-300 hover:border-saffron-400/40"
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        )}

        {turns.map((turn) => (
          <MessageBubble
            key={turn.id}
            turn={turn}
            selected={turn.id === selectedId}
            onSelect={() => onSelect(turn.id)}
          />
        ))}
        <div ref={endRef} />
      </div>

      <form
        className="border-t border-white/10 bg-ink-900/80 p-3"
        onSubmit={(event) => {
          event.preventDefault()
          onSubmit()
        }}
      >
        <div className="flex items-end gap-2">
          <textarea
            value={input}
            onChange={(event) => onInputChange(event.target.value)}
            onKeyDown={handleKeyDown}
            rows={2}
            maxLength={4000}
            placeholder="Type what is going on. Enter to send, Shift+Enter for a new line."
            className="scroll-thin flex-1 resize-none rounded-xl border border-white/10 bg-ink-800 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-saffron-400/50 focus:outline-none"
            aria-label="Your message"
          />
          <button
            type="submit"
            disabled={busy || input.trim().length === 0}
            className="rounded-xl bg-saffron-600 px-4 py-2.5 text-sm font-semibold text-white transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy ? '…' : 'Send'}
          </button>
        </div>
        <p className="mt-2 text-[10px] leading-relaxed text-slate-500">
          GitaGuide is a research prototype, not a therapist or a doctor. If you are in crisis in
          India, Tele-MANAS is free and 24x7 on 14416.
        </p>
      </form>
    </section>
  )
}
