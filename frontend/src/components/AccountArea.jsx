import { SignedIn, SignedOut, SignInButton, UserButton } from '@clerk/clerk-react'

import { CLERK_ENABLED } from '../auth.js'

/**
 * The sidebar's footer: sign in, or the signed-in account badge.
 *
 * Renders nothing when accounts are not configured for this build - the
 * sidebar looks exactly as it did before this existed. `<SignedIn>` /
 * `<SignedOut>` are Clerk's own components; they read auth state from
 * `<ClerkProvider>` (mounted in main.jsx) rather than this component tracking
 * it itself, so there is no state here to get out of sync.
 */
export default function AccountArea() {
  if (!CLERK_ENABLED) return null

  return (
    <div className="sidebar-account">
      <SignedOut>
        {/* oauthFlow="popup" keeps Google sign-in in a small popup window
         * instead of navigating this tab away to Clerk's hosted domain -
         * without it, the redirect flow briefly shows Clerk's own branded
         * loading page mid-sign-in, which reads as a broken handoff. */}
        <SignInButton mode="modal" oauthFlow="popup">
          <button type="button" className="sidebar-signin-button">
            Sign in to sync your chats
          </button>
        </SignInButton>
      </SignedOut>

      <SignedIn>
        <div className="sidebar-account-row">
          <UserButton
            afterSignOutUrl={window.location.pathname}
            appearance={{ elements: { avatarBox: 'sidebar-avatar' } }}
          />
          <span className="sidebar-account-label">Synced to your account</span>
        </div>
      </SignedIn>
    </div>
  )
}
