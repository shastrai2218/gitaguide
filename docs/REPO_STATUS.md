# Repository & Deployment Status — 30 September 2026

**What this is.** A read-only audit of the GitaGuide repository as it stands at
`origin/master` = **`b8026b6`** on <https://github.com/shastrai2218/gitaguide>,
written for a reader who is not a programmer. Every claim below was checked
against the repository itself; where something could not be checked from the
repository, it says **UNKNOWN** and how to find out.

**What changed in the world since the last report.** The Render service that
used to run the API has been deleted. The only backend now is a single AWS EC2
virtual machine running `docker compose` behind a Caddy TLS proxy (introduced in
commits `d443b54`…`984bd96`). The frontend is still served by Vercel, from a
**new** Vercel project (`gitaguide-neon.vercel.app`, replacing
`gitaguide-livid.vercel.app`).

**Where the numbers come from.** Unless a line says otherwise, every number in
this document was produced on the author's Windows 11 laptop (Python 3.11.9,
Node 24.14.1, ruff 0.16.4) in a detached git worktree checked out at exactly
`b8026b6`, in offline/mock mode with no external service reachable. Nothing was
fetched, pulled or pushed beyond what the repository owner had already fetched.
GitHub's own pages and API were not readable from here (the repository is
private and no GitHub token is configured on this machine), so anything that
lives only on GitHub — pull requests, Actions results — is marked UNKNOWN.

---

## 0. Summary in five lines

1. **History is intact.** `b8026b6` is a clean fast-forward of the previous
   baseline `a3f25e2` (17 new commits, no rewrite). Local `master` is simply
   17 behind.
2. **The code is green.** Full suite on `b8026b6`: **900 passed, 7 skipped,
   0 failed** (907 collected). `ruff check` clean. Frontend: 13/13 unit tests
   pass and the production build succeeds. Baseline was 872 passed / 7 skipped,
   so 28 tests were added.
3. **Accounts landed.** Optional Clerk sign-in is now in the backend and
   frontend (`app/auth.py`, two new endpoints, one new database column). It is
   *off* unless two environment variables are set, and every anonymous flow
   works exactly as before.
4. **`origin/master` is deployable** to the EC2 stack as written, with three
   things to check on the VM itself before trusting it (§2.5, §5b).
5. **The EC2 deployment is manual** (SSH, `git pull`, `docker compose up`).
   Nothing in the repository deploys automatically, and nothing in the
   repository records which commit is running in production (§5a, §3.3).

---

## 1. Where we are

### 1.1 Position

| | value |
| --- | --- |
| `origin/master` | `b8026b6` — *Fix: an account's chats stayed visible in the browser after signing out* — 2005hari, 2026-09-20 |
| previous audited baseline | `a3f25e2` — *fix(deploy): ship data/ in the image, and say so loudly when it is missing* — 2026-09-16 |
| relationship | `a3f25e2` **is an ancestor** of `b8026b6` (`git merge-base` = `a3f25e2`); 17 commits ahead, 0 diverged. **History was not rewritten.** |
| total commits on master | 87, first commit `c05db53` on 2026-08-24 |

### 1.2 Branches

| branch | tip | vs `origin/master` | note |
| --- | --- | --- | --- |
| `origin/master` (remote) | `b8026b6` | — | the only branch on the new remote |
| `master` (local) | `a3f25e2` | 17 behind, 0 ahead | not yet fast-forwarded, deliberately (the owner asked for no merge/pull yet) |
| `arthashastra-corpus` (local) | `ea581cd` (2026-08-26) | 81 behind, 0 ahead | fully merged long ago; safe to delete whenever convenient |

There is no `ui-ux-redesign` branch on the new remote. Its work arrived on
master through two merge commits (`ac870b2`, `def4ab5`), so nothing is lost;
the branch itself was either deleted or never pushed to `shastrai2218`.

### 1.3 Pull requests and CI status — UNKNOWN from here

The GitHub API answers `404 Not Found` for this repository without a token,
and the anonymous browser sees a "Page not found" page: the repository is
private. There is no `gh` CLI on this machine. So the list of open PRs, their
mergeability, and the Actions result for `b8026b6` **could not be read**.

Two things *can* be said from the history:

- The merge commit `ac870b2` is titled *"Merge pull request #1 from
  kunjkansara01/ui-ux-redesign"*. That PR number belongs to the **old**
  repository (`kunjkansara01/gitaguide`); PR numbers on `shastrai2218/gitaguide`
  start again from 1. The "PR #2 lineage" for the EC2 work is likewise a number
  from the old repository.
- To read the current state: open
  <https://github.com/shastrai2218/gitaguide/pulls> and
  <https://github.com/shastrai2218/gitaguide/actions?query=branch%3Amaster>.
  Section 2.3 predicts what Actions should show.

### 1.4 The 17 commits, by what the diff actually did

Oldest first. "Flag" marks a commit whose message and diff disagree in a way a
reader should know about. Authors `2005hari` and `2005Hari` are the same
person under two spellings; `copilot-swe-agent[bot]` is GitHub's Copilot agent.

| hash | author | what the diff actually did | flag |
| --- | --- | --- | --- |
| `65a293e` | 2005hari | Frontend only (7 files): a visual restyle of `styles.css` (+540 lines), suggestion chips, small changes to `App.jsx`, `ConsentGate.jsx`, `WaitingIndicator.jsx`. | — |
| `7d37984` | 2005hari | 34 files. Frontend: new sidebar (`Sidebar.jsx`), edit/regenerate a turn (`UserTurn.jsx`), Clerk sign-in wiring (`main.jsx`, `session.js`), 995-line CSS rewrite. **Backend:** new `app/auth.py`, `PyJWT` dependency, new `sessions.user_id` column + index, new repository methods, two new endpoints and optional-auth on three existing ones; 342 lines of new tests. | **Yes.** The message reads as a frontend redesign "and optional Clerk auth"; the diff also changes the **database schema** and the **API surface**. Anyone reading the log for backend changes would miss this commit. |
| `6950aa5` | 2005hari | Merge of `origin/master` (the comment-cleanup pass `0a9dec0` and `a3f25e2`) into the redesign branch. Python changes are comment/docstring lines only. | — |
| `9a47e82` | copilot bot | `ci.yml`: adds `PyJWT[crypto]` to the CI install line. | — |
| `73952a9` | 2005Hari | `app/auth.py`: strips a trailing `/` from the issuer and checks the issuer string directly instead of the `auth_enabled` property. | — (vague title, correct content) |
| `bed3e6b` | 2005Hari | `UserTurn.jsx`: replaces a visually-hidden `<label>` with `aria-label`. | — |
| `4b27626` | copilot bot | `requirements.txt`: bumps `PyJWT` 2.11.0 → 2.13.0. | **Yes.** Message is *"Apply remaining changes"*, which says nothing. The diff is a dependency version bump. |
| `37fd187` | 2005Hari | `app/auth.py`: reword one docstring. | — |
| `ac870b2` | 2005Hari | Merge of old-repo PR #1 into master (the redesign + accounts, above). | — |
| `1c68bb2` | 2005hari | `ci.yml`: comment explaining the PyJWT install; `tests/test_embeddings.py`: three tests now skip when `onnxruntime` is absent. | — |
| `d443b54` | 2005hari | **Adds the EC2 stack**: `docker-compose.vm.yml`, `deploy/vm/Caddyfile`, `.env.vm.example` (118 lines), `docs/DEPLOY_AWS.md` (265 lines); `.gitignore`/`.dockerignore` learn about `.env.vm`. | — |
| `d0521f0` | 2005hari | Repoints the pinned frontend URL (`gitaguide-livid` → `gitaguide-neon`) in README, onboarding, two tests, `render.yaml`; adds measured EC2 latency findings to `DEPLOY_AWS.md`. | — |
| `def4ab5` | 2005hari | Merge of the redesign branch (EC2 + URL repoint) into master. | — |
| `5626315` | 2005hari | README and `DEPLOY.md` describe EC2 as the API host; `render.yaml` gains a 5-line "LEGACY" header comment. | minor: a "docs:" commit also edits deployment config (comment only) |
| `984bd96` | 2005hari | **Deletes `render.yaml`**, `.github/workflows/keepwarm.yml`, `.env.production.example`. Also rewrites `tests/test_deploy_config.py` (+223/−…) to assert against `.env.vm.example` and `docker-compose.vm.yml` instead of the blueprint, and edits comments in `config.py`, `api.js`, `vite.config.js`, `prod_smoke.py`, `test_tester_pack.py`. | **Yes.** Message says *"Remove the Render deployment files"*; the larger half of the diff is the **migration of the deployment tests** to the new config files. Correct work, under-described. |
| `2f1fddf` | 2005hari | New `scripts/keepalive_neo4j.py` (daily read-only query so Aura Free is not paused) + 5 tests. | — |
| `b8026b6` | 2005hari | Frontend: on sign-out, remove that account's chats from the browser (`App.jsx`, `session.js`, `AuthBridge.jsx`); adds `session.test.js` (13 tests) and an `npm test` step to CI. | minor: a "Fix:" commit also adds a test suite and a CI step — welcome, but not in the title |

**Line-ending / encoding check.** No CRLF line endings were committed and no
byte-order marks were introduced by these commits (checked every touched file
at `b8026b6`).

---

## 2. Master deployability

### 2.1 Every file changed outside `frontend/` since `a3f25e2`

(33 paths; `frontend/` had a further 24.)

| path | change | one line |
| --- | --- | --- |
| `.dockerignore` | M | keeps `.env` and `.env.vm` out of the image build |
| `.env.example` | M | documents the optional `GITAGUIDE_CLERK_ISSUER` |
| `.env.production.example` | **D** | the Render env template, removed |
| `.env.vm.example` | **A** | the EC2 env template — the file the tests now hold production to |
| `.github/workflows/ci.yml` | M | installs `PyJWT`; runs `npm test` in the frontend job |
| `.github/workflows/keepwarm.yml` | **D** | the Render keep-awake ping, removed |
| `.gitignore` | M | ignores `.env.vm` |
| `Dockerfile` | M | comments only: "Render" wording replaced |
| `README.md` | M | EC2 is the API host; new URLs; layout tree updated |
| `app/api/beta.py` | M | optional auth on chat/consent/messages; two new endpoints; 403 for claimed sessions |
| `app/auth.py` | **A** | Clerk JWT verification (see §2.2) |
| `app/config.py` | M | new `clerk_issuer` setting and `auth_enabled` property |
| `app/db/models.py` | M | `sessions.user_id` column |
| `app/db/repository.py` | M | `claim_session`, `list_sessions_for_user`, `user_id` on the session record |
| `app/db/session.py` | M | boot-time `ADD COLUMN IF NOT EXISTS user_id` + `CREATE INDEX IF NOT EXISTS` |
| `app/db/sql_repository.py` | M | SQL versions of the two new methods |
| `deploy/vm/Caddyfile` | **A** | TLS reverse proxy config for the VM |
| `docker-compose.vm.yml` | **A** | the production stack: `api` + `caddy` |
| `docs/DEPLOY.md` | M | banner pointing to `DEPLOY_AWS.md`; new "Step 4b — Accounts (Clerk)" |
| `docs/DEPLOY_AWS.md` | **A** | the EC2 runbook (295 lines) |
| `docs/tester-pack/ONBOARDING.md` | M | new app URL |
| `render.yaml` | **D** | the Render blueprint, removed |
| `requirements.txt` | M | `PyJWT[crypto]==2.13.0` |
| `scripts/keepalive_neo4j.py` | **A** | daily Aura touch |
| `scripts/prod_smoke.py` | M | example URL and one log-message wording |
| `tests/test_accounts.py` | **A** | 10 tests: claiming, listing, 403/404 rules |
| `tests/test_auth.py` | **A** | 11 tests: token verification edge cases |
| `tests/test_deploy_config.py` | M | asserts `.env.vm.example` + compose instead of `render.yaml` |
| `tests/test_embeddings.py` | M | 3 tests skip without `onnxruntime` |
| `tests/test_frontend_reliability.py` | M | follows the `switchToSession` refactor |
| `tests/test_keepalive_neo4j.py` | **A** | 5 tests for the keep-alive script |
| `tests/test_tester_pack.py` | M | new URL; reads consent version from `.env.vm.example` |

### 2.2 What `current_user_id`, `PyJWT` and "issuer handling" build

**In one sentence:** a tester can optionally sign in (email/password or Google,
via a third-party service called Clerk), and when they do, their chats are
attached to their account so they follow them across devices; if they never
sign in, nothing changes.

**Yes — there is now auth/accounts code in the backend.** Item 7 (accounts)
had been deferred in the earlier audits; it arrived anyway in `7d37984`
(2026-09-17, 2005hari), merged via `ac870b2`. What it consists of:

- **`app/auth.py`** (new, 100 lines). `verify_token()` at
  [`app/auth.py:47`](../app/auth.py) checks a token's *signature* against
  Clerk's published public keys (`{issuer}/.well-known/jwks.json`) using the
  `PyJWT` library with RS256. `current_user_id()` at
  [`app/auth.py:78`](../app/auth.py) reads the `Authorization: Bearer …`
  header and returns the user id, or `None` for no/invalid token. The backend
  never talks to Clerk's API and never sees a password. "Issuer handling"
  (`73952a9`) is the small fix that strips a trailing slash from the issuer
  URL so it matches the token's `iss` claim.
- **New dependency:** `PyJWT[crypto]==2.13.0` (`requirements.txt`), which pulls
  in `cryptography`. It is a *hard import* of `app.main` even when accounts are
  off, which is why CI had to install it (§2.3).
- **New env vars:** backend `GITAGUIDE_CLERK_ISSUER` (empty = accounts off,
  [`app/config.py:343`](../app/config.py)); frontend `VITE_CLERK_PUBLISHABLE_KEY`
  (unset = no sign-in UI, [`frontend/src/auth.js:12`](../frontend/src/auth.js)).
  Both must name the *same* Clerk application.
- **New DB column:** `sessions.user_id` (nullable, indexed —
  [`app/db/models.py:62`](../app/db/models.py)). Applied automatically at boot
  by `ADD COLUMN IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`
  ([`app/db/session.py:57-63`](../app/db/session.py)). Additive and safe to
  run against the live Supabase database; no data is rewritten.
- **New endpoints:** `GET /api/sessions` (this account's chats; 401 without a
  token — [`app/api/beta.py:585`](../app/api/beta.py)) and
  `POST /api/sessions/{id}/claim` (attach a pre-login chat to the account —
  [`app/api/beta.py:608`](../app/api/beta.py)).
- **Changed endpoints:** `/api/chat`, `/api/consent` and
  `/api/sessions/{id}/messages` now accept an optional token, and refuse with
  **403** a session that *another* account has claimed
  ([`_ensure_session_access`, `app/api/beta.py:259`](../app/api/beta.py)).

**What it does *not* do.** An unclaimed (anonymous) session stays readable by
anyone who knows its id — `tests/test_accounts.py:214` asserts this on purpose.
The research surface (`POST /chat`, `GET /explain/{id}`, `GET /safety/logs` in
`app/api/routes.py`) is untouched by these commits and still has no access
check at all. So the 19 Sep privacy audit's findings remain open (§4.2).

### 2.3 Why CI was red on master

Actions results could not be read (§1.3). From the repository, the failure is
reconstructible and was already fixed **in-tree**:

- **The red window: `7d37984` → `ac870b2` (17 Sep).** `app/auth.py` imports
  `jwt`, and `app/main.py` imports the beta router, which imports `app.auth`.
  CI's "Install core dependencies" step did not install `PyJWT`, so the unit
  test job would fail at import time with `ModuleNotFoundError: No module named
  'jwt'` before running a single test.
- **Fixed by** `9a47e82` (adds `"PyJWT[crypto]"` to the CI install line) and
  `1c68bb2` (same, plus makes three `test_embeddings.py` tests skip when
  `onnxruntime` is absent, which it is in the core CI job).
- The two other Python jobs (`safety-redteam`, `retrieval-eval`) import only
  `app.config` and `app.pipeline.*`, none of which reaches `app.auth`, so they
  were never affected (checked: no `app.pipeline`/`app.db`/`app.llm` module
  imports the API layer).
- `b8026b6` adds an `npm test` step; locally `node --test` passes 13/13 on
  Node 24 (CI pins Node 20; the tests use only the built-in runner).

**Prediction:** Actions on `b8026b6` should be green. If it is not, the failing
job name is the first thing to read, because none of the four jobs failed here.

### 2.4 Local run on `origin/master` (`b8026b6`)

| check | result | vs baseline |
| --- | --- | --- |
| `pytest` (full suite, offline) | **900 passed, 7 skipped, 0 failed**, 907 collected (from the JUnit report) | baseline 872 / 7 → **+28 tests** (`test_accounts` 10, `test_auth` 11, `test_keepalive_neo4j` 5, and +2 net elsewhere) |
| `ruff check .` | **All checks passed** | same as baseline |
| `ruff format --check .` | 69 files *would* be reformatted | baseline: 64. **Not a CI gate** — CI runs `ruff check` only. Pre-existing; the 5 new files follow the same house style as the old ones |
| frontend `npm test` | **13 passed, 0 failed** | new in `b8026b6` |
| frontend `npm run build` | **builds**: `index-DuPkX6Kv.js` 265 KB (81 KB gzipped) + 978 KB source map | bundle grew from 168 KB: Clerk's SDK is now included |

The 7 skips are the same 7 as before (§4.1). **Failing tests: none.**

One environment note, reported because it appeared: on this Windows machine
both full runs printed a `Windows fatal exception: access violation` stack
trace mid-run, from `pyarrow` being loaded through `transformers` during
`test_embeddings`. The run continued and every test passed; the JUnit report
is complete. It also swallows pytest's last summary line, which is why the
count above comes from the JUnit XML. This is a local-toolchain artefact
(Linux CI has no `pyarrow`), not a repository defect.

### 2.5 VERDICT

**`origin/master` (`b8026b6`) is deployable right now.** The suite is green,
the only schema change is additive and self-applying, accounts are off unless
explicitly configured, and the deployment files it needs are the ones the EC2
host already uses.

The three things to verify **on the VM**, not in the repository, before
calling a deploy of this commit done:

1. `.env.vm` on the instance contains real values for every `REPLACE_ME` in
   `.env.vm.example` — in particular the seven that used to live only in
   Render's dashboard (§5b).
2. After `docker compose … up -d --build`, the API log shows
   `database schema ensured (… additive column(s), 1 additive index(es) checked)`
   — proof the `user_id` column landed on Supabase.
3. `python -m scripts.prod_smoke https://<host>` passes all sections.

**Revert vs forward-fix.** There is nothing to revert *for*. A revert to
`a3f25e2` would also delete `docker-compose.vm.yml`, the Caddyfile and the
runbook — the only description of the only backend — so that option is
listed only to be ruled out:

| option | what it would do | risk |
| --- | --- | --- |
| Revert to `a3f25e2` | Removes accounts, the EC2 stack and the URL repoints. | **High.** The VM would have no compose file to build from, and the repo would again point at a deleted Render service. Not viable. |
| Forward-fix (recommended) | Deploy `b8026b6` as-is; then address the open items in §4.2 as their own small commits. | Low. Each item is independent and none blocks serving testers. |

---

## 3. What the project contains today

### 3.1 Top-level map

Sizes are tracked bytes at `b8026b6` (untracked local files noted separately).

| directory | tracked files / size | what it is |
| --- | --- | --- |
| `app/` | 39 files, 444 KB | **The API server.** `main.py` builds everything once at start-up and runs the boot self-tests. `api/` is the HTTP surface (`routes.py` research endpoints, `beta.py` the endpoints the UI uses, `ratelimit.py`, `concurrency.py`). `pipeline/` is the eight-stage answer pipeline (§3.2). `db/` is storage (PostgreSQL or an in-memory stand-in). `llm/` talks to the language-model providers. `auth.py` (new) verifies sign-in tokens. `telemetry.py` writes one privacy-safe JSON line per turn. `config.py` is every setting the code reads. |
| `tests/` | 38 files, 357 KB | **834 unit tests** (plus 73 in `eval/`). Fully offline; see §4.1 for the shape. |
| `frontend/` | 34 files, 204 KB | **The chat UI.** React + Vite. `src/api.js` is the only file that knows the wire format; `src/session.js` keeps the browser-side chat registry; `components/` has the consent gate, composer, sidebar, sign-in bridge, verse leaf. `dist/` is the built output (not tracked). |
| `data/` | 16 files, 877 KB | `intent_lexicon.yaml` (the zero-cost stage-1 router table, 224 phrases) and `arthashastra_raw/book_1–15.txt` (Shamasastry 1915, 900 KB). Untracked here and 68 MB: `data/graph_export/`, the Neo4j dump used by the import scripts. |
| `corpus/` | 6 files, 47 KB | Loaders and schemas for the scripture corpus. `load_gita.py` (Purohit Swami from the `gita/gita` dataset), `build_arthashastra.py` (Wikisource), `tagging.py` (emotion/theme tags). `data/sample_corpus.json` (16 verses) is the only scripture text in git; the built `gita.json` (701) and `arthashastra.json` (3,135) are untracked (§3.4). |
| `kg/` | 3 files, 30 KB | Builds the knowledge graph from the corpus and holds the Cypher queries retrieval uses. |
| `eval/` | 8 files, 96 KB | The measurement harnesses: `retrieval_eval.py` (hit@5 / MRR on 40 gold pairs), `test_safety_redteam.py` (73 crisis prompts, must be 100% recall — a CI gate), `conversation_set.jsonl` (the 40-turn routing set). |
| `scripts/` | 9 files, 89 KB | Operator tools: `prod_smoke.py` (post-deploy check), `import_neo4j.py`/`export_neo4j.py`/`reembed_graph.py` (graph moves), `keepalive_neo4j.py` (new), `smoke_nvidia.py`, `diagnose_retrieval.py`, `download_arthashastra.py`. |
| `docs/` | 8 files, 159 KB | `DEPLOY.md` (accounts, graph, database console steps — Render parts kept as history), `DEPLOY_AWS.md` (the EC2 runbook), `DATA_MIGRATION.md`, `kg_measurements.md` (every measurement, §1–§14d; **untouched since `a3f25e2`**), `gold_set_review.md`, `tester-pack/` (ONBOARDING, PRIVACY, DISCLAIMER). |
| `deploy/vm/` | 1 file, 1 KB | `Caddyfile`: the TLS proxy config. |
| `.github/workflows/` | 1 file, 3 KB | `ci.yml`: four jobs — unit tests, safety red-team, retrieval eval (report only), frontend build. `keepwarm.yml` is gone. |
| root | 12 files, 49 KB | `Dockerfile` (the API image), `docker-compose.vm.yml` (**production**), `docker-compose.yml` (local dev: adds Postgres, Neo4j and the UI), `.env.vm.example` (production settings template), `.env.example` (dev), `requirements*.txt`, `pyproject.toml`, `pytest.ini`, `README.md`. |

### 3.2 The eight-stage pipeline

All in `app/pipeline/orchestrator.py`, `handle_turn()`; the numbered comments
are the flow markers. Unchanged since `a3f25e2`.

| # | stage | where the work happens | marker |
| --- | --- | --- | --- |
| 1 | Emotion — what the person seems to feel | `app/pipeline/emotion.py` → `EmotionDetector.detect` (ONNX, off the event loop) | `orchestrator.py:175` |
| 2 | Intent — what the message is about, and a crisis score | `app/pipeline/intent.py` → `IntentClassifier.classify` (one model call) | `orchestrator.py:180` |
| 3 | **Safety gate** — stop here on crisis, before anything else | `app/pipeline/safety.py` → `SafetyGate.check_input`; short-circuit via `_crisis_short_circuit` (`orchestrator.py:328`) | `orchestrator.py:186` |
| 4 | Memory — rolling summary + last turns | `app/pipeline/memory.py` → `MemoryStore.load` | `orchestrator.py:218` |
| 4½ | Router — greeting / thanks / meta / follow-up / guidance | `app/pipeline/router.py` → `ConversationRouter.route` (lexicon first, then a cheap model); `tiers.py` → `tier_for` picks the word budget | `orchestrator.py:~230` |
| 5 | Retrieval — verses, only for guidance/follow-up | `app/pipeline/retrieval.py` → `retrieve` (in-memory hybrid, or Neo4j hybrid+graph when configured) | `orchestrator.py:233` |
| 6 | Generation — the reply, grounded in the retrieved verses | `app/pipeline/generation.py` → `generate` / `generate_conversational` | `orchestrator.py:252` |
| 7 | Output gate — checks the reply too (runs on greetings as well) | `app/pipeline/safety.py` → `SafetyGate.check_output`; `generation.assess_answer` word-salad guard | `orchestrator.py:266` |
| 8 | Persist + refresh the summary | `app/db/repository.py` → `add_message`; `memory.maybe_update_summary` | `orchestrator.py:278` |

### 3.3 Feature inventory

Status vocabulary: **live-in-prod** = shipped code, on by default or by the
documented production settings; **built-undeployed** = in the repo, needs a
setting or an operator action the repo cannot confirm; **built-dormant** =
present but off by design; **planned** = not in the repo.

> **Honesty note on "live-in-prod".** Production is now one EC2 VM whose
> running commit is **not recorded anywhere in this repository**: `/health`
> reports `version: "0.1.0"` and the backends in use, not a git hash
> (`app/api/schemas.py:201`). To learn what is deployed, run on the VM:
> `git -C ~/gitaguide rev-parse --short HEAD` and
> `docker compose --env-file .env.vm -f docker-compose.vm.yml ps`. Until then
> every "live-in-prod" row means "live if the VM runs `b8026b6`, and present in
> every commit since the `since` column".

| feature | status | where | since |
| --- | --- | --- | --- |
| Crisis gate (rule layer + semantic score, input and output) | live-in-prod | `app/pipeline/safety.py`; `eval/test_safety_redteam.py` (CI gate) | `c05db53` 2026-08-24 |
| Boot self-tests (gate must block/allow canonical phrases; budget floor; lexicon loaded) | live-in-prod | `app/pipeline/gate_selftest.py`; called from `app/main.py` | `c0b28ef` 2026-09-06; lexicon check `a3f25e2` |
| Intent router + lexicon (7 routes, whole-message match, 224 phrases) | live-in-prod (`GITAGUIDE_ROUTER_MODEL_ENABLED=true` in template) | `app/pipeline/router.py`, `data/intent_lexicon.yaml` | `260ec99` 2026-09-13 |
| Word-budget tiers per route (+ per-tier output-guard floor) | live-in-prod | `app/pipeline/tiers.py` | `260ec99` 2026-09-13 |
| Query rewrite for the vector leg (Hinglish → English) | live-in-prod (`GITAGUIDE_QUERY_REWRITE=on`) | `app/pipeline/router.py` `vector_query`; `retrieval.py` | `59305bc` lineage, 2026-09-14 |
| Multi-turn memory (last 6 turns + rolling summary every 8) | live-in-prod | `app/pipeline/memory.py` | `c05db53` 2026-08-24 |
| Retrieval, in-memory hybrid (fallback / mock) | live-in-prod as fallback | `app/pipeline/retrieval.py` `InMemoryHybridRetriever` | `c05db53` |
| Retrieval, Neo4j hybrid + graph expansion | live-in-prod **if** `GITAGUIDE_USE_REAL_NEO4J=true` and Aura reachable (`/health` → `retrieval_backend`) | `app/pipeline/retrieval.py` Neo4j path; `kg/cypher_queries.py` | `c05db53` lineage; Aura import per `docs/DATA_MIGRATION.md` |
| Dense retrieval via NIM embeddings (Phase 1b Step B) | **planned** — blocked on Neo4j access; named encoder `nvidia/nemotron-3-embed-1b` not yet adopted | — | — |
| Citations with vedabase deep links; no verbatim śloka in replies | live-in-prod | `app/pipeline/references.py`; `app/api/beta.py` `_citations_for` | `936b26c` 2026-09-09 |
| Consent gate, server-enforced (409 without consent at `beta-1`) | live-in-prod | `app/api/beta.py` `_require_consent`; `GITAGUIDE_CONSENT_REQUIRED=true` | `4c42354` 2026-09-06 |
| Rate limiter (per-session, per-IP, global token buckets; one trusted proxy hop) | live-in-prod on `/api/chat` only | `app/api/ratelimit.py`; `.env.vm.example` values | `ff151e7` 2026-09-06 |
| Serialised turns (one at a time, 30 s queue) | live-in-prod | `app/api/concurrency.py`; `GITAGUIDE_PIPELINE_SERIALISE=true` | `3839eef` 2026-09-07 |
| Telemetry counters (per-turn JSON line, hashed session, no text; `/health` daily counters) | live-in-prod | `app/telemetry.py` | `9154dac` 2026-09-06 |
| Feedback votes (👍/👎 + ≤500-char comment per assistant turn) | live-in-prod | `app/api/beta.py` `/api/feedback` | `37cd3f9` 2026-09-06 |
| Prod smoke test | built — run by hand after deploys | `scripts/prod_smoke.py` | `f79d3e8` 2026-09-06 |
| Image-copy-coverage test (every `PROJECT_ROOT/` dir the code reads has a `COPY`) | live (test) | `tests/test_deploy_config.py` `test_the_image_copies_every_directory_the_code_reads` | `a3f25e2` 2026-09-16 |
| **Accounts (Clerk), optional** — sign-in, claim, list, 403 on others' claimed chats | **built-undeployed**: off unless `GITAGUIDE_CLERK_ISSUER` (VM) and `VITE_CLERK_PUBLISHABLE_KEY` (Vercel) are set. Whether they are set is **UNKNOWN**; `/health` does not report it. | `app/auth.py`, `app/api/beta.py`, `frontend/src/auth.js`, `AuthBridge.jsx`, `Sidebar.jsx` | `7d37984` 2026-09-17 |
| Chat sidebar, rename/delete, edit-and-resend, regenerate (frontend) | live-in-prod (Vercel builds `master`) | `frontend/src/components/Sidebar.jsx`, `UserTurn.jsx`, `App.jsx` | `7d37984` 2026-09-17 |
| Aura keep-alive (daily read-only query) | **built-undeployed**: needs a cron entry on the VM (`scripts/keepalive_neo4j.py:9`); no workflow, no compose service, not in the runbook. Whether cron was added is **UNKNOWN**. | `scripts/keepalive_neo4j.py` | `2f1fddf` 2026-09-19 |
| Render keep-warm ping | **removed** | was `.github/workflows/keepwarm.yml` | deleted `984bd96` |
| Corpus provenance fields per verse (Phase 2) | planned | — | — |
| Prabhupāda translation switch | **blocked** — legal preconditions not met (no `docs/legal/`) | — | — |

### 3.4 Corpus

Unchanged since the 19 Sep inventory; nothing under `corpus/` or `data/` was
touched by the 17 commits.

| file | tracked? | records | translator / edition | provenance fields |
| --- | --- | --- | --- | --- |
| `corpus/data/gita.json` | no (built locally, 664 KB) | **701** verses, 18 chapters | Shri Purohit Swami, via the `gita/gita` dataset — attribution reproduced 701/701 with 0 text mismatches; wording shows a later editorial revision (mixed archaic/modern register, one literal `"Revised:"` prefix) | **none** |
| `corpus/data/arthashastra.json` | no (built locally, 2.3 MB) | **3,135** sutras, 15 books | R. Shamasastry 1915, public domain — very high confidence (2,529 acute-accent diacritics, "Kautilya" ×86, Olivelle's "Kauṭalya" ×0) | **none** |
| `corpus/data/sample_corpus.json` | **yes** | 16 (11 BG + 5 AS) | **unattributed** — matches neither translation on any verse; hand-written paraphrases with commentary | none |
| `data/arthashastra_raw/book_*.txt` | yes | 15 files | Wikisource transcription of Shamasastry | no header |

**Known defects (all pre-existing, none fixed):**

- **149 of 150 chapters.** Wikisource merges Arthashastra Book 1 "CHAPTER I &
  II" on one page, so Chapter 2 is missing and `AS-1.1.x` holds both chapters'
  content. Citations to Book 1 Ch. 1 may not match a printed edition.
- **`sample_corpus.json` attribution.** The only scripture text in git is a
  paraphrase of unstated origin; it serves users in mock mode and needs its
  own licence line before the repo goes public.
- **`commentary` is empty** on 0/701 and 0/3,135 built records but populated
  16/16 in the sample — the fulltext index behaves differently in mock mode.
- Two upstream translators (Adidevananda, Gambirananda) are not in
  `PREFERRED_AUTHORS` and would silently win a verse if Purohit ever lacked one
  (`corpus/load_gita.py`). Never triggered.

### 3.5 Config surface

The code reads **88 settings**, all `GITAGUIDE_`-prefixed
(`app/config.py`, `env_prefix="GITAGUIDE_"`), plus `NVIDIA_API_KEY` (an alias
for `GITAGUIDE_NVIDIA_API_KEY`), `PORT` and `MALLOC_ARENA_MAX` (Dockerfile). The
frontend reads four `VITE_*` variables at **build** time.

**Where they are set now that `render.yaml` is gone:** the VM's `.env.vm`
file (gitignored), copied from `.env.vm.example`. `docker-compose.vm.yml`
reads it twice — `--env-file .env.vm` for its own substitutions
(`API_DOMAIN`) and `env_file: .env.vm` to pass everything to the API container.
Frontend variables live in the Vercel project's dashboard.

**The 37 keys the template sets** (`.env.vm.example`), grouped:

| group | variable | template value / meaning |
| --- | --- | --- |
| VM | `API_DOMAIN` | hostname Caddy gets a certificate for (`REPLACE_ME.sslip.io`) |
| proxy | `GITAGUIDE_RATE_LIMIT_TRUSTED_PROXY_HOPS` | `1` — Caddy is the one proxy; wrong value breaks per-IP limiting |
| CORS | `GITAGUIDE_CORS_ORIGINS` | `["https://gitaguide-neon.vercel.app"]` — must be a JSON list |
| **secrets** | `NVIDIA_API_KEY` | language-model API key |
| | `GITAGUIDE_POSTGRES_DSN` | Supabase **session** pooler, port 5432, `+asyncpg`, no `sslmode` |
| | `GITAGUIDE_NEO4J_URI` / `_USERNAME` / `_DATABASE` / `_PASSWORD` | AuraDB; on Aura Free username and database are the instance id |
| | `GITAGUIDE_TELEMETRY_SESSION_SALT` | pseudonymises session ids in telemetry (§5b) |
| optional | `GITAGUIDE_CLERK_ISSUER` | commented out — accounts off |
| mode | `GITAGUIDE_ENVIRONMENT`=`prod`, `GITAGUIDE_LOG_LEVEL`=`INFO`, `GITAGUIDE_MOCK_MODE`=`false` | `true` would serve canned answers while `/health` looks fine |
| models | `GITAGUIDE_EMBEDDER_PRECISION`=`int8` | must match the graph's vectors (INT8 in Aura) |
| | `GITAGUIDE_ONNX_INTRA_OP_THREADS`=`1` | |
| | `GITAGUIDE_PIPELINE_SERIALISE`=`true`, `GITAGUIDE_PIPELINE_QUEUE_TIMEOUT_SECONDS`=`30` | one turn at a time |
| backends | `GITAGUIDE_USE_REAL_NEO4J`=`true`, `GITAGUIDE_LLM_PROVIDER`=`nvidia` | |
| limits | `GITAGUIDE_RATE_LIMIT_ENABLED`=`true`; session 5/min burst 3; IP 20/min burst 8; global 16/min burst 6 | sized to the ~40 req/min NIM free tier |
| | `GITAGUIDE_GENERATION_BUDGET_SECONDS`=`45` | must be ≥ 15 or the app refuses to boot |
| router | `GITAGUIDE_ROUTER_MODEL_ENABLED`=`true`, `GITAGUIDE_ROUTER_MODEL`=`nvidia/nemotron-3.5-lightning-30b-a3b`, `GITAGUIDE_ROUTER_TIMEOUT_SECONDS`=`6`, `GITAGUIDE_ROUTER_MAX_TOKENS`=`160`, `GITAGUIDE_QUERY_REWRITE`=`on` | pinned to the code defaults by `tests/test_deploy_config.py` |
| consent/telemetry | `GITAGUIDE_CONSENT_REQUIRED`=`true`, `GITAGUIDE_CONSENT_VERSION`=`beta-1`, `GITAGUIDE_TELEMETRY_ENABLED`=`true`, `GITAGUIDE_TELEMETRY_PATH`=`/app/logs/telemetry.jsonl` | |

**The other 53 settings fall to code defaults** and are not in the template.
None is a secret or a deployment-shape decision; they are model ids, retrieval
weights, memory sizes, safety thresholds, provider retry policy, and the
Anthropic/OpenAI provider keys that this deployment does not use. Full list:
`python -c "from app.config import Settings; print(sorted(Settings.model_fields))"`.

**Frontend (Vercel dashboard):** `VITE_API_BASE` (required; the API origin
**plus `/api`**), `VITE_SHOW_META` (`1`), `VITE_CLERK_PUBLISHABLE_KEY`
(optional), `VITE_MOCK` (must be absent).

---

## 4. Health & risks

### 4.1 Test suite shape

907 collected on `b8026b6`; 900 pass, 7 skip. By area (counts from the JUnit
report):

| area | files | tests |
| --- | --- | --- |
| Conversational router, tiers, routed pipeline | `test_router` 122, `test_tiers` 37, `test_routed_pipeline` 45 | 204 |
| Safety (unit + red-team) | `test_safety` 51, `eval/test_safety_redteam` 73, `test_output_guard` 24, `test_gate_selftest` 15 | 163 |
| Generation & response contract | `test_generation` 35, `test_response_contract` 35, `test_generation_budget` 7, `test_register` 66 | 143 |
| LLM providers | `test_nvidia_provider` 56 | 56 |
| Retrieval, graph, embeddings | `test_retrieval` 21, `test_neo4j_retrieval` 22, `test_graph_manifest` 18, `test_graph_roundtrip` 6, `test_embeddings` 12, `test_gold_set` 13, `test_torch_free_runtime` 2 | 94 |
| API surface (beta + research) | `test_beta_api` 15, `test_api` 12, `test_consent` 8, `test_feedback` 7, `test_ratelimit` 17, `test_pipeline_gate` 12, `test_telemetry` 12 | 83 |
| **Accounts (new)** | `test_accounts` 10, `test_auth` 11 | 21 |
| Pipeline core | `test_orchestrator` 28, `test_emotion` 23, `test_intent` 9, `test_memory` 10 | 70 |
| Deployment & docs consistency | `test_deploy_config` 25, `test_tester_pack` 13, `test_frontend_reliability` 22, `test_frontend_routing` 8, `test_keepalive_neo4j` 5 | 73 |
| Frontend (`node --test`) | `session.test.js` | 13 |

**The 7 skips**, same as at baseline:

- `test_embeddings.py:63` — downloads real model weights; opt in with
  `GITAGUIDE_RUN_PARITY=1`.
- `test_graph_roundtrip.py` ×6 (lines 219–332) — need a Docker Neo4j; opt in
  with `GITAGUIDE_RUN_GRAPH_ROUNDTRIP=1`.

**What the recent merges left untested:**

- `AuthBridge.jsx` and the sign-in/claim/merge flow in `App.jsx` — no React
  test harness exists; only the pure `session.js` registry is tested.
- `deploy/vm/Caddyfile` — no test reads it (compose is checked for
  `command:`/`replicas:`; the Caddyfile is not).
- `ADDITIVE_INDEXES` in `app/db/session.py` — the boot-time index creation
  has no test (the column addition path is exercised indirectly).
- The three `test_embeddings.py` tests that now `importorskip("onnxruntime")`
  still run here (onnxruntime is installed locally) but **never run in CI**.

### 4.2 Top 5 risks right now

| # | risk | evidence | one-line mitigation |
| --- | --- | --- | --- |
| 1 | **Any conversation is readable by anyone who has its id, and one endpoint hands ids out.** `GET /safety/logs` lists `session_id`+`message_id` of every crisis-flagged turn, unauthenticated; `GET /explain/{id}` returns the verbatim message; `GET /api/sessions/{id}/messages` returns the whole chat for any *unclaimed* session. All reachable through Caddy. | `app/api/routes.py:122`, `:73`; `app/api/beta.py` `_ensure_session_access` only protects claimed sessions; `tests/test_accounts.py:214` codifies the anonymous case. Demonstrated end-to-end locally on 19 Sep. | Remove `/safety/logs` and `/explain` from the served app (or gate them behind an admin token); ~1 hour. |
| 2 | **`POST /chat` (research surface) bypasses consent and rate limiting** and returns the full trace including `memory_summary`. 8 back-to-back calls all returned 200 locally where `/api/chat` 429s after 3. | `app/api/routes.py:47` has no `_require_consent` / `_enforce_rate_limit`; `main.py` mounts it in production. | Don't mount `routes.router`'s `/chat` when `environment == "prod"`, or apply both guards; ~1 hour. |
| 3 | **Production is not reproducible from the repo.** No record of the deployed commit; deploy is `git pull && docker compose up --build` over SSH by whoever has the key; `/health` reports `0.1.0` for every commit. | `docs/DEPLOY_AWS.md` "Day to day"; `app/api/schemas.py:201`. | Bake `git rev-parse HEAD` into the image as a build arg and expose it in `/health`; 30 min. |
| 4 | **Seven production secrets now live only in one file on one VM** (`.env.vm`), with no documented backup, and the telemetry salt has a known public default if left unset. | `.env.vm.example:42-61`; `app/config.py:292` default `"gitaguide-beta"`; no validation rejects `REPLACE_ME`. | Keep an encrypted copy of `.env.vm` off the VM; make the app refuse to boot on `REPLACE_ME` or the default salt in `prod`; 30 min. |
| 5 | **Aura Free pauses after 3 idle days and nothing restarts it.** The keep-alive script exists but nothing schedules it; the Render-era ping is gone. A paused graph silently degrades retrieval to in-memory. | `scripts/keepalive_neo4j.py:9` (cron line in a docstring only); `DEPLOY_AWS.md` "Day to day" mentions the pause but not the script. | Add the cron line to the runbook's step 6 and to the VM; 15 min. |

Also worth knowing, below the top five: `docs/DEPLOY.md:329-330` (step 4b)
tells the operator to set `GITAGUIDE_CLERK_ISSUER` "in Render", which no longer
exists; `docs/DATA_MIGRATION.md:236` still recommends the transaction pooler
on port 6543, which `DEPLOY_AWS.md` and the tests now forbid; the
`app/pipeline/references.py` docstring points at a `docs/legal/` directory that
has never existed.

### 4.3 Anything present that should not be

**Secrets — none found.**

- A credential-shaped sweep over **every blob introduced by the 17 new
  commits** (144 objects): no NVIDIA/OpenAI/Anthropic/Clerk/GitHub/AWS keys, no
  private-key blocks, no JWTs, no DSN or Neo4j URI carrying a real password.
  The only matches are placeholders (`nvapi-REPLACE_ME`, `pk_live_...`,
  `postgres://…REPLACE_ME…`), the documented local-dev DSN
  (`gitaguide:gitaguide@localhost`), and variable *names* in test code.
- Compose, Caddyfile, runbook, `.env.vm.example`, `.env.example`, `DEPLOY.md`,
  `README.md`, `frontend/.env.production.example`: same result, file by file.
- The earlier full-history scan (gitleaks + trufflehog, 19 Sep) was clean up
  to `a3f25e2`; this extends it to `b8026b6`.

**Large binaries — none.** No blob over 200 KB was added by the new commits.

**Important-but-untracked (local working copy only):**

| path | size | why it matters |
| --- | --- | --- |
| `corpus/data/gita.json`, `corpus/data/arthashastra.json` | 664 KB, 2.3 MB | the real corpora; rebuildable from the loaders, but the VM builds an image from a git clone and therefore **does not have them either** — production retrieval reads the graph in Aura, not these files |
| `data/graph_export/` | 68 MB | the Neo4j dump; the only local copy of the graph; excluded from the image by `.dockerignore` |
| `.env` | 937 B | local dev secrets; correctly ignored |
| `data.zip` | 29.9 MB | appeared 19 Sep at 11:27 in the working copy, untracked, origin unknown — not created by this audit; check what it is before it is accidentally committed |
| `logs/` | 1.3 MB | local telemetry; ignored |

---

## 5. Deployment audit — EC2 is now the only backend

### 5a. The deploy procedure, as written

Files: `docker-compose.vm.yml` (`d443b54`), `deploy/vm/Caddyfile` (`d443b54`),
`.env.vm.example` (`d443b54`, `d0521f0`), `docs/DEPLOY_AWS.md` (`d443b54` →
`984bd96`). Introduced over `d443b54`…`984bd96`, 18–19 Sep 2026.

**How a new commit reaches production — manual, over SSH:**

1. An operator SSHes to the instance (`ssh -i key.pem ubuntu@<elastic-ip>`,
   security group allows port 22 from "My IP" only).
2. `cd gitaguide && git pull` — pulls whatever branch the clone is on
   (runbook step 5: `git checkout <the branch you deploy from>`).
3. `docker compose --env-file .env.vm -f docker-compose.vm.yml up -d --build`
   — rebuilds the API image from the `Dockerfile` in the clone and restarts
   `api`; Caddy keeps running.
4. Watch `logs -f api` for the four start-up lines; `curl /health`; run
   `prod_smoke` from a laptop.

**There is no automatic deploy.** `ci.yml` tests and builds; it does not
touch the VM. No webhook, no SSH action, no image registry.

**Which env file:** `.env.vm` in the clone directory on the VM, gitignored,
created by `cp .env.vm.example .env.vm && chmod 600 .env.vm` and edited by
hand. Compose reads it for substitution *and* passes it to the container
(`env_file: .env.vm`). **Editing it requires `up -d`, not `restart`**
(runbook: "restart does not re-read").

### 5b. Env var completeness

Every variable the code *requires* for a working production turn is a slot in
`.env.vm.example` (§3.5 table); the 53 not in the template have safe defaults.
The test suite holds the template to the invariants (`tests/test_deploy_config.py`:
INT8 ⇒ serialised, proxy hops = 1, JSON-list CORS, session pooler DSN, router
values = code defaults).

**The hand-set seven that previously existed only in Render's dashboard.**
They must now be present as real values in the VM's `.env.vm`; the repository
cannot show whether they are:

| variable | consequence if missing/wrong |
| --- | --- |
| `NVIDIA_API_KEY` | every turn falls to the `fake` provider — canned answers with `/health` green (the smoke test's section 1 catches this) |
| `GITAGUIDE_POSTGRES_DSN` | the app falls back to in-memory storage — consent, history and feedback vanish on restart (`/health` → `repository`) |
| `GITAGUIDE_NEO4J_URI`, `_USERNAME`, `_DATABASE`, `_PASSWORD` | retrieval silently drops to in-memory over the 16-verse sample (`/health` → `retrieval_backend: in-memory-hybrid`) |
| `GITAGUIDE_TELEMETRY_SESSION_SALT` | see below |

**The salt.** If it was not copied out of Render before the service was
deleted, **it is unrecoverable** — Render did not export it and nothing in git
ever held it. What a *new* salt breaks: session pseudonyms in telemetry
(`app/telemetry.py` `hash_session`) are `sha256(salt + session_id)`, so
turns written under the old salt cannot be grouped with turns under the new
one. What it does **not** break: PostgreSQL stores raw session ids, not hashes,
so history, consent and feedback are unaffected; and the old telemetry file
lived on Render's ephemeral disk, so unless someone downloaded it, there is
nothing left to correlate with anyway. Two cautions: (1) if the variable is
left as `REPLACE_ME`, that string *is* the salt; (2) if it is removed, the code
default `"gitaguide-beta"` (`app/config.py:292`) is public in this repo, so
pseudonyms would be reversible by anyone with a session id. Set a fresh random
value once and keep it.

### 5c. Data topology

**Same managed services, no self-hosted data.** `docker-compose.vm.yml`
defines exactly two services — `api` and `caddy` — and four volumes (model
cache, API logs, Caddy certificates/config). There is **no Neo4j and no
PostgreSQL container**. The header says so explicitly: *"Neo4j (AuraDB) and
PostgreSQL (Supabase) stay managed and are reached over the internet from the
values in .env.vm."* `DEPLOY.md`'s banner and `README.md:123-125` say the same
Aura instance (3,836 verses / 14,709 edges) and the same Supabase project are
in use.

Two things the repository *cannot* prove, because the template holds
`REPLACE_ME`: that the URI in `.env.vm` is the same Aura instance id as
before, and that the DSN points at the same Supabase project. One thing it
*does* show changed: the Supabase connection moved from the **transaction**
pooler (port 6543, Render era) to the **session** pooler (port 5432), for a
measured reason (prepared statements fail on 6543 under concurrency;
`DEPLOY_AWS.md` step 6, `test_the_database_dsn_template_uses_the_asyncpg_session_pooler`).
Same database, different door.

(The other file, `docker-compose.yml`, *does* run local Postgres and Neo4j
containers with published ports 5432/7474/7687 — it is the **laptop** stack and
is not used on the VM.)

### 5d. Exposure — the published port map

| published by | port | to | purpose |
| --- | --- | --- | --- |
| `caddy` (compose `ports:`) | **80** | internet | Let's Encrypt HTTP-01 challenge; redirect to https |
| `caddy` (compose `ports:`) | **443** | internet | the API, TLS-terminated, `reverse_proxy api:8000` |
| `api` | 8000 | **compose network only** (`expose:`, not `ports:`) | unreachable from outside the VM |
| AWS security group (runbook step 3) | 22 | "My IP" only | SSH |

**Neo4j (7474/7687) and PostgreSQL (5432) are not published — they do not run
here.** Nothing else is published. The runbook explicitly says *"Do not open
8000"*. This part is clean.

**What *is* public through 443, and should not be** (unchanged from the
19 Sep audit): `/docs`, `/redoc`, `/openapi.json` (FastAPI defaults —
advertise the route table), `GET /safety/logs`, `GET /explain/{id}`,
`POST /chat`. None needs to be reachable by testers; the UI calls only
`/api/*`. See §4.2 rows 1–2.

### 5e. Frontend's API target

`frontend/src/api.js:40`:
`const API_BASE = import.meta.env.VITE_API_BASE || '/api'`.

So the backend URL is a **Vercel build-time environment variable**,
`VITE_API_BASE`, inlined into the bundle when Vercel builds `master`. Its
production value is **not in the repository**; read it from the Vercel
dashboard (Project → Settings → Environment Variables). The repository's
*documentation* says it should be `https://13-205-163-41.sslip.io/api`
(`README.md:16`, `frontend/vite.config.js:29`, `scripts/prod_smoke.py:5`), and
`vite.config.js` makes the build **fail** on Vercel if the variable is unset.
Changing it means a redeploy, not a restart.

The old bundle at `gitaguide-livid.vercel.app` (old Vercel account) carried
`https://gitaguide-api.onrender.com/api` — a deleted service. The runbook's
step 8 says to delete that old project; whether it was is UNKNOWN. If it is
still live, anyone with the old link gets a UI whose every call fails.

**Which backend real users hit today, as far as the repo shows:** the EC2
host, *if* the new Vercel project's `VITE_API_BASE` matches the README. That
is a dashboard fact, not a repository fact.

### 5f. Dead config — recommendations only

Most of the cleanup already happened in `984bd96`:

| item | state at `b8026b6` | recommendation |
| --- | --- | --- |
| `render.yaml` | **deleted** | nothing to do (in history for rollback) |
| `.github/workflows/keepwarm.yml` | **deleted** | nothing to do |
| `.env.production.example` | **deleted** | nothing to do |
| "blueprint-pins-defaults" test (`test_the_declared_router_values_match_the_code_defaults`) | **already migrated** — asserts against `.env.vm.example` | keep |
| `tests/test_tester_pack.py` consent-version test | already migrated to `.env.vm.example` | keep |
| `docs/DEPLOY.md` steps 3, 5, 6 (Render console steps) | kept, with a banner calling them historical | keep as history, **but fix `:329-330`**: step 4b must say "`.env.vm` on the VM", not "Render env var" |
| `docs/DATA_MIGRATION.md:122, 236` | still says "in Render" and "use the pooler on port 6543" | one-line corrections; the 6543 advice now contradicts the tests |
| `app/config.py:316`, `app/api/concurrency.py:8`, `app/pipeline/gate_selftest.py:113` comments | mention Render as historical context | leave; they describe measurements that were real |
| README "MEASURED MEMORY" section | describes the 512 MB Render shape with a banner | leave; the INT8/serialise decisions it justifies still apply |

Nothing needs to *migrate* to the compose story that has not already: the
compose file, the template and the tests are the complete replacement.

### 5g. Prod smoke against an arbitrary base URL

`scripts/prod_smoke.py` takes `base_url` as its only positional argument
(`:471`) and derives every request from it. **It works unchanged against the
EC2 host** (`python -m scripts.prod_smoke https://<host>`); the 17 commits
changed only its example URL and one log-message wording.

What it checks, and any platform assumption:

| section | what it asserts | Render-specific? |
| --- | --- | --- |
| 1 `/health` | 200; `mock_mode` false (twice); `retrieval_backend` is Neo4j; `repository` starts `postgresql`; `database_ok`; LLM provider real; telemetry counters and rate-limit snapshot present | no — config facts |
| 2 self-test evidence | reachable ⇒ boot self-tests passed | no |
| 3 consent | 409 without consent, then accepted at `beta-1` | no |
| 4–6 control / greeting / blocked turns | a real answer with citations; a greeting with none; the crisis phrase blocked with the helpline text | no |
| 8 rate limiter | 7 **concurrent** `/api/chat` requests; expects ≥1 `429` with `Retry-After` | no, but depends on `GITAGUIDE_RATE_LIMIT_TRUSTED_PROXY_HOPS=1` behind Caddy — with `0`, every request keys on Caddy's address and the burst still limits (as one client), so the check passes; it would not detect a wrong hop count |
| `--timeout 120` default | sized for Render cold starts | harmless on EC2 (no cold start) |

One thing it does **not** check that this audit would add: that `/health` on
the target reports the expected commit — because `/health` has no commit
field (§4.2 row 3).

### 5h. Verdict — and how much ports to Oracle Always Free

**Production-fit for the closed beta, as written: yes, with two conditions
the repository cannot verify.** Env completeness is enforced by tests on the
*template* but only an operator can confirm the *real* file (§5b). Data
continuity is by design — same Aura, same Supabase — but the instance
identity is a `REPLACE_ME`, so "same" is a runbook promise, not a repo fact
(§5c). Exposure is clean at the network layer (only 80/443; no databases on
the box) and unchanged at the application layer (the audit's three open
endpoints). Deploy repeatability is the weak point: manual SSH, no record of
the deployed commit, no rollback procedure beyond `git checkout` and rebuild.

**Porting to Oracle Always Free (the standing target).** The compose file
already declares itself for "any single Linux VM (AWS EC2, Oracle, ...)", and
that is honest: `docker-compose.vm.yml`, the `Caddyfile` and `.env.vm.example`
are cloud-agnostic and port **as-is**. What changes is in the runbook, not the
stack:

- **Instance shape is the real question.** Oracle's Always Free x86 shape
  (`VM.Standard.E2.1.Micro`) has 1 GB RAM — the runbook's "t3.micro, works
  tightly, swap required" case, not the recommended 2 GB. The generous
  Always Free shape (Ampere A1, up to 4 OCPU / 24 GB) is **ARM**, and the
  runbook says *avoid ARM*: the INT8 encoder file `model_quint8_avx2.onnx` is
  built for x86 AVX2. Using A1 would mean serving with the fp32 encoder *and*
  re-embedding + re-importing the graph as fp32 (`scripts/reembed_graph.py`) —
  a data change, not a config change — or validating the INT8 file on ARM
  first. This is the decision to make before anything else.
- **Oracle Ubuntu images ship an in-instance firewall** (iptables rules that
  drop 80/443 by default), unlike EC2 Ubuntu; the runbook's *"no extra firewall
  inside the instance"* line (step 3) is EC2-specific and would need an extra
  `iptables`/`netfilter-persistent` step. Verify on the image you launch.
- Terminology swaps only: security group → VCN security list / NSG; Elastic IP
  → reserved public IP; `ap-south-1` → `ap-mumbai-1` (Oracle has a Mumbai
  region, so the latency argument in step 1 carries over).
- Everything from step 5 onward (swap, Docker, clone, `.env.vm`, compose,
  smoke) is identical.

Rough estimate: ~80% of `DEPLOY_AWS.md` ports verbatim; steps 1–4 need an
Oracle rewrite; the encoder/ARM decision is the one item with engineering
content.

**Three recommended next steps (options; nothing executed):**

1. **Close the two open exposure items before widening the beta** — remove or
   gate `/safety/logs` and `/explain`, and guard or unmount `POST /chat` in
   prod (§4.2 rows 1–2). About two hours including tests; no deploy-shape
   change.
2. **Make the deployed commit knowable** — pass `GIT_SHA` as a Docker build
   arg in `docker-compose.vm.yml`, expose it in `/health`, and have
   `prod_smoke` print it. Then this document's "UNKNOWN" rows become facts.
3. **Decide the Oracle shape** (x86 1 GB micro vs ARM A1 + fp32 re-embed)
   before porting the runbook, and schedule the Aura keep-alive on whichever
   VM is current (§4.2 row 5) — the pause is the one failure that a quiet
   week guarantees.

---

## Appendix — how the numbers were produced

- Worktree: `git worktree add --detach <scratch>/wt-origin-master origin/master`
  (no checkout of the main working copy; local `master` untouched at `a3f25e2`).
- Python: `pip install "PyJWT[crypto]==2.13.0"` (the one dependency not
  already present locally), then `python -m pytest -q -p no:cacheprovider
  --junit-xml=…` under the offline environment `tests/conftest.py` pins.
  Count taken from the JUnit XML (`tests=907 failures=0 errors=0 skipped=7`).
- Lint: `ruff check .` and `ruff format --check .` on both `a3f25e2` (main
  working copy) and `b8026b6` (worktree).
- Frontend: `npm install --no-audit --no-fund`, `npm test`,
  `VITE_API_BASE=https://placeholder.invalid/api npm run build`.
- History: `git log a3f25e2..origin/master`, `git show --stat`, per-commit
  diffs read for every commit in §1.4; `git merge-base --is-ancestor`.
- Secrets: a project-specific pattern sweep over `git rev-list --objects
  a3f25e2..origin/master` (144 blobs), values masked before printing.
- GitHub: `api.github.com/repos/shastrai2218/gitaguide/pulls` → 404; browser
  → "Page not found". No token was used or requested.
- Nothing in `docs/kg_measurements.md` (§13/§14) was read for numbers or
  changed; it is byte-identical between `a3f25e2` and `b8026b6`.
