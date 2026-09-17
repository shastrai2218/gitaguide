/**
 * The chat screen: owns the turn list, the composer draft and online state.
 *
 * Mounted by main.jsx. Talks to the backend only through api.js, which is
 * where the wire format lives; every failure path here restores the draft so
 * a dropped connection never costs the message.
 */
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
import AuthBridge from './components/AuthBridge.jsx'
import Composer from './components/Composer.jsx'
import ConsentGate from './components/ConsentGate.jsx'
import ErrorTurn from './components/ErrorTurn.jsx'
import Header from './components/Header.jsx'
import Sidebar from './components/Sidebar.jsx'
import UserTurn from './components/UserTurn.jsx'
import WaitingIndicator from './components/WaitingIndicator.jsx'
import { CLERK_ENABLED } from './auth.js'
import {
  clearConsent,
  deleteSession,
  deriveSessionTitle,
  getSessionId,
  hasConsented,
  listSessions,
  recordConsent,
  renameSession,
  rotateSessionId,
  setActiveSessionId,
  touchSession,
} from './session.js'

/**
 * The whole application: a saved-chats sidebar, a consent gate, and one chat
 * screen.
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
  const [sessions, setSessions] = useState(listSessions)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [entries, setEntries] = useState([])
  const [busy, setBusy] = useState(false)
  // The composer's text lives here so a failed turn can put it back. Tester
  // item 9: a message that vanishes into an error is worse than the error.
  const [draft, setDraft] = useState('')
  const [online, setOnline] = useState(() => !isOffline())
  const bottomRef = useRef(null)
  const abortRef = useRef(null)

  const handleSessionsSynced = useCallback(() => setSessions(listSessions()), [])

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
      // A session with real history belongs in the sidebar even if this
      // browser's registry does not know about it yet - an older tab, or a
      // saved chat opened before the registry existed.
      const firstUserMessage = messages.find((record) => record.role === 'user')
      touchSession(sessionId, deriveSessionTitle(firstUserMessage?.content))
      setSessions(listSessions())
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
        // First successful turn is what saves a chat to the sidebar, and what
        // names it - later turns only bump its place at the top of the list.
        touchSession(sessionId, deriveSessionTitle(text))
        setSessions(listSessions())
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

  /** Abandon any in-flight turn and point the transcript at a different session. */
  function switchToSession(id) {
    abortRef.current?.abort()
    abortRef.current = null
    setBusy(false)
    setActiveSessionId(id)
    setSessionId(id)
    setEntries([])
    setDraft('')
    setSidebarOpen(false)
  }

  function handleNewSession() {
    const id = rotateSessionId()
    switchToSession(id)
    // Consent is recorded per session id server-side (see app/api/beta.py),
    // and this id has never been seen there. The tester already consented
    // once this browser session - re-showing the gate for every new chat
    // would be a broken "New chat", not a safeguard - so this carries that
    // consent over silently. If it fails, the first send still recovers: the
    // 409 handler in `send` re-shows the gate rather than losing the message.
    recordConsentOnServer(id).catch(() => {})
  }

  function handleSelectSession(id) {
    if (id === sessionId) {
      setSidebarOpen(false)
      return
    }
    switchToSession(id)
  }

  function handleDeleteSession(id) {
    // Local-only: this forgets the browser's pointer to the chat, not the
    // transcript the server holds against its id. Still worth confirming -
    // there is no "undo" for losing it out of the sidebar.
    if (!window.confirm('Delete this chat from your history? This cannot be undone here.')) {
      return
    }
    deleteSession(id)
    setSessions(listSessions())
    if (id === sessionId) handleNewSession()
  }

  function handleRenameSession(id, title) {
    renameSession(id, title)
    setSessions(listSessions())
  }

  /**
   * Edit a past message of your own: drop it and everything after it, then
   * resend the edited text as a fresh turn. Local-only, like a retried error -
   * the turns being replaced stay in the session's history on the server.
   */
  function handleEditMessage(index, newText) {
    setEntries((current) => current.slice(0, index))
    send(newText)
  }

  /** Reroll the most recent reply. Never offered on a blocked (crisis) turn. */
  function handleRegenerate() {
    const last = entries[entries.length - 1]
    const precedingUser = entries[entries.length - 2]
    if (last?.kind !== 'assistant' || precedingUser?.kind !== 'user') return
    send(precedingUser.text, { replaceLast: true })
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
    <div className="shell">
      {CLERK_ENABLED && <AuthBridge onSessionsSynced={handleSessionsSynced} />}
      <Sidebar
        sessions={sessions}
        activeSessionId={sessionId}
        open={sidebarOpen}
        onNewChat={handleNewSession}
        onSelect={handleSelectSession}
        onDelete={handleDeleteSession}
        onRename={handleRenameSession}
        onClose={() => setSidebarOpen(false)}
      />

      <div className="app">
        <Header onToggleSidebar={() => setSidebarOpen((current) => !current)} />

        <main className="column transcript">
          {entries.length === 0 && !busy && (
            <div className="empty-state-wrap">
              <svg
                className="empty-diya"
                viewBox="0 0 48 48"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                xmlns="http://www.w3.org/2000/svg"
                aria-hidden="true"
              >
                <path d="M14 34c0-2.5 4-4 10-4s10 1.5 10 4" />
                <path d="M24 30V20" />
                <path d="M24 20c-3-4-3-8 0-11 3 3 3 7 0 11z" />
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
                <UserTurn
                  key={index}
                  text={entry.text}
                  editable={!busy}
                  onEdit={(newText) => handleEditMessage(index, newText)}
                />
              )
            }
            if (entry.kind === 'assistant') {
              const isLast = index === entries.length - 1
              return (
                <AssistantTurn
                  key={index}
                  turn={entry.turn}
                  onRegenerate={isLast && !entry.turn.blocked ? handleRegenerate : undefined}
                  busy={busy}
                />
              )
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
    </div>
  )
}
