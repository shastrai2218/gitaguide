/**
 * Client-side session identity and the consent flag.
 *
 * The session id is generated in the browser and kept in localStorage, so a
 * reload continues the same conversation and "New session" starts a clean one.
 * The backend treats it as an opaque string.
 *
 * localStorage can throw (private windows, blocked site data), so every access
 * is guarded and degrades to an in-memory value for the current page view.
 */

const SESSION_KEY = 'gitaguide.session_id'
const CONSENT_KEY = 'gitaguide.consent.v1'

let memorySessionId = null
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

/** Rotate to a fresh session and return the new id. */
export function rotateSessionId() {
  memorySessionId = newSessionId()
  writeStore(SESSION_KEY, memorySessionId)
  return memorySessionId
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
