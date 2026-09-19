import { useAuth } from '@clerk/clerk-react'
import { useEffect, useRef } from 'react'

import { claimSession, fetchMySessions, setAuthTokenGetter } from '../api.js'
import {
  deleteSession,
  listSessions,
  mergeServerSession,
  setSessionOwner,
  unverifiedSessions,
} from '../session.js'

/**
 * Wires Clerk into the rest of the app. Renders nothing - App.jsx only mounts
 * this when `CLERK_ENABLED`, which is what keeps `useAuth()` from throwing on
 * a build with no Clerk key configured.
 *
 * Three jobs:
 *
 * 1. Hand `api.js` a way to fetch the current session token, so every
 *    request it makes can carry one, without `api.js` importing Clerk itself.
 * 2. Tell the app who is signed in (`onAuthState`), as soon as Clerk knows and
 *    every time it changes. `null` means signed out. The app uses it to decide
 *    which saved chats may be shown, and to remove an account's chats from this
 *    browser at sign-out.
 * 3. On the transition into "signed in", claim every chat this browser already
 *    knows about (pre-login local chats), tag them as the account's, and pull
 *    the account's server-side list into the local registry, so the sidebar
 *    shows one merged history instead of losing what was there before sign-in.
 *
 * Signing out is the reverse of job 3 and is handled by the app, not here: an
 * account's chats must not stay visible in a browser nobody is signed into.
 */
export default function AuthBridge({ onAuthState, onSessionsSynced }) {
  const { isLoaded, isSignedIn, userId, getToken } = useAuth()
  const syncedForRef = useRef(null)

  useEffect(() => {
    setAuthTokenGetter(() => getToken())
    return () => setAuthTokenGetter(null)
  }, [getToken])

  // Runs after the token getter above is registered, so whatever the app does in
  // response can already make authenticated requests.
  useEffect(() => {
    if (!isLoaded) return
    onAuthState(isSignedIn && userId ? userId : null)
  }, [isLoaded, isSignedIn, userId, onAuthState])

  useEffect(() => {
    if (!isLoaded || !isSignedIn || !userId || syncedForRef.current === userId) return
    syncedForRef.current = userId

    let cancelled = false
    let finished = false
    ;(async () => {
      // Chats this account may already know about: anonymous ones from before
      // sign-in, ones it owns, and entries saved before ownership was tracked.
      const unverified = unverifiedSessions()
      const candidates = [...listSessions(userId), ...unverified]
      const ids = [...new Set(candidates.map((session) => session.id))]

      // Best-effort: a chat someone else already owns stays theirs (claimSession
      // reports that as `false`), and a network hiccup here should not block
      // the rest of sign-in.
      const claimed = new Map(
        await Promise.all(
          ids.map(async (id) => [id, await claimSession(id).catch(() => false)]),
        ),
      )
      if (cancelled) return
      for (const [id, wasClaimed] of claimed) {
        if (wasClaimed) setSessionOwner(id, userId)
      }
      // An entry of unknown ownership that this account could not claim belongs
      // to somebody else (or to nothing). It must not appear in this account's
      // sidebar, so it goes.
      for (const session of unverified) {
        if (!claimed.get(session.id)) deleteSession(session.id)
      }

      const serverSessions = await fetchMySessions()
      if (cancelled) return
      for (const session of serverSessions) {
        mergeServerSession(session.id, session.title, session.updated_at, userId)
      }
      finished = true
      onSessionsSynced()
    })()

    return () => {
      cancelled = true
      // An interrupted pass has to be able to run again.
      if (!finished) syncedForRef.current = null
    }
  }, [isLoaded, isSignedIn, userId, onSessionsSynced])

  // Signing out re-arms the claim/merge pass for the next sign-in.
  useEffect(() => {
    if (isLoaded && !isSignedIn) syncedForRef.current = null
  }, [isLoaded, isSignedIn])

  return null
}
