/**
 * The wire format lives here and nowhere else.
 *
 * Every other module in this app consumes the *normalised* turn shape returned
 * by `sendMessage()`. If the backend contract changes, this file changes and
 * nothing else does.
 *
 * Contract (see app/api/routes.py):
 *
 *   POST /api/chat  {session_id, message}
 *     -> {reply, blocked,
 *         emotion {label, score},
 *         intent  {label, crisis_score},
 *         citations [{id, source, label, ref, text, url, why}],
 *         memory  {turn_count, summary_included, recent_turns_injected},
 *         meta    {provider, model, latency_ms, stage_latency_ms}}
 *
 *   POST /api/consent {session_id, version} -> {session_id, consent_version, consent_at}
 *   POST /api/feedback {message_id, rating, comment} -> {message_id, rating, comment}
 *
 *   GET /api/sessions/{id}/messages -> {session_id, messages: [...]}
 *     Optional: the UI degrades gracefully when it is absent (404/501).
 *
 * Consent is enforced server-side: /api/chat answers 409 with
 * `detail.code === "consent_required"` for a session that has not recorded it.
 */

/**
 * Version of the consent notice this build shows. Must match the server's
 * (`GITAGUIDE_CONSENT_VERSION`; tests/test_deploy_config.py holds the two
 * together). `beta-1` is the version printed on the Privacy Notice and the
 * Disclaimer themselves, so what a tester reads, what they accept, and what
 * the server records are one string.
 */
export const CONSENT_VERSION = 'beta-1'

/** Thrown when the server says this session has not consented. */
export const CONSENT_REQUIRED = 'consent_required'

const API_BASE = import.meta.env.VITE_API_BASE || '/api'

/**
 * How every call in this module gets a Clerk session token, without this
 * module importing React or Clerk itself - `api.js` stays the one file that
 * knows the wire format, not a second place that knows about auth.
 * `AuthBridge.jsx` calls `setAuthTokenGetter` once, from inside
 * `<ClerkProvider>`, with Clerk's own `getToken`. Unset (the default, and
 * always the case when accounts are not configured for this build), every
 * request simply carries no `Authorization` header - identical to how every
 * request worked before accounts existed.
 */
let getAuthToken = null

export function setAuthTokenGetter(fn) {
  getAuthToken = fn
}

async function authHeaders() {
  if (!getAuthToken) return {}
  try {
    const token = await getAuthToken()
    return token ? { Authorization: `Bearer ${token}` } : {}
  } catch {
    // A token fetch failing (expired session, network hiccup) is not this
    // request's problem to report - it just goes out unauthenticated, the
    // same as a signed-out tester's would.
    return {}
  }
}

/** Mock mode is opt-in and always announced in the header. See `MOCK` below. */
export const MOCK = import.meta.env.VITE_MOCK === '1'

/** Meta line under each assistant turn. On unless explicitly switched off. */
export const SHOW_META = import.meta.env.VITE_SHOW_META !== '0'

/**
 * How long to wait for one turn before giving up, in milliseconds.
 *
 * There was no client-side deadline at all, which meant a genuinely hung
 * request span forever behind the diya. This is deliberately generous, because
 * aborting a slow-but-working request is worse than waiting:
 *
 *     ~60s   worst-case Render free-tier cold start (instance wake + both
 *            ONNX models loaded before /health passes)
 *   + ~50s   worst-case turn (45s generation budget plus the local stages)
 *   = ~110s  worst realistic first message
 *
 * 150s leaves margin over that. Anything beyond it is not slowness, it is a
 * request that is never coming back, and the tester is better served by an
 * error they can retry than by a spinner.
 */
export const REQUEST_TIMEOUT_MS = 150_000

/**
 * What went wrong, in the terms the UI has to act on.
 *
 * `offline`  the browser says it has no connection. Nothing was sent.
 * `network`  the request left and the connection failed. Retryable.
 * `timeout`  no answer inside REQUEST_TIMEOUT_MS. NOT auto-retried: the server
 *            is probably still working, and a second request would double the
 *            load on a 0.1-CPU instance for the same answer.
 * `server`   the server answered with a status. The message came from it.
 */
export const FAILURE = {
  OFFLINE: 'offline',
  NETWORK: 'network',
  TIMEOUT: 'timeout',
  SERVER: 'server',
}

/** Delay before the single automatic retry of a failed connection. */
export const NETWORK_RETRY_DELAY_MS = 1200

export const OFFLINE_MESSAGE =
  'You appear to be offline. Your message is still here — send it again once '
  + 'you are back on the network.'

export class ApiError extends Error {
  constructor(message, status, retryAfterSeconds = null, code = null, kind = FAILURE.SERVER) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    /** Seconds to wait, from a 429. Null for every other failure. */
    this.retryAfterSeconds = retryAfterSeconds
    /** Machine-readable reason, e.g. `consent_required`. Null if unstructured. */
    this.code = code
    /** One of `FAILURE`. Decides whether a retry is worth making. */
    this.kind = kind
  }

  get offline() {
    return this.kind === FAILURE.OFFLINE
  }

  /** True when sending the same message again is a reasonable thing to do. */
  get retryable() {
    return this.kind !== FAILURE.SERVER || this.status === 0 || this.status >= 500
  }
}

/** Whether the browser believes it is offline. `false` when it cannot tell. */
export function isOffline() {
  return typeof navigator !== 'undefined' && navigator.onLine === false
}

// --------------------------------------------------------------------------- //
// Normalisation
// --------------------------------------------------------------------------- //

/**
 * Coerce a raw /api/chat body into the shape the UI renders.
 *
 * Defensive on purpose: a field the backend cannot supply arrives as null and
 * the meta line simply omits it. It never invents a value.
 */
function normaliseTurn(body) {
  const emotion = body.emotion || {}
  const intent = body.intent || {}
  const memory = body.memory || {}
  const meta = body.meta || {}
  const blocked = body.blocked === true

  return {
    // Identifies this turn for feedback. Absent on a restored turn, which is
    // why the control renders nothing without it rather than guessing.
    messageId: typeof body.message_id === 'string' ? body.message_id : null,
    reply: typeof body.reply === 'string' ? body.reply : '',
    blocked,
    emotion: {
      label: emotion.label ?? null,
      score: typeof emotion.score === 'number' ? emotion.score : null,
    },
    intent: {
      label: intent.label ?? null,
      crisisScore:
        typeof intent.crisis_score === 'number' ? intent.crisis_score : null,
    },
    // A blocked turn never ran retrieval, so it can never carry citations.
    // Enforced here as well as on the server: two locks on the same door.
    citations: blocked ? [] : normaliseCitations(body.citations),
    memory: {
      turnCount: numberOrNull(memory.turn_count),
      summaryIncluded:
        typeof memory.summary_included === 'boolean'
          ? memory.summary_included
          : null,
      recentTurnsInjected: numberOrNull(memory.recent_turns_injected),
    },
    meta: {
      provider: meta.provider ?? null,
      model: meta.model ?? null,
      latencyMs: numberOrNull(meta.latency_ms),
      // Per-stage wall clock, keyed by pipeline stage name. Kept as the raw
      // map: the meta line decides what is worth showing, not the wire.
      stageLatencyMs:
        meta.stage_latency_ms && typeof meta.stage_latency_ms === 'object'
          ? meta.stage_latency_ms
          : {},
    },
  }
}

function numberOrNull(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/**
 * Normalise the citation list.
 *
 * `label`, `url` and `why` arrived on 9 Sep 2026, when the verse text and the
 * graph-path explanation moved out of the reply and into the citation surface.
 * All three are optional on the wire, so an older backend degrades to the
 * previous rendering rather than breaking.
 *
 * `url` is only accepted as an https URL. It comes from the API, for a verse
 * the citation check already validated — but this is the single place where a
 * link becomes clickable in the page, so the check belongs here regardless of
 * who is upstream.
 */
function safeUrl(value) {
  if (typeof value !== 'string' || !value) return ''
  try {
    return new URL(value).protocol === 'https:' ? value : ''
  } catch {
    return ''
  }
}

function normaliseCitations(raw) {
  if (!Array.isArray(raw)) return []
  return raw
    .map((item) => ({
      id: item?.id ?? '',
      source: item?.source ?? '',
      label: item?.label ?? '',
      ref: item?.ref ?? '',
      text: item?.text ?? '',
      url: safeUrl(item?.url),
      why: item?.why ?? '',
    }))
    .filter((item) => item.label || item.ref || item.text)
}

// --------------------------------------------------------------------------- //
// Live calls
// --------------------------------------------------------------------------- //

/**
 * Send one turn, with a single automatic retry of a failed connection.
 *
 * Tester item 9 pairs "I asked the same query, no response the first time,
 * perfect the second" with "internet disconnected" and "taking long time" in
 * one sentence, and the server-side evidence agrees: turns were admitted to
 * the pipeline and then never completed, which is what a client that goes away
 * mid-request looks like. So a dropped connection is retried once here, before
 * the tester ever sees an error.
 *
 * What is deliberately NOT retried:
 *
 * * the 150s deadline — the server is very likely still working on it, and a
 *   second request would spend another two NIM calls and another turn's memory
 *   on a 0.1-CPU instance to get the same answer;
 * * anything the server answered, including 429 and 5xx — the message it sent
 *   is better than a guess, and 429 in particular already carries the wait;
 * * a caller cancellation — the user did that on purpose.
 */
async function postChatLive(sessionId, message, signal) {
  if (isOffline()) {
    throw new ApiError(OFFLINE_MESSAGE, 0, null, null, FAILURE.OFFLINE)
  }

  try {
    return await postChatAttempt(sessionId, message, signal)
  } catch (error) {
    const retryable =
      error instanceof ApiError && error.kind === FAILURE.NETWORK && !signal?.aborted
    if (!retryable) throw error

    // The connection failed. Wait a moment for it to come back, then try once.
    await sleep(NETWORK_RETRY_DELAY_MS, signal)
    if (isOffline()) {
      throw new ApiError(OFFLINE_MESSAGE, 0, null, null, FAILURE.OFFLINE)
    }
    return await postChatAttempt(sessionId, message, signal)
  }
}

async function postChatAttempt(sessionId, message, signal) {
  // Two reasons a request can be abandoned: the caller cancelled it (New
  // session, unmount) or it exceeded the deadline. They are told apart below,
  // because the first must stay silent and the second must be reported.
  const deadline = new AbortController()
  const timer = setTimeout(() => deadline.abort(), REQUEST_TIMEOUT_MS)
  const onCallerAbort = () => deadline.abort()
  signal?.addEventListener('abort', onCallerAbort)

  let response
  try {
    response = await fetch(`${API_BASE}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(await authHeaders()) },
      body: JSON.stringify({ session_id: sessionId, message }),
      signal: deadline.signal,
    })
  } catch (cause) {
    // The caller's own cancellation propagates untouched, so App.jsx can drop
    // it silently rather than showing an error for something the user did.
    if (signal?.aborted) throw new DOMException('aborted', 'AbortError')
    if (cause?.name === 'AbortError') {
      throw new ApiError(
        `GitaGuide did not answer within ${Math.round(REQUEST_TIMEOUT_MS / 1000)} seconds. `
          + 'It may still be waking up — your message is still here, so you can send '
          + 'it again.',
        0,
        null,
        null,
        FAILURE.TIMEOUT,
      )
    }
    throw new ApiError(
      'Could not reach GitaGuide — the connection dropped. Your message is still '
        + 'here; send it again when you are ready.',
      0,
      null,
      null,
      FAILURE.NETWORK,
    )
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', onCallerAbort)
  }

  if (!response.ok) {
    const failure = await readFailure(response)
    throw new ApiError(
      failure.message,
      response.status,
      failure.retryAfterSeconds,
      failure.code,
    )
  }
  return normaliseTurn(await response.json())
}

/**
 * Turn a failed response into a message and, for a 429, the wait.
 *
 * The rate limiter returns a structured detail object; every other error
 * returns a plain string. Both shapes are handled here so no other module has
 * to know the difference.
 */
async function readFailure(response) {
  const headerWait = Number.parseInt(response.headers.get('Retry-After') || '', 10)
  let retryAfterSeconds = Number.isFinite(headerWait) ? headerWait : null

  let body
  try {
    body = await response.json()
  } catch {
    body = null
  }

  const detail = body?.detail
  if (detail && typeof detail === 'object') {
    if (typeof detail.retry_after_seconds === 'number') {
      retryAfterSeconds = detail.retry_after_seconds
    }
    const code = typeof detail.code === 'string' ? detail.code : null
    if (typeof detail.message === 'string') {
      return { message: detail.message, retryAfterSeconds, code }
    }
    return {
      message: `The server returned ${response.status}.`,
      retryAfterSeconds,
      code,
    }
  }
  if (typeof detail === 'string') {
    return { message: detail, retryAfterSeconds, code: null }
  }

  return {
    message: `The server returned ${response.status}.`,
    retryAfterSeconds,
    code: null,
  }
}

/**
 * Record consent for this session, server-side.
 *
 * The browser flag alone is not a record: it does not survive cleared storage
 * and cannot say which version was shown. This is what the server enforces.
 */
export async function recordConsentOnServer(sessionId) {
  if (MOCK) return true
  let response
  try {
    response = await fetch(`${API_BASE}/consent`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(await authHeaders()) },
      body: JSON.stringify({ session_id: sessionId, version: CONSENT_VERSION }),
    })
  } catch {
    throw new ApiError(
      'Could not reach GitaGuide to record your consent. Is the backend running?',
      0,
    )
  }
  if (!response.ok) {
    const failure = await readFailure(response)
    throw new ApiError(failure.message, response.status, null, failure.code)
  }
  return true
}

/**
 * Record a verdict on one assistant turn.
 *
 * Called once on the vote itself and again if a comment follows; the server
 * replaces the row, so one turn keeps one unambiguous verdict.
 */
export async function sendFeedback(messageId, rating, comment = '') {
  if (MOCK) return true
  const response = await fetch(`${API_BASE}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message_id: messageId, rating, comment }),
  })
  if (!response.ok) {
    const failure = await readFailure(response)
    throw new ApiError(failure.message, response.status, null, failure.code)
  }
  return true
}

/**
 * Optional history endpoint. Returns `null` — not an error — when the backend
 * does not implement it, so the caller can simply skip restoring history.
 */
export async function fetchSessionMessages(sessionId) {
  if (MOCK) return null
  let response
  try {
    response = await fetch(
      `${API_BASE}/sessions/${encodeURIComponent(sessionId)}/messages`,
      { headers: await authHeaders() },
    )
  } catch {
    return null
  }
  if (!response.ok) return null
  try {
    const body = await response.json()
    return Array.isArray(body?.messages) ? body.messages : null
  } catch {
    return null
  }
}

/**
 * This account's saved chats, most recently active first. Only meaningful
 * when signed in - the caller is expected to check that first, since a
 * signed-out request has nothing to authenticate the call with and the
 * server refuses it with 401 rather than an empty list.
 */
export async function fetchMySessions() {
  if (MOCK) return []
  let response
  try {
    response = await fetch(`${API_BASE}/sessions`, { headers: await authHeaders() })
  } catch {
    return []
  }
  if (!response.ok) return []
  try {
    const body = await response.json()
    return Array.isArray(body?.sessions) ? body.sessions : []
  } catch {
    return []
  }
}

/**
 * Attach a session this browser already knows about to the account that just
 * signed in. Safe to call for a session the account already owns, or does not
 * own at all (a stranger's claimed session): both come back `claimed: false`
 * rather than an error, since neither is this call's problem to report.
 */
export async function claimSession(sessionId) {
  if (MOCK) return false
  let response
  try {
    response = await fetch(
      `${API_BASE}/sessions/${encodeURIComponent(sessionId)}/claim`,
      { method: 'POST', headers: await authHeaders() },
    )
  } catch {
    return false
  }
  if (!response.ok) return false
  try {
    const body = await response.json()
    return body?.claimed === true
  } catch {
    return false
  }
}

// --------------------------------------------------------------------------- //
// Mock (UI preview only)
// --------------------------------------------------------------------------- //

// Kept in step with the wire format on purpose: this is what the UI preview
// renders, so a field the real API sends and the mock omits is a field nobody
// sees until production.
const MOCK_CITATIONS = [
  {
    id: 'BG-2.47',
    source: 'bhagavad_gita',
    label: 'BG 2.47',
    ref: 'Bhagavad Gita 2.47 (BG-2.47)',
    url: 'https://vedabase.io/en/library/bg/2/47',
    why: "detected emotion 'annoyance' <-[:ADDRESSES_EMOTION]- (BG-2.47)",
    text:
      'Let right deeds be thy motive, not the fruit which comes from them. '
      + 'Do not let thy life be spent in inaction.',
  },
  {
    id: 'AS-1.7.1',
    source: 'arthashastra',
    label: 'AS 1.7.1',
    ref: 'Arthashastra Book 1, Chapter 7, Sutra 1 (AS-1.7.1)',
    url: '',
    why: "(AS-1.7.1) -[:HAS_THEME]-> theme 'self-control'",
    text:
      'Whoever is well versed in the science of polity shall keep his senses '
      + 'under control, and shun another man’s wife, property and injury to others.',
  },
]

const MOCK_CRISIS_TEXT =
  'It sounds as though you are carrying something very heavy right now, and '
  + 'reflection is not what this moment needs. Please talk to someone who can '
  + 'help you directly. You deserve real support, not verses.'

/**
 * A cancellable delay. Used by the mock's latency simulation and by the live
 * path's single network retry, which is why it is not mock-only despite living
 * in this section.
 */
function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms)
    signal?.addEventListener('abort', () => {
      clearTimeout(timer)
      reject(new DOMException('aborted', 'AbortError'))
    })
  })
}

async function postChatMock(sessionId, message, signal) {
  const text = message.toLowerCase()

  if (text.includes('slow-test')) await sleep(14000, signal)
  else await sleep(900, signal)

  if (text.includes('error-test')) {
    throw new ApiError('Mock failure, as requested by "error-test".', 500)
  }

  if (text.includes('limit-test')) {
    throw new ApiError(
      'GitaGuide is thinking for someone else right now. '
        + 'Please wait a moment and send it again.',
      429,
      12,
    )
  }

  const crisis = text.includes('crisis-test')
  return normaliseTurn({
    reply: crisis
      ? MOCK_CRISIS_TEXT
      : 'This is mock copy for previewing the layout. It runs no model and '
        + 'retrieves nothing. The paragraph is deliberately long enough to show '
        + 'how commentary sits against the marigold rule, and how the verse '
        + 'leaves below break the column.',
    blocked: crisis,
    emotion: { label: crisis ? 'sadness' : 'annoyance', score: crisis ? 0.91 : 0.62 },
    intent: {
      label: crisis ? 'crisis' : 'workplace_conflict',
      crisis_score: crisis ? 0.94 : 0.02,
    },
    message_id: `mock-${Date.now()}`,
    citations: crisis ? [] : MOCK_CITATIONS,
    memory: { turn_count: 2, summary_included: false, recent_turns_injected: 2 },
    meta: {
      provider: 'mock',
      model: 'mock-preview',
      latency_ms: crisis ? 120 : 4200,
      stage_latency_ms: crisis
        ? { emotion: 62, intent: 41, safety_input: 3, persist: 14 }
        : {
            emotion: 58,
            intent: 39,
            safety_input: 2,
            memory: 11,
            retrieval: 380,
            generation: 3660,
            safety_output: 2,
            persist: 48,
          },
    },
  })
}

// --------------------------------------------------------------------------- //

/** Send one turn. Resolves to the normalised turn, or throws `ApiError`. */
export function sendMessage(sessionId, message, signal) {
  return MOCK
    ? postChatMock(sessionId, message, signal)
    : postChatLive(sessionId, message, signal)
}
