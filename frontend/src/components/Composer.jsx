import { useEffect, useRef, useState } from 'react'

/**
 * The message composer.
 *
 * Enter sends, Shift+Enter inserts a newline, and the textarea grows with the
 * content up to a cap set in CSS. The footnote below it is persistent by
 * design: the disclaimer and the helpline must be on screen at every moment of
 * the conversation, not only on the consent screen.
 */
export default function Composer({ onSend, busy }) {
  const [text, setText] = useState('')
  const textareaRef = useRef(null)

  useEffect(() => {
    const node = textareaRef.current
    if (!node) return
    node.style.height = 'auto'
    // scrollHeight excludes the border, so add it back or the box is 2px short
    // and shows a scrollbar on a single line of text.
    const border = node.offsetHeight - node.clientHeight
    node.style.height = `${node.scrollHeight + border}px`
  }, [text])

  function submit() {
    const trimmed = text.trim()
    if (!trimmed || busy) return
    setText('')
    onSend(trimmed)
  }

  function handleKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <div className="composer-wrap">
      <div className="column">
        <form
          className="composer"
          onSubmit={(event) => {
            event.preventDefault()
            submit()
          }}
        >
          <label className="sr-only" htmlFor="composer-input">
            Your message
          </label>
          <textarea
            id="composer-input"
            ref={textareaRef}
            rows={1}
            value={text}
            placeholder="Write what is on your mind…"
            onChange={(event) => setText(event.target.value)}
            onKeyDown={handleKeyDown}
            disabled={busy}
          />
          <button
            type="submit"
            className="send-button"
            disabled={busy || !text.trim()}
          >
            Send
          </button>
        </form>

        <p className="footnote">
          GitaGuide offers reflection, not medical or mental-health care. In
          distress? Tele-MANAS 14416 · emergency 112.{' '}
          <span className="footnote-links">
            <a href="/privacy" target="_blank" rel="noopener">
              Privacy
            </a>
            {' · '}
            <a href="/disclaimer" target="_blank" rel="noopener">
              Disclaimer
            </a>
          </span>
        </p>
      </div>
    </div>
  )
}
