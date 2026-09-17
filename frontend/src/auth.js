/**
 * Whether accounts are configured for this build, and nothing else.
 *
 * Signing in is optional everywhere in this app - see app/auth.py on the
 * backend, which treats every request exactly the same whether or not it
 * carries a token. The frontend half of that same rule lives here: with no
 * publishable key set, `CLERK_ENABLED` is false, `<ClerkProvider>` is never
 * mounted (main.jsx), and every Clerk-aware component (AuthBridge,
 * AccountArea) renders nothing rather than throwing "used outside
 * ClerkProvider". The app works exactly as it did before accounts existed.
 */
export const CLERK_PUBLISHABLE_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY || ''
export const CLERK_ENABLED = Boolean(CLERK_PUBLISHABLE_KEY)
