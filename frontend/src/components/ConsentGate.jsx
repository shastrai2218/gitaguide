/**
 * First-visit consent screen.
 *
 * Shown once per browser (localStorage flag). It has to say four things before
 * anyone types anything: what this is, that it is a closed beta, that it is
 * reflection and not treatment, and what is stored. The helplines are here as
 * well as in the persistent footnote, because the first screen is the one a
 * person in distress is most likely to be looking at.
 */
export default function ConsentGate({ onAccept, error }) {
  return (
    <div className="gate">
      <main className="gate-card">
        <h1>
          Gita<em>Guide</em>
        </h1>

        <p>
          GitaGuide reads what you write, recognises the feeling in it, and
          offers a reflection grounded in specific verses from the Bhagavad Gita
          and the Arthashastra. Every answer shows the verses it drew on, so you
          can judge the reasoning for yourself.
        </p>

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

        <h2>If you need help now</h2>
        <ul>
          <li>Tele-MANAS — 14416 (24×7, free)</li>
          <li>KIRAN — 1800-599-0019 (24×7, free)</li>
          <li>Emergency — 112</li>
        </ul>

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
