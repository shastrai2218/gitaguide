/**
 * Who may see which saved chat.
 *
 * The bug this holds down: chats made while signed in stayed in the browser's
 * saved list after signing out, so a signed-out browser - or the next person on
 * a shared computer - could still see and open them. Run with `npm test`; it
 * uses Node's built-in test runner and a stub `localStorage`, so it needs no
 * extra dependencies.
 */
import assert from 'node:assert/strict'
import { beforeEach, describe, test } from 'node:test'

function makeStorage(initial = {}) {
  const data = new Map(Object.entries(initial))
  return {
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => data.set(key, String(value)),
    removeItem: (key) => data.delete(key),
    dump: () => Object.fromEntries(data),
  }
}

let loads = 0

/** A fresh copy of session.js over a fresh storage, so tests cannot leak into each other. */
async function freshSession(initialStorage = {}) {
  globalThis.window = { localStorage: makeStorage(initialStorage) }
  loads += 1
  return import(`./session.js?fresh=${loads}`)
}

const ids = (sessions) => sessions.map((session) => session.id).sort()

describe('who can see which saved chat', () => {
  let session

  beforeEach(async () => {
    session = await freshSession()
  })

  test('an anonymous chat is visible to everyone using the browser', () => {
    session.touchSession('anon-1', 'hello')
    assert.deepEqual(ids(session.listSessions(null)), ['anon-1'])
    assert.deepEqual(ids(session.listSessions('user_a')), ['anon-1'])
  })

  test("an account's chat is visible only to that account", () => {
    session.touchSession('acct-1', 'private', 'user_a')
    assert.deepEqual(ids(session.listSessions('user_a')), ['acct-1'])
    assert.deepEqual(ids(session.listSessions(null)), [], 'signed out must not see it')
    assert.deepEqual(ids(session.listSessions('user_b')), [], 'another account must not see it')
  })

  test('the default view, before anyone is known to be signed in, is anonymous-only', () => {
    session.touchSession('anon-1', 'a')
    session.touchSession('acct-1', 'b', 'user_a')
    assert.deepEqual(ids(session.listSessions()), ['anon-1'])
  })

  test('an entry saved before ownership was tracked is hidden until it is verified', () => {
    // What an older build wrote: no `owner` field at all.
    const legacy = JSON.stringify([{ id: 'old-1', title: 'old', createdAt: 1, updatedAt: 1 }])
    return freshSession({ 'gitaguide.sessions': legacy }).then((legacySession) => {
      assert.deepEqual(ids(legacySession.listSessions(null)), [])
      assert.deepEqual(ids(legacySession.listSessions('user_a')), [])
      assert.deepEqual(ids(legacySession.unverifiedSessions()), ['old-1'])

      legacySession.setSessionOwner('old-1', null)
      assert.deepEqual(ids(legacySession.listSessions(null)), ['old-1'])
      assert.deepEqual(ids(legacySession.unverifiedSessions()), [])
    })
  })
})

describe('signing out', () => {
  test("removes an account's chats from the browser and keeps the anonymous ones", async () => {
    const s = await freshSession()
    s.touchSession('anon-1', 'made signed out')
    s.touchSession('acct-1', 'made signed in', 'user_a')

    const removed = s.purgeAccountSessions()

    assert.deepEqual(removed, ['acct-1'])
    assert.deepEqual(ids(s.listSessions(null)), ['anon-1'])
    // Not merely hidden: the pointer is gone, so signing in as someone else
    // (or nobody) cannot surface it either.
    assert.deepEqual(ids(s.listSessions('user_a')), ['anon-1'])
  })

  test('reports the removed ids so the open chat can be moved off if it was one of them', async () => {
    const s = await freshSession()
    s.touchSession('acct-1', 'x', 'user_a')
    s.touchSession('acct-2', 'y', 'user_a')
    assert.deepEqual(s.purgeAccountSessions().sort(), ['acct-1', 'acct-2'])
    assert.deepEqual(s.purgeAccountSessions(), [], 'a second purge finds nothing')
  })

  test('keeps a named account when asked to (switching straight between accounts)', async () => {
    const s = await freshSession()
    s.touchSession('a-1', 'x', 'user_a')
    s.touchSession('b-1', 'y', 'user_b')
    assert.deepEqual(s.purgeAccountSessions('user_b'), ['a-1'])
    assert.deepEqual(ids(s.listSessions('user_b')), ['b-1'])
  })

  test('the next person on a shared computer sees none of the last account chats', async () => {
    const s = await freshSession()
    s.touchSession('acct-1', 'user a private chat', 'user_a')
    s.purgeAccountSessions() // user_a signs out

    assert.deepEqual(ids(s.listSessions(null)), [])
    assert.deepEqual(ids(s.listSessions('user_b')), [], 'user_b signs in on the same browser')
  })
})

describe('signing in', () => {
  test('a chat claimed into the account moves out of the anonymous set', async () => {
    const s = await freshSession()
    s.touchSession('anon-1', 'before sign-in') // made signed out
    s.setSessionOwner('anon-1', 'user_a') // AuthBridge: the server accepted the claim

    assert.deepEqual(ids(s.listSessions('user_a')), ['anon-1'])
    // It now belongs to the account, so it is not visible signed out and goes
    // at sign-out like any other account chat.
    assert.deepEqual(ids(s.listSessions(null)), [])
    assert.deepEqual(s.purgeAccountSessions(), ['anon-1'])
  })

  test("the account's server-side list is tagged as the account's", async () => {
    const s = await freshSession()
    s.mergeServerSession('srv-1', 'from another device', new Date(2026, 8, 19).toISOString(), 'user_a')
    assert.deepEqual(ids(s.listSessions('user_a')), ['srv-1'])
    assert.deepEqual(ids(s.listSessions(null)), [])
  })

  test('sending while signed in tags an existing anonymous chat as the account\'s', async () => {
    const s = await freshSession()
    s.touchSession('c-1', 'started signed out')
    s.touchSession('c-1', 'ignored', 'user_a')
    assert.deepEqual(ids(s.listSessions(null)), [])
    assert.deepEqual(ids(s.listSessions('user_a')), ['c-1'])
  })

  test('ownership never moves back to anonymous through a plain touch', async () => {
    const s = await freshSession()
    s.touchSession('c-1', 'x', 'user_a')
    s.touchSession('c-1', 'x') // owner omitted
    assert.deepEqual(ids(s.listSessions(null)), [], 'still the account\'s')
    assert.deepEqual(ids(s.listSessions('user_a')), ['c-1'])
  })
})

describe('the registry survives a corrupt or unavailable store', () => {
  test('unreadable storage falls back to memory without throwing', async () => {
    globalThis.window = {
      localStorage: {
        getItem: () => {
          throw new Error('blocked')
        },
        setItem: () => {
          throw new Error('blocked')
        },
        removeItem: () => {},
      },
    }
    loads += 1
    const s = await import(`./session.js?fresh=${loads}`)
    s.touchSession('m-1', 'in memory only', 'user_a')
    assert.deepEqual(ids(s.listSessions('user_a')), ['m-1'])
    assert.deepEqual(s.purgeAccountSessions(), ['m-1'])
    assert.deepEqual(ids(s.listSessions('user_a')), [])
  })
})
