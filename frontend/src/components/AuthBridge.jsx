import { useAuth } from '@clerk/clerk-react'
import { useEffect, useRef } from 'react'

import { claimSession, fetchMySessions, setAuthTokenGetter } from '../api.js'
import { listSessions, mergeServerSession } from '../session.js'

/**
 * Wires Clerk into the rest of the app. Renders nothing - App.jsx only mounts
 * this when `CLERK_ENABLED`, which is what keeps `useAuth()` from throwing on
 * a build with no Clerk key configured.
 *
 * Two jobs:
 *
 * 1. Hand `api.js` a way to fetch the current session token, so every
 *    request it makes can carry one, without `api.js` importing Clerk itself.
 * 2. On the transition into "signed in", claim every session this browser
 *    already knows about (pre-login local chats) and pull the account's
 *    server-side list into the local registry, so the sidebar shows one
 *    merged history instead of losing what was there before sign-in.
 */
export default function AuthBridge({ onSessionsSynced }) {
  const { isSignedIn, getToken } = useAuth()
  const syncedRef = useRef(false)

  useEffect(() => {
    setAuthTokenGetter(() => getToken())
    return () => setAuthTokenGetter(null)
  }, [getToken])

  useEffect(() => {
    if (!isSignedIn || syncedRef.current) return
    syncedRef.current = true

    let cancelled = false
    ;(async () => {
      const local = listSessions()
      // Best-effort: a session someone else already owns just stays theirs
      // (claimSession is a no-op then), and a network hiccup here should not
      // block the rest of sign-in.
      await Promise.all(local.map((session) => claimSession(session.id).catch(() => false)))

      const serverSessions = await fetchMySessions()
      if (cancelled) return
      for (const session of serverSessions) {
        mergeServerSession(session.id, session.title, session.updated_at)
      }
      onSessionsSynced()
    })()

    return () => {
      cancelled = true
    }
  }, [isSignedIn, onSessionsSynced])

  // Signing out again is not "un-sync": the merged local registry stays as a
  // record of what was seen, the same way it would if the server were simply
  // unreachable. Only a fresh sign-in re-arms the claim/merge pass.
  useEffect(() => {
    if (!isSignedIn) syncedRef.current = false
  }, [isSignedIn])

  return null
}
