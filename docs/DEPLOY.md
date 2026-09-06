# Deploying the GitaGuide closed beta

Console steps, in the order they must happen. Each step ends with a check you
can run before moving on, because every one of these services fails in a way
that looks like success from the next step along.

**You do the console work.** Nothing in this repository asks for a credential,
and no automation here logs into anything. Keys and connection strings are
entered by you, in the provider dashboards, and never committed.

**Order matters.** Aura and Supabase must exist before Render, because Render
needs their connection details. Render must exist before Vercel, because Vercel
needs the API origin. And Render needs one variable set *after* Vercel exists —
its own CORS origin — so the last step comes back to Render.

```
  1. AuraDB      -> graph + credentials
  2. Supabase    -> pooled connection string
  3. Render      -> API deployed, smoke-tested
  4. Vercel      -> UI deployed
  5. Render      -> CORS origin, then re-smoke
```

Set aside about 90 minutes. The Aura node import is the long part.

---

## Before you start

On your machine, with the local stack running:

```bash
python -m scripts.export_neo4j --out data/graph_export
```

You should see 3,836 verses and 14,709 relationships across five types. The
exporter refuses to write a partial graph, so if it printed files, it exported
everything. Details and the expected counts: [DATA_MIGRATION.md](DATA_MIGRATION.md).

No `cypher-shell` needed. The import runs through the Neo4j Python driver you
already have installed, sending the graph as query *parameters*.

---

## Step 1 — Neo4j AuraDB

1. <https://console.neo4j.io> → **New Instance** → **AuraDB Free**.
2. Name it `gitaguide-beta`. Choose the region closest to your testers.
3. **Download the credentials file when it is offered.** It is shown once.
4. Wait until the instance shows **Running** — roughly 3 minutes.

> **Aura Free names things after the instance id.** For instance `1c4ad18d`, the
> URI is `neo4j+s://1c4ad18d.databases.neo4j.io` and **both the username and the
> database name are `1c4ad18d`** — not `neo4j`, which is what every tutorial
> shows and what this app defaults to. Read all four values off the credentials
> file rather than assuming any of them.

Set the four connection variables in your shell, then run the importer. It reads
them from the environment; it never prompts for a password and nothing is typed
where your shell history can keep it:

```bash
export GITAGUIDE_NEO4J_URI=neo4j+s://<instance-id>.databases.neo4j.io
export GITAGUIDE_NEO4J_USERNAME=<instance-id>
export GITAGUIDE_NEO4J_DATABASE=<instance-id>
read -rs GITAGUIDE_NEO4J_PASSWORD && export GITAGUIDE_NEO4J_PASSWORD

python -m scripts.import_neo4j --source data/graph_export
```

It applies the constraints and indexes, loads the verses and relationships in
parameterised batches, and then **verifies the target against `manifest.json`**.
Expect it to finish with:

```
  [ok] 3836 verses, all embedded
  [ok] ADDRESSES_EMOTION: 1689
  [ok] HAS_THEME: 5898
  [ok] MENTIONS: 740
  [ok] PART_OF: 3836
  [ok] RELATED_TO: 2546
  [ok] both indexes ONLINE
```

**If it exits non-zero, it names exactly what did not match.** Just run it
again: every write is a `MERGE` on a stable key, so a re-run repairs a partial
import rather than duplicating anything. That is the documented recovery for a
connection dropped mid-upload.

`--verify-only` re-runs the checks without writing, which is the quickest way to
confirm an instance that has been sitting idle is still intact.

---

## Step 2 — Supabase

1. <https://supabase.com/dashboard> → **New project**, named `gitaguide-beta`.
2. Set a database password and save it in your password manager. It is shown
   once and cannot be recovered, only reset.
3. Wait for provisioning — roughly 2 minutes.
4. **Project Settings → Database → Connection string → Transaction pooler.**
   Copy the string. It looks like:

   ```
   postgresql://postgres.abcdefghijklm:[YOUR-PASSWORD]@aws-0-ap-south-1.pooler.supabase.com:6543/postgres
   ```

5. Transform it into what this app needs — **two changes, both required**:
   - `postgresql://` → **`postgresql+asyncpg://`**
   - **do not** append `?sslmode=require`; asyncpg rejects that parameter and
     negotiates TLS on its own

   Percent-encode the password if it contains `@ / : #`.

Nothing needs to be created in Supabase by hand. The schema builds itself on
the API's first boot. Reasoning and the verification queries:
[DATA_MIGRATION.md](DATA_MIGRATION.md#2-relational-store--supabase).

---

## Step 3 — Render (API)

1. <https://dashboard.render.com> → **New** → **Web Service** → connect this
   repository.
2. Render reads `render.yaml`. Confirm it picked up: **Docker** runtime,
   **Starter** plan, health check path **`/health`**, **1** instance.

   Keep Starter. On Free the instance sleeps, and the first request after a
   sleep pays the entire ONNX model load — testers will read that as broken.

   Keep it at one instance. The rate limiter and telemetry counters are
   in-process, so two instances would silently double every limit below.

3. **Environment → add the variables.** `render.yaml` already carries the
   non-secret ones; these are the five it deliberately leaves blank, plus CORS
   which you will fill in at step 5.

   | NAME | Where the value comes from |
   | --- | --- |
   | `NVIDIA_API_KEY` | build.nvidia.com — your key, starts with `nvapi-` |
   | `GITAGUIDE_POSTGRES_DSN` | step 2, after both transformations |
   | `GITAGUIDE_NEO4J_URI` | step 1, the `neo4j+s://…` URI |
   | `GITAGUIDE_NEO4J_PASSWORD` | step 1, from the credentials file |
   | `GITAGUIDE_NEO4J_USERNAME` | step 1 — on Aura Free this is the **instance id**, not `neo4j` |
   | `GITAGUIDE_NEO4J_DATABASE` | step 1 — on Aura Free this is the **instance id** too |
   | `GITAGUIDE_TELEMETRY_SESSION_SALT` | any long random string, set once |
   | `GITAGUIDE_CORS_ORIGINS` | **leave blank for now** — step 5 |

   Confirm these are present and correct, from `render.yaml`:

   | NAME | Value | Why it matters |
   | --- | --- | --- |
   | `GITAGUIDE_MOCK_MODE` | `false` | `true` serves canned answers while `/health` looks fine |
   | `GITAGUIDE_LLM_PROVIDER` | `nvidia` | |
   | `GITAGUIDE_USE_REAL_NEO4J` | `true` | |
   | `GITAGUIDE_CONSENT_REQUIRED` | `true` | off makes the consent record worthless |
   | `GITAGUIDE_RATE_LIMIT_ENABLED` | `true` | |
   | `GITAGUIDE_RATE_LIMIT_SESSION_PER_MINUTE` | `5` | per tester |
   | `GITAGUIDE_RATE_LIMIT_SESSION_BURST` | `3` | |
   | `GITAGUIDE_RATE_LIMIT_IP_PER_MINUTE` | `20` | above session, so an office NAT is not the limit |
   | `GITAGUIDE_RATE_LIMIT_IP_BURST` | `8` | |
   | `GITAGUIDE_RATE_LIMIT_GLOBAL_PER_MINUTE` | `16` | **this is the one protecting the NIM budget** |
   | `GITAGUIDE_RATE_LIMIT_GLOBAL_BURST` | `6` | |
   | `GITAGUIDE_GENERATION_BUDGET_SECONDS` | `45` | must be ≥ 15 or the app refuses to boot |
   | `GITAGUIDE_TELEMETRY_ENABLED` | `true` | |
   | `GITAGUIDE_CONSENT_VERSION` | `v1` | must match the frontend's |

   Full annotated list: [`.env.production.example`](../.env.production.example).

4. **Create Web Service.** The first build takes 5–10 minutes: it installs the
   serving requirements and, on first boot, downloads both ONNX models.

**Check before moving on.** The deploy log should contain, in order:

```
safety gate self-test passed (14 rules armed; crisis blocked, control allowed)
database schema ensured (4 additive column(s) checked)
LLM chain: nvidia:… -> nvidia:… (budget 45s across the whole chain)
rate limits: session=5/min(burst 3) ip=20/min(burst 8) global=16/min(burst 6)
```

Then, from your machine:

```bash
python -m scripts.prod_smoke https://<your-service>.onrender.com
```

Every check must pass. It asserts the things that look fine when they are
wrong: `mock_mode` false, the real ONNX emotion model, `neo4j-hybrid-cypher`
(not the in-memory retriever), `postgresql+asyncpg` (not SQLite), a NIM model
answering, consent refused then accepted, a crisis turn that runs no retrieval
or generation, and the rate limiter engaging.

If the smoke test fails, **stop here**. Common causes:

| Symptom | Cause |
| --- | --- |
| `repository` is `in-memory` or `sqlite+aiosqlite` | the DSN was not picked up — check the `+asyncpg` prefix |
| `TypeError … 'sslmode'` in the logs | `?sslmode=require` is still on the DSN |
| `retrieval_backend` is `in-memory-hybrid` | Neo4j URI or password wrong; the retriever fell back |
| Boot fails, "refusing to start" | a disarmed rule table, or a budget below 15 — the error names which |
| `Unauthorized` from the importer | on Aura Free the username is the instance id, not `neo4j` |
| `database does not exist` | same — `GITAGUIDE_NEO4J_DATABASE` is the instance id too |
| `mock_mode: true` | `GITAGUIDE_MOCK_MODE` is not exactly `false` |

---

## Step 4 — Vercel (frontend)

1. <https://vercel.com/new> → import this repository.
2. **Root Directory → `frontend`.** Vercel then reads `frontend/vercel.json`
   and picks up the Vite framework preset automatically.
3. **Environment Variables** (scope: Production):

   | NAME | Value |
   | --- | --- |
   | `VITE_API_BASE` | `https://<your-service>.onrender.com/api` — note the `/api` suffix |
   | `VITE_SHOW_META` | `1` |

   Do **not** set `VITE_MOCK`. The build fails if it is `1` in production, and
   there is no reason to set it to anything.

4. **Deploy.** About a minute.

**Check before moving on.** If `VITE_API_BASE` is missing, the build *fails*
with an error naming the variable — that guard exists so you never ship a UI
where every turn 404s silently. A successful build means it was set.

Open the URL. You should see the consent gate. Do not send a message yet — CORS
will refuse it until step 5.

---

## Step 5 — Render again (CORS), then verify

1. Back in Render → **Environment** → set:

   | NAME | Value |
   | --- | --- |
   | `GITAGUIDE_CORS_ORIGINS` | `https://<your-project>.vercel.app` |

   Exact origin, no trailing slash. Comma-separate if you add a preview domain.

2. Save. Render restarts the service — about 2 minutes, including the model
   warm-up.

**Final check.** In the browser, on the Vercel URL:

- [ ] the consent gate appears on a fresh visit
- [ ] "I understand — begin" is accepted (this writes the consent row)
- [ ] a workplace message returns a reflection **with verse leaves**
- [ ] the meta line shows a real model name and per-stage latency
- [ ] the thumbs buttons record, showing "Thanks — recorded."
- [ ] `New session` clears the transcript and starts a fresh session
- [ ] no CORS errors in the browser console

And once more from your machine, now that CORS is set:

```bash
python -m scripts.prod_smoke https://<your-service>.onrender.com
```

---

## What to watch during the beta

| Where | What |
| --- | --- |
| `GET /health` → `components.telemetry_today` | `semantic_malformed_final` and `soft_cue_fallback_blocks` — the Day 3 residual risk, per day |
| `GET /health` → `components.rate_limit` | `global_tokens` near zero means testers are queueing on the NIM budget |
| Render logs, `TURN_LATENCY` | per-stage wall clock for any turn a tester says was slow |
| Render logs, `SAFETY_AUDIT` | every gate decision, including near-misses that did not block |
| Supabase → `feedback` | what testers actually thought |
| Supabase → `safety_logs` | the durable audit trail; the telemetry file does not survive a restart |

---

## Known limits of this deployment

Stated here rather than discovered later:

- **One instance, in-process counters.** Rate limits and telemetry counters
  live in memory. They reset on every restart and deploy, and scaling to two
  instances would double every limit. Moving them to shared storage is a
  prerequisite for scaling, not an optimisation.
- **Telemetry is ephemeral.** `/app/logs/telemetry.jsonl` is on Render's
  ephemeral disk. PostgreSQL is the durable record; the JSONL is the
  convenience view. Pull anything you want to keep before a redeploy.
- **AuraDB Free pauses after 3 days idle.** A gap in testing means the first
  request afterwards fails until you resume the instance in the console.
- **The NIM free tier is ~40 requests/minute per model** and one turn spends
  two calls on the primary. That is the real ceiling: about 19 turns/minute for
  everyone combined. The global limit of 16/min sits just under it. With ~20
  testers that is roughly 0.8 turns/minute each. Raising the limit does not buy
  capacity — it buys 429s from NVIDIA instead of from us.
- **Retrieval quality is the open research problem**, not a deployment issue:
  hit rate 0.175 on the 40-query gold set (`kg_measurements.md` section 4).
  Testers will notice verses that do not quite fit. That is the finding, and it
  is what the feedback buttons are for.
