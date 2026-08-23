/**
 * GitaGuide shell: chat on the left, explanation panel on the right.
 *
 * Session state is intentionally client-side and minimal - the server owns the
 * conversation (PostgreSQL) and returns the whole trace with every answer, so
 * the UI never has to reconstruct pipeline state.
 */

import { useCallback, useEffect, useState } from 'react'

import { ApiError, fetchHealth, sendMessage } from './api'
import ChatPanel from './components/ChatPanel'
import ExplanationPanel from './components/ExplanationPanel'
import type { ChatResponse, ChatTurn, HealthResponse } from './types'

const SAFETY_THRESHOLD = 0.85

function newId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

export default function App() {
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [health, setHealth] = useState<HealthResponse | null>(null)

  useEffect(() => {
    fetchHealth()
      .then(setHealth)
      .catch(() => setHealth(null))
  }, [])

  const selectedTrace: ChatResponse | null =
    turns.find((turn) => turn.id === selectedId)?.trace ?? null

  const handleSubmit = useCallback(async () => {
    const message = input.trim()
    if (!message || busy) return

    const userTurn: ChatTurn = { id: newId('user'), role: 'user', content: message }
    const pendingId = newId('assistant')
    const pendingTurn: ChatTurn = {
      id: pendingId,
      role: 'assistant',
      content: '',
      pending: true,
    }

    setTurns((previous) => [...previous, userTurn, pendingTurn])
    setInput('')
    setBusy(true)

    try {
      const response = await sendMessage(message, sessionId)
      setSessionId(response.session_id)
      setTurns((previous) =>
        previous.map((turn) =>
          turn.id === pendingId
            ? {
                id: pendingId,
                role: 'assistant',
                content: response.answer,
                crisis: response.crisis,
                trace: response,
              }
            : turn,
        ),
      )
      setSelectedId(pendingId)
    } catch (error) {
      const detail =
        error instanceof ApiError
          ? `The pipeline returned an error (${error.status}): ${error.message}`
          : 'Could not reach the API. Is it running on port 8000?'
      setTurns((previous) =>
        previous.map((turn) =>
          turn.id === pendingId
            ? { id: pendingId, role: 'assistant', content: detail, error: true }
            : turn,
        ),
      )
    } finally {
      setBusy(false)
    }
  }, [busy, input, sessionId])

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-white/10 px-4 py-3">
        <div>
          <h1 className="font-serif text-lg text-slate-100">
            GitaGuide<span className="ml-2 text-xs font-sans text-slate-500">v0.1</span>
          </h1>
          <p className="text-[11px] text-slate-500">
            Emotion-aware, explainable guidance from the Bhagavad Gita and the Arthashastra
          </p>
        </div>

        <div className="flex items-center gap-3 text-[11px] text-slate-400">
          {health ? (
            <>
              <span className="chip">
                <span
                  className={`h-1.5 w-1.5 rounded-full ${
                    health.status === 'ok' ? 'bg-emerald-400' : 'bg-red-400'
                  }`}
                />
                api
              </span>
              {health.mock_mode && <span className="chip text-amber-200">mock mode</span>}
              <span className="chip">{String(health.components.retrieval_backend ?? '')}</span>
              <span className="chip">{String(health.components.llm_provider ?? '')}</span>
            </>
          ) : (
            <span className="chip text-red-300">api unreachable</span>
          )}
        </div>
      </header>

      <main className="grid flex-1 grid-cols-1 overflow-hidden lg:grid-cols-[minmax(0,1fr)_420px]">
        <div className="min-h-0 border-white/10 lg:border-r">
          <ChatPanel
            turns={turns}
            input={input}
            busy={busy}
            selectedId={selectedId}
            onInputChange={setInput}
            onSubmit={handleSubmit}
            onSelect={setSelectedId}
          />
        </div>

        <div className="min-h-0 bg-ink-900/60">
          <ExplanationPanel trace={selectedTrace} threshold={SAFETY_THRESHOLD} />
        </div>
      </main>
    </div>
  )
}
