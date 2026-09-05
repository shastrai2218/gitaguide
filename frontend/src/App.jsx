import { useCallback, useEffect, useRef, useState } from 'react'

import {
  ApiError,
  CONSENT_REQUIRED,
  fetchSessionMessages,
  recordConsentOnServer,
  sendMessage,
} from './api.js'
import AssistantTurn from './components/AssistantTurn.jsx'
import Composer from './components/Composer.jsx'
import ConsentGate from './components/ConsentGate.jsx'
import ErrorTurn from './components/ErrorTurn.jsx'
import Header from './components/Header.jsx'
import WaitingIndicator from './components/WaitingIndicator.jsx'
import {
  clearConsent,
  getSessionId,
  hasConsented,
  recordConsent,
  rotateSessionId,
} from './session.js'

/**
 * The whole application: a consent gate and one chat screen.
 *
 * The transcript is a flat list of entries, each `{kind, ...}` where kind is
 * `user`, `assistant`, or `error`. An error entry keeps the text that produced
 * it so "Try again" can resend it without the user retyping anything.
 */
/** Project one persisted message onto a transcript entry. */
function restoredEntry(record) {
  if (record.role === 'user') {
    return { kind: 'user', text: record.content ?? '' }
  }
  return {
    kind: 'assistant',
    turn: {
      messageId: record.id ?? null,
      reply: record.content ?? '',
      blocked: record.blocked === true,
      emotion: { label: null, score: null },
      intent: { label: null, crisisScore: null },
      citations: [],
      memory: { turnCount: null, summaryIncluded: null, recentTurnsInjected: null },
      meta: { provider: null, model: null, latencyMs: null, stageLatencyMs: {} },
    },
  }
}

export default function App() {
  const [consented, setConsented] = useState(hasConsented)
  const [sessionId, setSessionId] = useState(getSessionId)
  const [entries, setEntries] = useState([])
  const [busy, setBusy] = useState(false)
  const bottomRef = useRef(null)
  const abortRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [entries, busy])

  // Abandon an in-flight request if the component goes away.
  useEffect(() => () => abortRef.current?.abort(), [])

  // Restore an earlier conversation on reload. The history endpoint is
  // optional: `fetchSessionMessages` returns null when it is not implemented,
  // and the transcript simply starts empty. Restored turns carry no trace, so
  // their meta line is absent rather than fabricated.
  useEffect(() => {
    let cancelled = false
    if (!consented) return undefined

    fetchSessionMessages(sessionId).then((messages) => {
      if (cancelled || !messages?.length) return
      setEntries(messages.map(restoredEntry))
    })

    return () => {
      cancelled = true
    }
  }, [consented, sessionId])

  const send = useCallback(
    async (text, { replaceLast = false } = {}) => {
      setEntries((current) => {
        const base = replaceLast ? current.slice(0, -1) : [...current, { kind: 'user', text }]
        return base
      })
      setBusy(true)

      const controller = new AbortController()
      abortRef.current = controller

      try {
        const turn = await sendMessage(sessionId, text, controller.signal)
        setEntries((current) => [...current, { kind: 'assistant', turn }])
      } catch (error) {
        if (error?.name === 'AbortError') return

        // The server is the authority on consent. If it says this session has
        // none - cleared storage, a second browser, a rotated session - show
        // the gate again rather than leaving the tester at a dead end.
        if (error instanceof ApiError && error.code === CONSENT_REQUIRED) {
          clearConsent()
          setConsented(false)
          return
        }

        const message =
          error instanceof ApiError
            ? error.message
            : 'Something went wrong on the way to GitaGuide.'
        const retryAfterSeconds =
          error instanceof ApiError ? error.retryAfterSeconds : null
        setEntries((current) => [
          ...current,
          { kind: 'error', text, message, retryAfterSeconds },
        ])
      } finally {
        abortRef.current = null
        setBusy(false)
      }
    },
    [sessionId],
  )

  const [consentError, setConsentError] = useState(null)

  function handleNewSession() {
    abortRef.current?.abort()
    abortRef.current = null
    setBusy(false)
    setSessionId(rotateSessionId())
    setEntries([])
  }

  async function handleAccept() {
    setConsentError(null)
    try {
      await recordConsentOnServer(sessionId)
    } catch (error) {
      // Fail loudly: if consent could not be recorded, do not let the tester
      // through on a browser flag the server will refuse anyway.
      setConsentError(
        error instanceof ApiError
          ? error.message
          : 'Could not record your consent. Please try again.',
      )
      return
    }
    recordConsent()
    setConsented(true)
  }

  if (!consented) {
    return <ConsentGate onAccept={handleAccept} error={consentError} />
  }

  return (
    <div className="app">
      <Header onNewSession={handleNewSession} />

      <main className="column transcript">
        {entries.length === 0 && !busy && (
          <>
            <p className="empty-state">What is weighing on you today?</p>
            <p className="empty-hint">
              Write it plainly. GitaGuide will answer with a reflection and the
              verses it drew on.
            </p>
          </>
        )}

        {entries.map((entry, index) => {
          if (entry.kind === 'user') {
            return (
              <div className="turn turn-user" key={index}>
                <div className="bubble">{entry.text}</div>
              </div>
            )
          }
          if (entry.kind === 'assistant') {
            return <AssistantTurn key={index} turn={entry.turn} />
          }
          return (
            <ErrorTurn
              key={index}
              message={entry.message}
              retryAfterSeconds={entry.retryAfterSeconds}
              disabled={busy}
              onRetry={() => send(entry.text, { replaceLast: true })}
            />
          )
        })}

        {busy && <WaitingIndicator />}
        <div ref={bottomRef} />
      </main>

      <Composer onSend={send} busy={busy} />
    </div>
  )
}
