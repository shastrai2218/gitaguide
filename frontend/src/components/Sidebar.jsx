import { useState } from 'react'

import AccountArea from './AccountArea.jsx'

/**
 * Saved chats, grouped by recency, ChatGPT-style.
 *
 * "Saved" means "in this browser's local registry" (see session.js) - a chat
 * earns its place here on its first successful turn, not the moment "New
 * chat" is clicked. Renaming and deleting only ever touch that local
 * registry; the transcript itself still lives on the server under its
 * session id. Signing in (AccountArea, at the bottom) additionally syncs that
 * registry to an account across devices; signed out, this behaves exactly as
 * it did before accounts existed.
 *
 * Below the mobile breakpoint this renders as an off-canvas drawer (see
 * `.sidebar` in styles.css); `open`/`onClose` control that state, and are
 * ignored by the desktop layout, which keeps the sidebar always visible.
 */

function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
}

function groupLabel(updatedAt) {
  const days = Math.round((startOfDay(new Date()) - startOfDay(new Date(updatedAt))) / 86_400_000)
  if (days <= 0) return 'Today'
  if (days === 1) return 'Yesterday'
  if (days <= 7) return 'Previous 7 days'
  return 'Older'
}

function groupSessions(sessions) {
  const order = ['Today', 'Yesterday', 'Previous 7 days', 'Older']
  const buckets = new Map()
  for (const session of sessions) {
    const label = groupLabel(session.updatedAt)
    if (!buckets.has(label)) buckets.set(label, [])
    buckets.get(label).push(session)
  }
  return order
    .filter((label) => buckets.has(label))
    .map((label) => ({ label, items: buckets.get(label) }))
}

export default function Sidebar({
  sessions,
  activeSessionId,
  open,
  onNewChat,
  onSelect,
  onDelete,
  onRename,
  onClose,
}) {
  const [editingId, setEditingId] = useState(null)
  const [editValue, setEditValue] = useState('')
  const [query, setQuery] = useState('')

  const trimmedQuery = query.trim().toLowerCase()
  const visible = trimmedQuery
    ? sessions.filter((session) => session.title.toLowerCase().includes(trimmedQuery))
    : sessions
  const groups = trimmedQuery ? [{ label: 'Search results', items: visible }] : groupSessions(visible)

  function startRename(session) {
    setEditingId(session.id)
    setEditValue(session.title)
  }

  function commitRename(id) {
    if (editValue.trim()) onRename(id, editValue)
    setEditingId(null)
  }

  return (
    <>
      {open && <div className="sidebar-backdrop" onClick={onClose} aria-hidden="true" />}

      <aside className={`sidebar${open ? ' is-open' : ''}`} aria-label="Saved chats">
        <div className="sidebar-head">
          <button type="button" className="new-chat-button" onClick={onNewChat}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M12 5v14M5 12h14" />
            </svg>
            New chat
          </button>

          {sessions.length > 0 && (
            <div className="sidebar-search">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <circle cx="11" cy="11" r="7" />
                <path d="M21 21l-4.3-4.3" />
              </svg>
              <input
                type="text"
                value={query}
                placeholder="Search chats"
                aria-label="Search saved chats"
                onChange={(event) => setQuery(event.target.value)}
              />
              {query && (
                <button
                  type="button"
                  className="sidebar-search-clear"
                  aria-label="Clear search"
                  onClick={() => setQuery('')}
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M18 6 6 18M6 6l12 12" />
                  </svg>
                </button>
              )}
            </div>
          )}
        </div>

        <nav className="sidebar-list">
          {sessions.length === 0 && (
            <p className="sidebar-empty">Your reflections will be saved here.</p>
          )}

          {sessions.length > 0 && trimmedQuery && visible.length === 0 && (
            <p className="sidebar-empty">No chats match "{query.trim()}".</p>
          )}

          {groups.map((group) => (
            <div className="sidebar-group" key={group.label}>
              <p className="sidebar-group-label">{group.label}</p>
              {group.items.map((session) => (
                <div
                  key={session.id}
                  className={`sidebar-row${session.id === activeSessionId ? ' is-active' : ''}`}
                >
                  {editingId === session.id ? (
                    <input
                      className="sidebar-rename-input"
                      value={editValue}
                      autoFocus
                      maxLength={80}
                      onChange={(event) => setEditValue(event.target.value)}
                      onBlur={() => commitRename(session.id)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') {
                          event.preventDefault()
                          commitRename(session.id)
                        }
                        if (event.key === 'Escape') setEditingId(null)
                      }}
                    />
                  ) : (
                    <button
                      type="button"
                      className="sidebar-row-title"
                      title={session.title}
                      onClick={() => onSelect(session.id)}
                    >
                      {session.title}
                    </button>
                  )}

                  {editingId !== session.id && (
                    <div className="sidebar-row-actions">
                      <button
                        type="button"
                        className="sidebar-icon-button"
                        aria-label="Rename chat"
                        onClick={() => startRename(session)}
                      >
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                          <path d="M12 20h9" />
                          <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
                        </svg>
                      </button>
                      <button
                        type="button"
                        className="sidebar-icon-button"
                        aria-label="Delete chat"
                        onClick={() => onDelete(session.id)}
                      >
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                          <path d="M3 6h18" />
                          <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                          <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                        </svg>
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          ))}
        </nav>

        <AccountArea />
      </aside>
    </>
  )
}
