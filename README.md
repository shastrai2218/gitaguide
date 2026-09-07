# GitaGuide

**An emotion-aware, explainable guidance system grounded in the Bhagavad Gita and the
Arthashastra.** A final-year research project, running as a **closed beta** for a small group
of invited testers. It is not a commercial service, not a product, and not a clinical tool.

Someone describes a situation that is weighing on them - a decision, a conflict, a worry - in
English or Hinglish. GitaGuide reads the emotion in the message, decides what kind of help is
being asked for, checks whether this is a crisis **before doing anything else**, retrieves
verses through a knowledge graph, and answers only from those verses, citing each one by id
and saying which graph path surfaced it. Every stage of that is stored with the turn and can
be replayed, because the research claim of the project is explainability, not fluency.

- **App (closed beta):** <https://gitaguide-livid.vercel.app> - by invitation; the first
  screen asks for consent to the beta notice, and nothing works until it is given.
- **Tester pack:** [`docs/tester-pack/`](docs/tester-pack/) - [ONBOARDING](docs/tester-pack/ONBOARDING.md),
  [PRIVACY](docs/tester-pack/PRIVACY.md), [DISCLAIMER](docs/tester-pack/DISCLAIMER.md).
  The last two are also served by the app at `/privacy` and `/disclaimer`.
- **If you are in distress**, this is not the tool: Tele-MANAS **14416**, KIRAN
  **1800-599-0019**, emergency **112**.

---

## Contents

1. [The two scriptures](#the-two-scriptures)
2. [The pipeline](#the-pipeline)
3. [The safety gate and its non-negotiables](#the-safety-gate-and-its-non-negotiables)
4. [Production stack](#production-stack)
5. [Fitting 512 MB: INT8 and one turn at a time](#fitting-512-mb-int8-and-one-turn-at-a-time)
6. [Two worlds: local and production](#two-worlds-local-and-production)
7. [Running it locally](#running-it-locally)
8. [Evaluation](#evaluation)
9. [Operating the beta](#operating-the-beta)
10. [Repository layout](#repository-layout)
11. [Limitations, stated](#limitations-stated)

---

## The two scriptures

| Scripture | Units | Id form | Text |
| --- | ---: | --- | --- |
| Bhagavad Gita | **701** verses | `BG-<chapter>.<verse>` | Shri Purohit Swami's English translation (public domain), from the open `gita/gita` dataset; Sanskrit and transliteration kept alongside |
| Arthashastra | **3,135** sutras | `AS-<book>.<chapter>.<sutra>` | R. Shamasastry's 1915 translation, public domain, with its century-old wording intact |

**3,836 verses** in total. Each carries weak labels - themes, the GoEmotions labels it
addresses, and concepts - produced by keyword rules over the English text
(`corpus/tagging.py`). Those labels become the `Theme`, `Emotion` and `Concept` nodes of the
knowledge graph, and verses that share enough of them are joined by `RELATED_TO` edges
(hub labels attached to more than 8% of the corpus are excluded from that pass, which is why
the graph has 14,709 edges rather than 270,000). The Arthashastra's sutra numbers are
positional - the paragraph's index within its chapter - stable and citable, but not a
critical edition's numbering.

## The pipeline

One turn runs, in this order and no other: **emotion** (`SamLowe/roberta-base-go_emotions`
as ONNX INT8 on CPU, with an arousal-weighted intensity) → **intent** (zero-shot
classification by the LLM plus a keyword prior, six classes) → **the safety gate**, which
short-circuits the turn when it fires → **memory** (the last 6 turns verbatim and a rolling
summary refreshed every 8 messages) → **hybrid retrieval** through `neo4j-graphrag`'s
`HybridCypherRetriever` (vector + full-text over the graph, with 1-2 hop paths through
`Emotion`, `Theme` and `RELATED_TO` annotating and boosting each candidate; re-ranked as
`0.55·vector + 0.20·fulltext + 0.15·graph + 0.25` for a scripture that suits the intent) →
**grounded generation** (the model may reason but not source: every id it cites is checked
against the retrieved set, a hallucinated or missing citation costs one corrective retry,
and an answer that still fails is replaced by a deterministic one composed from the retrieved
verses; a "Why this verse" section restates each path) → **the output safety gate** → persist.
The whole trace - every score, every path, per-stage wall clock - is stored on the message row
and served by `GET /explain/{message_id}`.

Two things guard the generation step beyond citations. A **wall-clock budget** of 45 s spans
the whole provider chain, shared as `remaining / providers_left`, and when it runs out the
tester gets an honest "the guide is overloaded" reply rather than a spinner or a 500. An
**output sanity guard** measures the answer body - word count, repetition, and stutter (one
word or word pair repeated back to back) - retries once if it reads as word salad, and
serves the same overload reply if the retry does too. It exists because a fallback-model reply
in the beta passed every other check (`grounded=True`, three valid citations, a clean safety
verdict) while its body was `I see the the the / I the I the`.

## The safety gate and its non-negotiables

The gate runs **before** retrieval and generation and again **after** generation. When the
input gate fires the pipeline returns the crisis response - the helplines, in plain words - and
nothing else: no retrieval, no generation call, no verses. This is enforced structurally in
`GuidanceOrchestrator.handle_turn` and asserted by tests, by the production smoke test, and by
the beta adapter, which drops citations from any blocked turn even if some were present.

| Layer | What it is | Effect |
| --- | --- | --- |
| **rule** | 14 deterministic patterns over suicidal ideation, self-harm, harm to others, abuse, medical emergency - including Latin-script Hindi/Hinglish phrasings; negation and third-party-past framing are suppressed | blocks |
| **model** | the LLM's semantic crisis score, threshold 0.5; a malformed classification is **retried once**, and a result that is still malformed is treated as *risk unknown*, not *risk low* | blocks |
| **soft-cue fallback** | 10 crisis-adjacent cues that block conservatively **only** when no rule fired and the semantic layer failed twice - the window in which the gate would otherwise be a single layer | blocks |
| **output** | 4 patterns over the generated answer: harm methods, discouraging treatment, moralising, dismissal | replaces the answer |

The non-negotiables, which no sprint has been allowed to change without an approved and
measured reason:

- **Order.** Emotion → intent → gate → everything else. The gate cannot be moved later.
- **A blocked turn carries no verses and makes no generation call.**
- **GoEmotions never blocks on its own.** Emotion and intensity are context for the audit,
  not a trigger; grief guidance stays available unless a rule or the semantic layer fires.
- **Malformed means unknown.** A classifier that returns garbage has not said "safe".
- **The process refuses to boot** if the canonical crisis phrase does not block, if the
  canonical control does, or if the generation budget is below 15 s (the same budget bounds
  the classifier, so a small value would silently starve the semantic layer).
- **Everything is auditable.** Every trigger and every near-miss is written to
  `safety_logs`; the red-team suite (36 crisis prompts at **100% recall**, 25 benign
  controls) is a required CI check and a release gate.

## Production stack

The beta runs on free tiers, deliberately, and the trade-offs are written down rather than
hidden ([`docs/DEPLOY.md`](docs/DEPLOY.md)).

| Component | Service | What matters about it |
| --- | --- | --- |
| API | **Render** (Free: 512 MB, 0.1 CPU) | one instance, one worker; sleeps after ~15 min idle, woken by a 10-minute GitHub Actions ping; cold start ~80 s |
| Knowledge graph | **Neo4j AuraDB Free** | 3,836 verses / 14,709 edges, vector + full-text indexes; the username and database are the instance id, not `neo4j` |
| Sessions, messages, safety logs, feedback | **Supabase PostgreSQL** | through the transaction pooler with `asyncpg`; schema created by `create_all` plus an additive `ADD COLUMN IF NOT EXISTS` pass |
| UI | **Vercel** | Vite + React 18, plain CSS, no UI framework; `frontend/src/api.js` is the only file that knows the wire format |
| Language model | **NVIDIA NIM** (developer / evaluation tier, ~40 requests/min per model) | `nvidia/nemotron-3-super-120b-a12b` primary, `nvidia/nemotron-3.5-lightning-30b-a3b` fallback; hybrid reasoning switched off; **no fake last resort in production** - a total outage becomes the overload reply, not a template dressed as guidance |
| Telemetry | a rotating JSONL file plus daily counters on `/health` | text-free by construction and by test; the database is the durable record |

Consent is recorded server-side against the session (`POST /api/consent`, version
`beta-1`), and `POST /api/chat` refuses a session without it. Rate limits are token buckets
per session (5/min, burst 3), per IP (20/min, burst 8) and global (16/min, burst 6) - the
global one is what protects the NIM budget, because one turn spends two calls on the primary
model.

## Fitting 512 MB: INT8 and one turn at a time

The fp32 embedder did not fit: measured under Render Free's exact shape, the service idled at
497 MiB of 512 and peaked at 511 MiB during one turn, and the kernel's answer to the second
concurrent turn was `SIGKILL` - a bare 502 with nothing in the application log. Four settings,
all measured on 7 Sep 2026, made it fit with margin:

| | fp32, 2 threads | int8, 1 thread, serialised |
| --- | ---: | ---: |
| idle after boot | 497 MiB (97.1%) | **398 MiB (77.8%)** |
| peak, one turn | 511 MiB (99.7%) | **429 MiB (83.7%)** |
| peak, one running + one queued | would OOM | **430 MiB (84.0%)** |
| headroom at peak | 1.5 MiB | **81.7 MiB** |

- **INT8 embedder** (`GITAGUIDE_EMBEDDER_PRECISION=int8`): the same `all-MiniLM-L6-v2`,
  quantised, −37 MiB. It is only correct because **the index is embedded with it too**.
  Vectors are compared by cosine; an INT8 query against fp32-built vectors does not error, it
  quietly returns worse neighbours (0.986 min cosine). Both sides on INT8 cost nothing
  measurable: hit rate 0.1750 → 0.1750 on the 40-query gold set. The export records which
  encoder wrote the graph and the importer refuses a mismatch
  ([`docs/DATA_MIGRATION.md`](docs/DATA_MIGRATION.md)).
- **One ONNX intra-op thread**: on 0.1 CPU a second thread cannot run in parallel; it only
  costs an arena. −47 MiB and a *faster* cold start.
- **`MALLOC_ARENA_MAX=2`**: −14.5 MiB of per-thread malloc arenas.
- **Serialised turns**: an `asyncio.Semaphore(1)` around pipeline execution in `/api/chat`
  only - not health, consent or feedback - so peak memory is one turn, not N. A queued turn
  waits up to 30 s, then gets the overload reply. This is a memory bound, not throughput
  control; the width is a hard 1.

These travel as a set, and `tests/test_deploy_config.py` asserts the pairing: if production
is on INT8 it must also serialise, because INT8 leaves room for one turn and not two.

## Two worlds: local and production

Every stage has two implementations behind one interface: a real backend and an in-process
fake. `app/config.py` is the **only** place that decides which one you get, and
`GITAGUIDE_MOCK_MODE=true` (the default) selects all the fakes: the lexicon emotion backend,
the hashing embedder, the in-memory retriever over a 16-verse sample corpus, the
deterministic `FakeLLMProvider`, the in-memory repository. The safety gate, the citation
enforcement, the re-ranker and the output guard are the real implementations in both worlds.

The rule that keeps the two honest:

- **Nothing real degrades silently into a fake.** The embedder raises rather than falling
  back to hashing; the evaluation harness refuses to run against a dead graph; the UI shows a
  `mock` badge from the same flag that switches its data on; `scripts/prod_smoke.py` asserts
  the real ONNX model, `neo4j-hybrid-cypher`, `postgresql+asyncpg` and a real NIM model
  answering, and fails otherwise.
- **Offline and production numbers never appear in the same table.** The in-memory
  retriever expands the candidate set through the graph and the Neo4j one does not, so the
  same query scores differently for reasons unrelated to the change under test. Every figure
  in [`docs/kg_measurements.md`](docs/kg_measurements.md) says which backend and which
  encoder produced it.
- **The test suite is pinned offline** before any fixture runs (`tests/conftest.py`), so a
  developer's exported credentials can never reach a real database from a test.

## Running it locally

Offline, no services, no keys:

```bash
python -m venv .venv && . .venv/Scripts/activate      # Windows; bin/activate elsewhere
pip install -r requirements.txt                       # serving set, torch-free
cp .env.example .env                                  # GITAGUIDE_MOCK_MODE=true
uvicorn app.main:app --reload                         # http://localhost:8000/docs
```

```bash
cd frontend && npm install && npm run dev             # http://localhost:5173, proxies /api
```

The real stack: set `GITAGUIDE_MOCK_MODE=false`, `GITAGUIDE_NEO4J_*`,
`GITAGUIDE_POSTGRES_DSN` and `NVIDIA_API_KEY` in `.env`, build the corpus
(`python -m corpus.load_gita`, `python -m corpus.build_arthashastra`) and the graph
(`python -m kg.build_graph --reset`), then start the API. `docker-compose.yml` brings up
api + neo4j + postgres + frontend. `requirements-dev.txt` adds pytest, ruff and the torch
reference encoder used only to check the ONNX export's parity.

```bash
pytest                                                # offline: 494 pass, 7 opt-in tests skip without Docker/torch
ruff check app corpus kg eval tests scripts
pytest eval/test_safety_redteam.py -v                 # the release gate
```

## Evaluation

**Safety** is a release gate, not a metric: 36 crisis prompts must all block and 25 benign
prompts must all pass, in CI, on every push.

**Retrieval** is the open research problem. On the live graph (`neo4j-hybrid-cypher`, 3,836
verses, INT8 index and encoder, deterministic intent, k=5) the 40-query gold set gives **hit
rate 0.175**, recall@5 0.056, MRR 0.074, nDCG@5 0.046. Testers will meet verses that do not
quite fit; that is the finding, and it is what the feedback buttons are for. Two things
about how those numbers are produced matter as much as the numbers:

- Intent is a live LLM call that feeds the re-ranker, so the plain harness is **not
  reproducible**: three identical runs gave hit rates of 0.125, 0.200 and 0.200.
  `python -m eval.retrieval_eval --deterministic-intent` swaps in the keyword prior and
  reproduces exactly; every comparison is made in that mode.
- Every artefact records the backend and the encoder that produced it. A mismatched run - an
  fp32 query against the INT8 index - scored *higher* than either consistent one, and was
  caught only by that provenance field.

Offline runs (hashing embedder, 16-verse sample) are a floor for development, not a result.

## Operating the beta

Console steps and their order are in [`docs/DEPLOY.md`](docs/DEPLOY.md); the graph and
database moves are in [`docs/DATA_MIGRATION.md`](docs/DATA_MIGRATION.md). Before anyone is
invited, and after every deploy:

```bash
python -m scripts.prod_smoke https://gitaguide-api.onrender.com
```

It asserts the things that look fine when they are wrong - mock mode off, real backends, a
NIM model answering, consent refused then accepted at the current notice version, a crisis
turn that ran no retrieval or generation, and the rate limiter engaging under a *concurrent*
burst (a sequential burst cannot drain a token bucket when each request outlasts the refill
interval, which once made a working limiter look disabled). During the beta, `GET /health`
carries the day's telemetry counters - the semantic-layer failure window, the output guard,
queued and shed turns - and Supabase holds the durable record.

## Repository layout

```
gitaguide/
├── app/
│   ├── main.py                  lifespan: self-test, build the pipeline, warm the models
│   ├── config.py                pydantic-settings; the single place deciding real vs fake
│   ├── embeddings.py            MiniLM on onnxruntime (fp32 / int8) + the offline hashing embedder
│   ├── telemetry.py             text-free JSONL events + daily counters
│   ├── api/
│   │   ├── routes.py            POST /chat, GET /explain/{id}, GET /health, GET /safety/logs
│   │   ├── beta.py              /api/chat, /api/consent, /api/feedback, /api/sessions/{id}/messages
│   │   ├── ratelimit.py         token buckets; X-Forwarded-For read by declared proxy hops
│   │   ├── concurrency.py       the Semaphore(1) pipeline gate
│   │   └── schemas.py           wire models for the research surface
│   ├── llm/                     LLMProvider interface, NIM provider, fallback chain, fake
│   ├── db/                      SQLAlchemy models, repository interface, in-memory + SQL impls
│   └── pipeline/
│       ├── emotion.py           GoEmotions ONNX INT8 + lexicon fallback + intensity
│       ├── intent.py            zero-shot + keyword prior; malformed-output retry
│       ├── safety.py            rule / semantic / soft-cue / output layers, crisis response
│       ├── gate_selftest.py     boot-time proof the gate is armed
│       ├── memory.py            last N turns + rolling summary
│       ├── retrieval.py         hybrid retrieval + re-ranker (Neo4j and in-memory)
│       ├── generation.py        grounded generation, citation enforcement, output guard
│       ├── orchestrator.py      the pipeline order; owns the short-circuit
│       └── schemas.py           the trace types
├── corpus/                      Verse schema, Gita and Arthashastra loaders, weak labelling
├── kg/                          graph build (nodes, edges, hub suppression, indexes) + Cypher
├── eval/                        red-team suite, retrieval eval, 40-query gold set
├── scripts/                     export/import/re-embed the graph, prod_smoke, NIM smoke
├── tests/                       offline unit + contract tests, deploy-config assertions
├── frontend/                    Vite + React 18 chat UI, consent gate, /privacy, /disclaimer
├── docs/                        DEPLOY, DATA_MIGRATION, kg_measurements, tester-pack/
├── .github/workflows/           ci.yml (lint, tests, red-team, eval, UI build), keepwarm.yml
├── Dockerfile  render.yaml  docker-compose.yml  requirements.txt  requirements-dev.txt
```

## Limitations, stated

- **Retrieval quality** is low in absolute terms (hit rate 0.175) and is the research
  problem, not a deployment defect. Known ranking issues queued for after the beta: an
  `ADDRESSES_EMOTION` hop on a secondary emotion (`curiosity` at 0.42 on a business question)
  pulling an unrelated verse, and 2-hop paths whose intermediate node is outside the candidate
  set rendering as `(None)` in the explanation.
- **Weak labels.** Themes and emotions come from keyword rules over English translations;
  metaphor is missed and polysemy mistagged.
- **The Arthashastra reads like 1915**, because it is. Sutra numbers are positional.
- **Intent is zero-shot + a prior**; the fine-tuned classifier is an explicit stub.
- **The gate's Hinglish coverage is a rule table**, not language understanding; unseen
  phrasings depend on the semantic layer, whose residual failure window is counted in
  telemetry rather than assumed away.
- **Free tiers.** Cold starts after quiet periods; in-process rate limits and counters that
  reset on restart (and a spin-down is a restart); telemetry on ephemeral disk; AuraDB Free
  pauses after three idle days.
- **Schema by `create_all`** plus additive columns, not migrations.

**Closed beta, not commercial.** GitaGuide is a research prototype shared with invited
testers under the notice in `docs/tester-pack/`. It is not a therapist, doctor, lawyer or
religious authority, it says so before the first message, and the safety gate exists because
the failure mode of a guidance system is not a bad answer but a harmful one.
