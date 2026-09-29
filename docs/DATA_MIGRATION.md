# Data migration — local stack to managed services

Two stores move: the knowledge graph to **Neo4j AuraDB**, and the session /
message / safety-log tables to **Supabase PostgreSQL**. They are very different
jobs. The graph is a build artefact that must be copied *exactly*; the relational
schema creates itself on first boot and needs no data at all.

Console steps and their order live in [DEPLOY.md](DEPLOY.md). This page is the
detail behind them.

---

## 1. Knowledge graph → AuraDB

### Why export rather than rebuild against Aura

Rebuilding would re-embed 3,836 verses and re-derive every `RELATED_TO` edge.
The embeddings are deterministic, but a rebuild also re-runs the tagger and the
hub-suppression pass — and **every figure in `kg_measurements.md` was measured
against the graph as it exists locally**. Exporting moves that exact artefact,
so production is the deployment the evidence describes. A rebuild would be a
different graph that happens to look similar.

### Export

```bash
python -m scripts.export_neo4j --out data/graph_export
```

Read-only against the source; it prints the host but never a credential. Expect:

| file | contents |
| --- | --- |
| `verses.jsonl` | 3,836 verses, one per line, embeddings included — **~33 MB** |
| `relationships.json` | label/Scripture nodes and 14,709 edges |
| `manifest.json` | the counts, **the encoder that wrote the vectors**, and the export time — the import is verified against all three |

### The manifest says which encoder wrote the vectors

The stored vectors and the serving encoder must be the same encoder. An INT8
query against fp32-built vectors does not error; it returns worse neighbours
(0.986 min cosine, measured — `kg_measurements.md` section 12). On 8 Sep 2026
`data/graph_export` held an fp32 export while the local graph had been
re-embedded to INT8 and production was configured for INT8, and **every count
in the manifest matched**. Only re-embedding a sample and comparing cosines told
the two apart.

So the exporter now makes that measurement itself, **before writing any file**:
it re-embeds twelve verses (evenly spaced through the id order, so both
scriptures are covered) with the encoder `GITAGUIDE_EMBEDDER_PRECISION` selects
and requires the stored vectors to agree to cosine ≥ 0.999. The same encoder
gives 1.000000; the other precision of the same MiniLM gives ~0.986. A mismatch
is `PrecisionMismatchError`, exit 1, nothing written — because a manifest that
labels the vectors wrongly is worse than no manifest.

The result is recorded:

```json
"exported_at": "2026-09-07T20:41:12+00:00",
"embedder_precision": "int8",
"embedder_precision_verified": true,
"precision_check": {"encoder": "all-MiniLM-L6-v2-onnx-int8", "sampled": 12,
                    "min_cosine": 1.0, "threshold": 0.999}
```

And the importer **refuses** a manifest whose `embedder_precision` differs from
the `GITAGUIDE_EMBEDDER_PRECISION` of the environment running the import — exit
2, before credentials are read or a connection is made, with a message naming
both sides. So set that variable to what **production** runs (`int8`) before
importing to Aura; the local `.env` should already say so. A manifest with no
`embedder_precision` at all (any export before 8 Sep) is refused too: re-export.

`--skip-precision-check` exists for synthetic graphs whose vectors no encoder
produced (the round-trip test fixture). The manifest then records
`embedder_precision_verified: false` and the importer prints a warning. Never
use it for the production export.

**It writes data, not query text.** An earlier version generated `.cypher`
files by interpolating `json.dumps()` output into `UNWIND [...] AS row`. That is
invalid Cypher — JSON quotes its map keys (`{"verse_id": …}`) where Cypher
requires bare identifiers (`{verse_id: …}`) — so every file was rejected at line
1 by `cypher-shell`. It survived review because the checks were all on the
writer: does it produce a file, does the file contain the right counts. Both
passed. Neither ever executed a statement.

The fix is not more careful escaping. Hand-built query text was the wrong tool:
the importer now sends this data as query *parameters*, where the driver handles
typing and escaping and no amount of unusual content in a verse can change the
shape of a statement.

Expected counts, which should match `manifest.json`:

| relationship | edges |
| --- | ---: |
| `ADDRESSES_EMOTION` | 1,689 |
| `HAS_THEME` | 5,898 |
| `MENTIONS` | 740 |
| `PART_OF` | 3,836 |
| `RELATED_TO` | 2,546 |
| **total** | **14,709** |

The exporter **refuses to write a partial graph**. If the database contains a
relationship type it does not handle, or any handled type exports zero edges, it
raises `ExportIncompleteError` instead of producing a file. This is not
theoretical: the first version of the script guessed the concept relationship
was called `MENTIONS_CONCEPT` when it is actually `MENTIONS`, the query matched
nothing, the export "succeeded", and 740 edges would have gone missing in
production with no error anywhere.

### The Aura Free naming quirk

Every Neo4j tutorial uses `neo4j` as both the username and the database name.
**On AuraDB Free, both are the instance id.** For instance `1c4ad18d`:

| variable | value |
| --- | --- |
| `GITAGUIDE_NEO4J_URI` | `neo4j+s://1c4ad18d.databases.neo4j.io` |
| `GITAGUIDE_NEO4J_USERNAME` | `1c4ad18d` |
| `GITAGUIDE_NEO4J_DATABASE` | `1c4ad18d` |

The app's defaults are `neo4j` for both, so both must be set explicitly — in
your shell for the import, and in `.env.vm` on the API host for the service
(originally in Render's dashboard). A wrong username gives
`Unauthorized`; a wrong database gives `database does not exist`. Neither
mentions Aura's naming, so it is easy to spend a while suspecting the password.

### Import

```bash
export GITAGUIDE_NEO4J_URI=neo4j+s://<instance-id>.databases.neo4j.io
export GITAGUIDE_NEO4J_USERNAME=<instance-id>
export GITAGUIDE_NEO4J_DATABASE=<instance-id>
read -rs GITAGUIDE_NEO4J_PASSWORD && export GITAGUIDE_NEO4J_PASSWORD

python -m scripts.import_neo4j --source data/graph_export
```

The importer reads those four variables and never prompts, so no credential
reaches your shell history. It:

1. refuses, before connecting, a manifest whose `embedder_precision` is not
   the `GITAGUIDE_EMBEDDER_PRECISION` of your environment (see above),
2. applies the constraints and both indexes with `IF NOT EXISTS`,
3. loads verses in parameterised batches of 500, then the relationships,
4. counts what is actually in the target and compares it against
   `manifest.json`, **exiting non-zero and naming every mismatch** if they
   disagree.

**It is safe to re-run.** Every write is a `MERGE` on a stable key, so a second
run repairs a partial import rather than duplicating anything — which is the
recovery when a connection drops 20 MB into a 33 MB upload. `--verify-only`
re-runs just the checks.

Against a local container the whole thing takes about 10 seconds; Aura Free is
slower but still minutes, not tens of minutes.

Three things must be true before moving on, and the importer asserts all three
for you:

1. `verses = 3836` **and** all 3,836 embedded. A verse without an embedding is
   invisible to the vector channel and nothing else reports it.
2. The five relationship counts match the table above.
3. Both indexes exist and are **`ONLINE`**. A vector index still `POPULATING`
   returns poor neighbours rather than an error.

### This path is round-trip tested

`tests/test_graph_roundtrip.py` stands up a throwaway Neo4j container, runs the
real export and the real import against it, and then asks the database what it
holds — including that quotes and Devanagari survive intact, that the vector
index is `ONLINE`, that a second run changes nothing, and that verification
actually fails on a deliberately damaged graph. It is opt-in because it needs
Docker:

```bash
GITAGUIDE_RUN_GRAPH_ROUNDTRIP=1 pytest tests/test_graph_roundtrip.py
```

A test that only inspects the exporter's output is what missed the invalid
Cypher, so this one refuses to look at files.

### Capacity

14,709 relationships against the AuraDB Free cap of 400,000 is **3.7%**. That
headroom is the result of the hub suppression in `kg_measurements.md` section 1
— unsuppressed, the same corpus produces 269,649 `RELATED_TO` edges, which is
67% of the cap on its own.

---

## 2. Relational store → Supabase

### There is nothing to migrate

The local Postgres holds development sessions and test messages. None of it is
worth carrying into a beta, and copying it would mix your own testing into the
data the beta is meant to produce. **Start empty.**

The schema creates itself: `init_models()` runs `Base.metadata.create_all` on
first boot, and then applies `ADDITIVE_COLUMNS` (`app/db/session.py`), which
issues `ALTER TABLE … ADD COLUMN IF NOT EXISTS` for every column added after a
table first shipped — currently `messages.memory`, `messages.timings`,
`sessions.consent_version`, `sessions.consent_at`. Additive only; it never drops
or retypes anything.

So Supabase needs no SQL run by hand. Create the project, take the connection
string, and let the first boot build the schema.

### The connection string, exactly

This is the step most likely to cost you an hour, because Supabase shows a
string that this app cannot use as-is. Two transformations are required.

Supabase gives you, under **Project Settings → Database → Connection string →
Transaction pooler**, something shaped like:

```
postgresql://postgres.abcdefghijklm:[YOUR-PASSWORD]@aws-0-ap-south-1.pooler.supabase.com:6543/postgres
```

**1. Change the driver prefix** to `postgresql+asyncpg://`. SQLAlchemy picks its
driver from this prefix; without it you get the synchronous `psycopg2` path and
an immediate failure about an async engine.

**2. Do not append `?sslmode=require`.** `sslmode` is a libpq parameter.
`asyncpg` does not accept it and will raise
`TypeError: connect() got an unexpected keyword argument 'sslmode'`. Supabase's
pooler requires TLS and asyncpg negotiates it by default, so the correct action
is to leave it off entirely.

The result:

```
GITAGUIDE_POSTGRES_DSN=postgresql+asyncpg://postgres.abcdefghijklm:YOURPASSWORD@aws-0-ap-south-1.pooler.supabase.com:5432/postgres
```

**Use the *session* pooler (`.pooler.supabase.com`, port 5432), not the
transaction pooler on 6543 and not the direct `db.<ref>.supabase.co` host.**
This paragraph originally recommended 6543 for the Render deployment. Measured
on 19 Sep 2026 while moving to EC2: this app's SQLAlchemy + asyncpg engine uses
prepared statements, which the transaction pooler does not support under
concurrent queries, and the direct host is IPv6-only and unreachable from the
VM. `tests/test_deploy_config.py` now holds the template to the session pooler;
see `docs/DEPLOY_AWS.md` step 6.

If your password contains `@`, `/`, `:` or `#`, percent-encode it — those
characters otherwise terminate fields in a URL and the parse failure will not
say so clearly.

### Verify after the first deploy

`GET /health` should report:

```json
"repository": "postgresql+asyncpg", "database_ok": true
```

`repository` is read from the live engine's URL, not hardcoded, so this is
evidence rather than assertion. Anything else — `sqlite+aiosqlite`, or
`in-memory` — means the DSN was not picked up and the deployment is storing
nothing durable while looking healthy.

Then confirm the additive pass ran, in the Supabase SQL editor:

```sql
select column_name from information_schema.columns
where table_name = 'messages' and column_name in ('memory','timings');
select column_name from information_schema.columns
where table_name = 'sessions' and column_name in ('consent_version','consent_at');
```

Four rows. Fewer means the app is running an older image than you think.
