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
 *         citations [{source, ref, text}],
 *         memory  {turn_count, summary_included, recent_turns_injected},
 *         meta    {provider, model, latency_ms, stage_latency_ms}}
 *
 *   GET /api/sessions/{id}/messages -> {session_id, messages: [...]}
 *     Optional: the UI degrades gracefully when it is absent (404/501).
 */

const API_BASE = import.meta.env.VITE_API_BASE || '/api'

/** Mock mode is opt-in and always announced in the header. See `MOCK` below. */
export const MOCK = import.meta.env.VITE_MOCK === '1'

/** Meta line under each assistant turn. On unless explicitly switched off. */
export const SHOW_META = import.meta.env.VITE_SHOW_META !== '0'

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
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

function normaliseCitations(raw) {
  if (!Array.isArray(raw)) return []
  return raw
    .map((item) => ({
      source: item?.source ?? '',
      ref: item?.ref ?? '',
      text: item?.text ?? '',
    }))
    .filter((item) => item.ref || item.text)
}

// --------------------------------------------------------------------------- //
// Live calls
// --------------------------------------------------------------------------- //

async function postChatLive(sessionId, message, signal) {
  let response
  try {
    response = await fetch(`${API_BASE}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, message }),
      signal,
    })
  } catch (cause) {
    if (cause?.name === 'AbortError') throw cause
    throw new ApiError(
      'Could not reach GitaGuide. Is the backend running on port 8000?',
      0,
    )
  }

  if (!response.ok) {
    const detail = await readDetail(response)
    throw new ApiError(detail, response.status)
  }
  return normaliseTurn(await response.json())
}

async function readDetail(response) {
  try {
    const body = await response.json()
    if (typeof body?.detail === 'string') return body.detail
  } catch {
    /* body was not JSON; fall through to the status text */
  }
  return `The server returned ${response.status}.`
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

// --------------------------------------------------------------------------- //
// Mock (UI preview only)
// --------------------------------------------------------------------------- //

const MOCK_CITATIONS = [
  {
    source: 'bhagavad_gita',
    ref: 'Bhagavad Gita 2.47',
    text:
      'Let right deeds be thy motive, not the fruit which comes from them. '
      + 'Do not let thy life be spent in inaction.',
  },
  {
    source: 'arthashastra',
    ref: 'Arthashastra 1.7',
    text:
      'Whoever is well versed in the science of polity shall keep his senses '
      + 'under control, and shun another man’s wife, property and injury to others.',
  },
]

const MOCK_CRISIS_TEXT =
  'It sounds as though you are carrying something very heavy right now, and '
  + 'reflection is not what this moment needs. Please talk to someone who can '
  + 'help you directly. You deserve real support, not verses.'

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
