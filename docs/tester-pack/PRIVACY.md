# GitaGuide — Privacy Notice (closed beta)

**Version:** beta-1 · **Effective:** 5 September 2026 · **Contact:** Kunj
Kansara (the developer)

This notice describes, in plain language, what data the GitaGuide closed beta
collects and how it is used. This is a small invited beta run by an
individual developer, not a commercial service.

## What is collected

- **Your messages and GitaGuide's replies**, stored in a database so the
  session can remember context across turns and across reloads.
- **Safety records.** When a message triggers, or comes near, the safety
  gate, a record of that decision is stored (the decision, its reasons and
  scores). Routine messages with no safety signal do not create safety
  records.
- **Technical records per reply:** detected emotion and intent labels, which
  AI model served the reply, timing, and error counts. Telemetry logs do not
  contain your message text.
- **Consent and feedback:** the fact that you accepted this notice, and any
  thumbs up/down and comments you submit.
- **A random session identifier** created in your browser. The beta does not
  ask for your name, email, or phone number, and does not use analytics
  trackers or advertising cookies.

## How it is used

Only to run the beta and improve it: debugging, evaluating answer quality,
strengthening the safety gate, and measuring speed and reliability. The
developer may read stored conversations for these purposes. Data is **not**
sold, shared for marketing, or used to train third-party models by us.

## Third parties that process data to serve you

- **NVIDIA NIM** — generates replies; your message content is sent to it for
  each turn under NVIDIA's API terms.
- **Neo4j AuraDB** — stores the scripture knowledge graph used for retrieval.
- **Supabase (PostgreSQL)** — stores conversations and records above.
- **Render / Vercel** — host the backend and the app.

Each processes data in order to provide its service to this beta.

## Retention and deletion

Beta data is kept for the duration of the beta and up to 90 days after it
ends, then deleted. You can ask the developer at any time to delete all data
tied to your session identifier (visible in your browser's site data), and it
will be removed within 7 days.

## Your part

Please don't enter passwords, financial details, government ID numbers, or
other people's personal information. The beta stores what you type.

## Changes

If this notice changes materially during the beta, the version number above
changes and the app will ask you to accept it again.

*This is a good-faith plain-language notice for a small invited beta; it is
not legal advice, and the developer is not a law firm.*
