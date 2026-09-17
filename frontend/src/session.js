/**
 * Client-side session identity, the sidebar's session registry, and the
 * consent flag.
 *
 * The active session id is generated in the browser and kept in localStorage,
 * so a reload continues the same conversation and "New chat" starts a clean
 * one. The backend treats it as an opaque string.
 *
 * There are no accounts, so the list of saved chats a tester sees in the
 * sidebar is itself local: a small registry of `{id, title, createdAt,
 * updatedAt}` entries, one per session this browser has actually sent a
 * message in. A session is added to the registry on its first successful
 * turn, not the moment it is created — an untouched "New chat" should not
 * appear in its own history, the same way ChatGPT's does not.
 *
 * localStorage can throw (private windows, blocked site data), so every access
 * is guarded and degrades to an in-memory value for the current page view.
 */

const SESSION_KEY = 'gitaguide.session_id'
const SESSIONS_KEY = 'gitaguide.sessions'
// Keyed by notice version, so a browser that accepted an older notice sees the
// gate again at once rather than after its first message comes back 409.
const CONSENT_KEY = 'gitaguide.consent.beta-1'

// Registry entries beyond this age are dropped on write, so a browser that has
// been reflecting daily for a year does not carry an ever-growing JSON blob in
// localStorage forever.
const MAX_SAVED_SESSIONS = 200

let memorySessionId = null
let memorySessions = []
let memoryConsent = false

function readStore(key) {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStore(key, value) {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    /* storage unavailable; the in-memory fallback carries this page view */
  }
}

/** A short, collision-resistant opaque id. */
export function newSessionId() {
  const random =
    typeof crypto !== 'undefined' && crypto.randomUUID
      ? crypto.randomUUID().replace(/-/g, '').slice(0, 20)
      : Math.random().toString(36).slice(2, 12) + Date.now().toString(36)
  return `web-${random}`
}

/** The current session id, creating and persisting one on first call. */
export function getSessionId() {
  const stored = readStore(SESSION_KEY)
  if (stored) return stored
  if (!memorySessionId) memorySessionId = newSessionId()
  writeStore(SESSION_KEY, memorySessionId)
  return memorySessionId
}

/** Make `id` the active session, e.g. when the sidebar opens a saved chat. */
export function setActiveSessionId(id) {
  memorySessionId = id
  writeStore(SESSION_KEY, id)
}

/** Rotate to a fresh, not-yet-saved session and return its id. */
export function rotateSessionId() {
  const id = newSessionId()
  setActiveSessionId(id)
  return id
}

// --------------------------------------------------------------------- //
// Saved-chat registry (the sidebar's data)
// --------------------------------------------------------------------- //

function readSessions() {
  const raw = readStore(SESSIONS_KEY)
  if (raw) {
    try {
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed)) return parsed
    } catch {
      /* corrupt value; fall through to the in-memory copy */
    }
  }
  return memorySessions
}

function writeSessions(sessions) {
  // Oldest-touched first out, once the registry is over the cap.
  const trimmed =
    sessions.length > MAX_SAVED_SESSIONS
      ? [...sessions].sort((a, b) => b.updatedAt - a.updatedAt).slice(0, MAX_SAVED_SESSIONS)
      : sessions
  memorySessions = trimmed
  writeStore(SESSIONS_KEY, JSON.stringify(trimmed))
}

/** A short title from a message: the sidebar's stand-in for a real subject line. */
export function deriveSessionTitle(text) {
  const clean = (text || '').trim().replace(/\s+/g, ' ')
  if (!clean) return 'New chat'
  if (clean.length <= 48) return clean
  const cut = clean.slice(0, 48)
  const lastSpace = cut.lastIndexOf(' ')
  return `${cut.slice(0, lastSpace > 20 ? lastSpace : 48)}…`
}

/** All saved chats, most recently active first. */
export function listSessions() {
  return [...readSessions()].sort((a, b) => b.updatedAt - a.updatedAt)
}

/**
 * Fold one of the account's server-side sessions into the local registry, so
 * the sidebar shows one merged list rather than "your chats" and "this
 * browser's chats" as two different things. Called only from `AuthBridge`,
 * once per session the server returns after a sign-in.
 *
 * Unlike `touchSession`, the server's title and timestamp are authoritative -
 * they came from the account, not a guess this browser is making - so both
 * overwrite the local copy outright rather than only filling in what is
 * missing.
 */
export function mergeServerSession(id, title, updatedAtIso) {
  const updatedAt = new Date(updatedAtIso).getTime()
  if (!Number.isFinite(updatedAt)) return

  const sessions = readSessions()
  const idx = sessions.findIndex((session) => session.id === id)
  if (idx === -1) {
    writeSessions([...sessions, { id, title: title || 'New chat', createdAt: updatedAt, updatedAt }])
    return
  }
  const next = [...sessions]
  next[idx] = { ...next[idx], title: title || next[idx].title, updatedAt }
  writeSessions(next)
}

/**
 * Record activity on a session, adding it to the registry on its first turn.
 *
 * `candidateTitle` is only adopted when the session has none yet — its first
 * message names the chat, the way it names a ChatGPT conversation, and later
 * turns do not silently rename it out from under a tester.
 */
export function touchSession(id, candidateTitle) {
  const sessions = readSessions()
  const now = Date.now()
  const idx = sessions.findIndex((session) => session.id === id)
  if (idx === -1) {
    writeSessions([
      ...sessions,
      { id, title: candidateTitle || 'New chat', createdAt: now, updatedAt: now },
    ])
    return
  }
  const next = [...sessions]
  next[idx] = {
    ...next[idx],
    updatedAt: now,
    title: next[idx].title || candidateTitle || next[idx].title,
  }
  writeSessions(next)
}

/** Rename a saved chat. A blank title is ignored rather than saved as empty. */
export function renameSession(id, title) {
  const trimmed = (title || '').trim()
  if (!trimmed) return
  const sessions = readSessions()
  const idx = sessions.findIndex((session) => session.id === id)
  if (idx === -1) return
  const next = [...sessions]
  next[idx] = { ...next[idx], title: trimmed.slice(0, 80) }
  writeSessions(next)
}

/**
 * Forget a saved chat locally. Its transcript stays on the server — this only
 * removes the browser's pointer to it, the same as clearing one thread from a
 * client-side history list.
 */
export function deleteSession(id) {
  writeSessions(readSessions().filter((session) => session.id !== id))
}

export function hasConsented() {
  return readStore(CONSENT_KEY) === 'true' || memoryConsent
}

export function recordConsent() {
  memoryConsent = true
  writeStore(CONSENT_KEY, 'true')
}

/** Forget the local flag, e.g. when the server says this session has none. */
export function clearConsent() {
  memoryConsent = false
  try {
    window.localStorage.removeItem(CONSENT_KEY)
  } catch {
    /* storage unavailable; the in-memory flag is already cleared */
  }
}
