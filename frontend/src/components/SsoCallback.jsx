import { AuthenticateWithRedirectCallback } from '@clerk/clerk-react'

/**
 * Landing point for OAuth sign-in when it falls back to a full-page redirect
 * (popup blocked, or a browser that disallows `window.open` from a click).
 * `oauthFlow="popup"` on the sign-in button avoids this path in the common
 * case, but the fallback still needs a screen of our own rather than
 * Clerk's bare hosted page, which reads as a broken handoff mid-sign-in.
 */
export default function SsoCallback() {
  return (
    <div className="sso-callback-screen">
      <div className="waiting" role="status" aria-live="polite">
        <span className="diya-group" aria-hidden="true">
          <span className="diya-dot" />
          <span className="diya-dot" />
          <span className="diya-dot" />
        </span>
        <span>Almost there — finishing sign-in…</span>
      </div>
      <AuthenticateWithRedirectCallback signInFallbackRedirectUrl="/" signUpFallbackRedirectUrl="/" />
    </div>
  )
}
