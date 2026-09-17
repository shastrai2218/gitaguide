import { ClerkProvider } from '@clerk/clerk-react'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from './App.jsx'
import { CLERK_ENABLED, CLERK_PUBLISHABLE_KEY } from './auth.js'
import SsoCallback from './components/SsoCallback.jsx'
import StaticPage, { docForPath } from './components/StaticPage.jsx'
import './styles.css'

// Three paths are not the chat itself: /privacy and /disclaimer are
// documents, and /sso-callback is where a redirect-based OAuth sign-in
// lands (see SsoCallback.jsx). Vercel rewrites every path to index.html
// (vercel.json), so the choice is made here, once, from the URL.
const path = window.location.pathname
const doc = docForPath(path)
const page =
  path === '/sso-callback' ? <SsoCallback /> : doc ? <StaticPage doc={doc} /> : <App />

createRoot(document.getElementById('root')).render(
  <StrictMode>
    {CLERK_ENABLED ? (
      <ClerkProvider publishableKey={CLERK_PUBLISHABLE_KEY}>{page}</ClerkProvider>
    ) : (
      page
    )}
  </StrictMode>,
)
