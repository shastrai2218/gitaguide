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
| `01_constraints.cypher` | constraints + vector index (384-dim, cosine) + full-text index |
| `02_nodes.cypher` | 3,836 `:Verse` nodes with embeddings — **~33 MB** |
| `03_relationships.cypher` | label/Scripture nodes and 14,709 edges — ~812 KB |
| `manifest.json` | the counts to verify the import against |

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

### Import into Aura

**Do not paste `02_nodes.cypher` into the browser console.** It is ~33 MB; the
Aura query editor will hang or truncate it. Use `cypher-shell`, which streams:

```bash
cypher-shell -a "neo4j+s://<your-instance>.databases.neo4j.io" -u neo4j \
  --file 01_constraints.cypher
```

Then `02_nodes.cypher`, then `03_relationships.cypher`, **in that order**.
Constraints first, or every node gets scanned twice. Nodes before
relationships, or the `MATCH` clauses find nothing and edges are silently
skipped — the statements succeed, they just match zero rows.

`cypher-shell` will prompt for the password. Do not put it on the command line:
it lands in your shell history.

The node import takes a few minutes on Aura Free. Verify afterwards:

```cypher
MATCH (v:Verse) RETURN count(v) AS verses,
       count(v.embedding) AS embedded;
CALL db.relationshipTypes() YIELD relationshipType
CALL { WITH relationshipType
  MATCH ()-[r]->() WHERE type(r) = relationshipType RETURN count(r) AS edges }
RETURN relationshipType, edges ORDER BY relationshipType;
SHOW INDEXES YIELD name, type, state WHERE type IN ['VECTOR','FULLTEXT']
RETURN name, type, state;
```

Three things must be true before moving on:

1. `verses = 3836` **and** `embedded = 3836`. A verse without an embedding is
   invisible to the vector channel and nothing reports it.
2. The five relationship counts match the table above.
3. Both indexes exist and are **`ONLINE`**. A vector index still `POPULATING`
   returns poor neighbours rather than an error.

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
GITAGUIDE_POSTGRES_DSN=postgresql+asyncpg://postgres.abcdefghijklm:YOURPASSWORD@aws-0-ap-south-1.pooler.supabase.com:6543/postgres
```

**Use the pooler on port 6543, not direct on 5432.** Render's instance opens
several connections and reconnects on every deploy; the direct connection limit
on Supabase's free tier is small enough that this matters.

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
