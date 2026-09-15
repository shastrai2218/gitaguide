import { useEffect, useRef } from 'react'

/**
 * The message composer.
 *
 * Enter sends, Shift+Enter inserts a newline, and the textarea grows with the
 * content up to a cap set in CSS. The footnote below it is persistent by
 * design: the disclaimer and the helpline must be on screen at every moment of
 * the conversation, not only on the consent screen.
 *
 * The text is owned by `App`, not by this component (changed 9 Sep 2026). It
 * has to be, because a failed turn puts the message back here so nothing is
 * lost — tester item 9: *"I asked same queries 1st time but not get response
 * but in second time I am getting answer perfectly."* Losing what they typed
 * on top of that is the avoidable half of the problem.
 */
export default function Composer({ value, onChange, onSend, busy, online = true }) {
  const textareaRef = useRef(null)

  useEffect(() => {
    const node = textareaRef.current
    if (!node) return
    node.style.height = 'auto'
    // scrollHeight excludes the border, so add it back or the box is 2px short
    // and shows a scrollbar on a single line of text.
    const border = node.offsetHeight - node.clientHeight
    node.style.height = `${node.scrollHeight + border}px`
  }, [value])

  function submit() {
    const trimmed = value.trim()
    if (!trimmed || busy) return
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
        {!online && (
          <p className="composer-offline" role="status" aria-live="polite">
            You are offline. Whatever you have typed stays here.
          </p>
        )}

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
            value={value}
            placeholder="Write what is on your mind…"
            onChange={(event) => onChange(event.target.value)}
            onKeyDown={handleKeyDown}
            disabled={busy}
          />
          <button
            type="submit"
            className="send-button"
            disabled={busy || !value.trim()}
          >
            Send
            <svg className="send-icon" viewBox="0 0 16 16" aria-hidden="true">
              <path d="M1 8l6-6v4h7v4H7v4L1 8z" />
            </svg>
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
