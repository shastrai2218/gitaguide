import { useCallback, useEffect, useRef, useState } from 'react'

import {
  ApiError,
  CONSENT_REQUIRED,
  fetchSessionMessages,
  isOffline,
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
  // The composer's text lives here so a failed turn can put it back. Tester
  // item 9: a message that vanishes into an error is worse than the error.
  const [draft, setDraft] = useState('')
  const [online, setOnline] = useState(() => !isOffline())
  const bottomRef = useRef(null)
  const abortRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [entries, busy])

  // Abandon an in-flight request if the component goes away.
  useEffect(() => () => abortRef.current?.abort(), [])

  // Connection state, so the composer can say so before a send fails. The
  // events are the only reliable signal: `navigator.onLine` is a snapshot and
  // polling it would still miss the moment it changed.
  useEffect(() => {
    const goOnline = () => setOnline(true)
    const goOffline = () => setOnline(false)
    window.addEventListener('online', goOnline)
    window.addEventListener('offline', goOffline)
    return () => {
      window.removeEventListener('online', goOnline)
      window.removeEventListener('offline', goOffline)
    }
  }, [])

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
        // the gate again rather than leaving the tester at a dead end. The
        // draft goes back so the message survives being asked to consent.
        if (error instanceof ApiError && error.code === CONSENT_REQUIRED) {
          setDraft(text)
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
        // Put the message back in the composer. The error turn keeps its own
        // copy and its own Retry, so there are two ways forward: resend as
        // written, or edit it first.
        setDraft(text)
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

  /**
   * Send from the composer.
   *
   * If the last thing in the transcript is a failure for this exact text, this
   * is a retry rather than a new message, so it replaces the error entry
   * instead of stacking a second identical user bubble underneath it.
   */
  const sendFromComposer = useCallback(
    (text) => {
      setDraft('')
      const last = entries[entries.length - 1]
      const isRetryOfSameText = last?.kind === 'error' && last.text === text
      send(text, { replaceLast: isRetryOfSameText })
    },
    [entries, send],
  )

  const [consentError, setConsentError] = useState(null)

  function handleNewSession() {
    abortRef.current?.abort()
    abortRef.current = null
    setBusy(false)
    setSessionId(rotateSessionId())
    setEntries([])
    setDraft('')
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
          <div className="empty-state-wrap">
            <svg className="empty-diya empty-diya-glow" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
              <ellipse cx="32" cy="52" rx="14" ry="4" fill="#e9a23b" opacity="0.25"/>
              <path d="M26 52c0-4 2-8 6-20 4 12 6 16 6 20a6 6 0 01-12 0z" fill="#e9a23b" opacity="0.8"/>
              <path d="M30 52c0-2 1-5 2-10 1 5 2 8 2 10a2 2 0 01-4 0z" fill="#f0b85a"/>
              <circle cx="32" cy="30" r="2" fill="#f0b85a" opacity="0.7"/>
              <path d="M32 8v18" stroke="#e9a23b" strokeWidth="1" strokeLinecap="round" opacity="0.4"/>
            </svg>
            <p className="empty-state">What is weighing on you today?</p>
            <p className="empty-hint">
              Write it plainly. GitaGuide will answer with a reflection and the
              verses it drew on.
            </p>
            <div className="suggestion-chips">
              <button type="button" className="suggestion-chip" onClick={() => setDraft('I feel anxious about the future')}>
                I feel anxious about the future
              </button>
              <button type="button" className="suggestion-chip" onClick={() => setDraft('I am struggling with a difficult decision')}>
                I'm struggling with a decision
              </button>
              <button type="button" className="suggestion-chip" onClick={() => setDraft('I feel lost and don\'t know my purpose')}>
                I feel lost and purposeless
              </button>
              <button type="button" className="suggestion-chip" onClick={() => setDraft('I am dealing with anger I cannot control')}>
                I can't control my anger
              </button>
            </div>
          </div>
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

      <Composer
        value={draft}
        onChange={setDraft}
        onSend={sendFromComposer}
        busy={busy}
        online={online}
      />
    </div>
  )
}
