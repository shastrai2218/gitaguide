import { useEffect, useRef, useState } from 'react'

/**
 * The message composer with speech-to-text (voice input).
 */
export default function Composer({ value, onChange, onSend, busy, online = true }) {
  const textareaRef = useRef(null)
  const [listening, setListening] = useState(false)
  const recognitionRef = useRef(null)

  useEffect(() => {
    const node = textareaRef.current
    if (!node) return
    node.style.height = 'auto'
    const border = node.offsetHeight - node.clientHeight
    node.style.height = `${node.scrollHeight + border}px`
  }, [value])

  // Setup Web Speech API if supported
  useEffect(() => {
    const SpeechRecognition =
      window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SpeechRecognition) return

    const recognition = new SpeechRecognition()
    recognition.continuous = true
    recognition.interimResults = true
    recognition.lang = 'en-US'

    recognition.onresult = (event) => {
      let interim = ''
      let final = ''
      for (let i = event.resultIndex; i < event.results.length; ++i) {
        if (event.results[i].isFinal) {
          final += event.results[i][0].transcript
        } else {
          interim += event.results[i][0].transcript
        }
      }
      if (final) {
        onChange(value ? `${value} ${final.trim()}` : final.trim())
      }
    }

    recognition.onerror = () => {
      setListening(false)
    }

    recognition.onend = () => {
      setListening(false)
    }

    recognitionRef.current = recognition

    return () => {
      recognition.stop()
    }
  }, [onChange, value])

  function toggleVoice() {
    if (!recognitionRef.current) {
      alert('Voice dictation is not supported by your current browser.')
      return
    }
    if (listening) {
      recognitionRef.current.stop()
      setListening(false)
    } else {
      try {
        recognitionRef.current.start()
        setListening(true)
      } catch (err) {
        console.warn('Speech recognition error:', err)
        setListening(false)
      }
    }
  }

  function submit() {
    if (listening && recognitionRef.current) {
      recognitionRef.current.stop()
      setListening(false)
    }
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
            placeholder={listening ? 'Listening to your voice… speak freely' : 'Write what is on your mind…'}
            onChange={(event) => onChange(event.target.value)}
            onKeyDown={handleKeyDown}
            disabled={busy}
          />

          <div className="composer-actions">
            <button
              type="button"
              className={`voice-button ${listening ? 'listening' : ''}`}
              onClick={toggleVoice}
              disabled={busy}
              title={listening ? 'Stop listening' : 'Speak your thoughts'}
              aria-label={listening ? 'Stop listening' : 'Voice input'}
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                <line x1="12" y1="19" x2="12" y2="22" />
              </svg>
            </button>

            <button
              type="submit"
              className="send-button"
              disabled={busy || !value.trim()}
            >
              Send
              <svg
                className="send-icon"
                viewBox="0 0 16 16"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.4"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M2 8h11M9 4l4 4-4 4" />
              </svg>
            </button>
          </div>
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
