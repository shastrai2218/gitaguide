import { CONSENT_VERSION } from '../api.js'

/**
 * First-visit consent screen.
 *
 * Shown once per browser (localStorage flag). It has to say four things before
 * anyone types anything: what this is, that it is a closed beta, that it is
 * reflection and not treatment, and what is stored. The helplines are here as
 * well as in the persistent footnote, because the first screen is the one a
 * person in distress is most likely to be looking at.
 *
 * The full Privacy Notice and Disclaimer are one click away, in a new tab so
 * the gate stays where it is, and the version a tester is accepting is printed
 * next to the button - the same string the server records against the session.
 */
export default function ConsentGate({ onAccept, error }) {
  return (
    <div className="gate">
      <main className="gate-card">
        <div className="gate-motif" aria-hidden="true">
          <svg viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M32 8c0 12-8 20-16 24 8 0 14 4 16 12 2-8 8-12 16-12-8-4-16-12-16-24z" fill="#e9a23b" opacity="0.8"/>
            <path d="M32 8c0 8 4 14 8 18-4-2-6-2-8 0-2-2-4-2-8 0 4-4 8-10 8-18z" fill="#f0b85a" opacity="0.5"/>
            <circle cx="32" cy="44" r="3" fill="#e9a23b" opacity="0.6"/>
          </svg>
        </div>

        <h1>
          Gita<em>Guide</em>
        </h1>

        <p className="gate-intro">
          Recognises the feeling in what you write and offers a reflection
          grounded in the Bhagavad Gita and the Arthashastra.
        </p>

        <hr className="gate-divider" />

        <h2>Closed beta</h2>
        <p>
          This is an unreleased research prototype shared with a small group of
          testers. Answers can be wrong, thin, or oddly matched to what you
          asked. Replies usually take a few seconds and sometimes up to half a
          minute. The very first message after a quiet spell can take a minute
          on its own — that is the free hosting waking up, not a crash.
        </p>

        <h2>Reflection, not treatment</h2>
        <p>
          GitaGuide is not a therapist, doctor, lawyer, or crisis service, and
          nothing it says is medical or mental-health care. Please do not use it
          in place of professional help.
        </p>

        <h2>What is stored</h2>
        <p>
          Your messages and the replies are stored on the project&apos;s server,
          along with the pipeline&apos;s own trace of each turn, so the system
          can be evaluated and improved. Your acceptance of this notice is
          recorded against your session. Please do not enter names, contact
          details, or anything you would not want kept.
        </p>

        <hr className="gate-divider" />

        <h2>If you need help now</h2>
        <ul>
          <li>Tele-MANAS — 14416 (24×7, free)</li>
          <li>KIRAN — 1800-599-0019 (24×7, free)</li>
          <li>Emergency — 112</li>
        </ul>

        <p className="gate-terms">
          By continuing you accept the{' '}
          <a href="/disclaimer" target="_blank" rel="noopener">
            Disclaimer &amp; Beta Terms
          </a>{' '}
          and the{' '}
          <a href="/privacy" target="_blank" rel="noopener">
            Privacy Notice
          </a>
          {' '}(notice version {CONSENT_VERSION}).
        </p>

        {error && (
          <p className="gate-error" role="alert">
            {error}
          </p>
        )}

        <button type="button" className="gate-accept" onClick={onAccept}>
          I understand — begin
        </button>
      </main>
    </div>
  )
}

