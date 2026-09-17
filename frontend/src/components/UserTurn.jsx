import { useEffect, useRef, useState } from 'react'

/**
 * One of the tester's own messages, editable in place.
 *
 * Editing resends the edited text as a fresh turn and drops everything after
 * it from the transcript - the same "cut the conversation here and continue"
 * behaviour a tester would expect from any chat app. It only ever touches the
 * local view: there is no endpoint to delete a message server-side, so the
 * turns being replaced stay in the session's history on the server (they
 * already do for a retried error, which works the same way). What changes
 * here is what the tester sees and what they send next, not what the server
 * has stored.
 */
export default function UserTurn({ text, editable, onEdit }) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(text)
  const textareaRef = useRef(null)

  useEffect(() => {
    if (!editing) return
    const node = textareaRef.current
    if (!node) return
    node.style.height = 'auto'
    node.style.height = `${node.scrollHeight}px`
    node.focus()
    node.setSelectionRange(node.value.length, node.value.length)
  }, [editing])

  function startEdit() {
    setValue(text)
    setEditing(true)
  }

  function commit() {
    const trimmed = value.trim()
    setEditing(false)
    if (trimmed && trimmed !== text) onEdit(trimmed)
  }

  function autosize(event) {
    event.target.style.height = 'auto'
    event.target.style.height = `${event.target.scrollHeight}px`
  }

  if (editing) {
    return (
      <div className="turn turn-user">
        <div className="bubble bubble-editing">
          <textarea
            aria-label="Edit your message"
            ref={textareaRef}
            className="bubble-edit-textarea"
            value={value}
            rows={1}
            onChange={(event) => {
              setValue(event.target.value)
              autosize(event)
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                commit()
              }
              if (event.key === 'Escape') setEditing(false)
            }}
          />
          <div className="bubble-edit-actions">
            <button type="button" className="bubble-edit-cancel" onClick={() => setEditing(false)}>
              Cancel
            </button>
            <button type="button" className="bubble-edit-save" onClick={commit} disabled={!value.trim()}>
              Save &amp; resend
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="turn turn-user">
      <div className="bubble-wrap">
        {editable && (
          <button type="button" className="bubble-edit-button" aria-label="Edit message" onClick={startEdit}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M12 20h9" />
              <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
            </svg>
          </button>
        )}
        <div className="bubble">{text}</div>
      </div>
    </div>
  )
}
