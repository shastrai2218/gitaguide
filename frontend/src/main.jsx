import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from './App.jsx'
import StaticPage, { docForPath } from './components/StaticPage.jsx'
import './styles.css'

// Two paths are documents, not the app: /privacy and /disclaimer. Vercel
// rewrites every path to index.html (vercel.json), so the choice is made here,
// once, from the URL. Everything else is the chat.
const doc = docForPath(window.location.pathname)

createRoot(document.getElementById('root')).render(
  <StrictMode>{doc ? <StaticPage doc={doc} /> : <App />}</StrictMode>,
)
