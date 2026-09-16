"""All Cypher used by GitaGuide, in one auditable place.

Research-pipeline role
---------------------
The knowledge graph is the explainability substrate of the project: an answer is
only allowed to cite a verse that retrieval actually reached, and the *path* it
was reached by is shown to the user. Keeping the Cypher here (rather than inline
in Python) means the graph schema and the traversal logic can be quoted directly
in the dissertation and diffed over time.

Graph schema
------------
Nodes
    ``(:Scripture {name, key})``
    ``(:Verse {id, source, chapter, verse, book, text_english, text_sanskrit,
               transliteration, commentary, embedding})``
    ``(:Theme {name})`` ``(:Emotion {name})`` ``(:Concept {name})``
Relationships
    ``(:Verse)-[:PART_OF]->(:Scripture)``
    ``(:Verse)-[:HAS_THEME]->(:Theme)``
    ``(:Verse)-[:ADDRESSES_EMOTION {weight}]->(:Emotion)``
    ``(:Verse)-[:MENTIONS]->(:Concept)``
    ``(:Verse)-[:RELATED_TO {shared, score}]->(:Verse)``
"""

from __future__ import annotations

# Constraints and indexes
CONSTRAINTS: tuple[str, ...] = (
    "CREATE CONSTRAINT verse_id IF NOT EXISTS FOR (v:Verse) REQUIRE v.id IS UNIQUE",
    "CREATE CONSTRAINT theme_name IF NOT EXISTS FOR (t:Theme) REQUIRE t.name IS UNIQUE",
    "CREATE CONSTRAINT emotion_name IF NOT EXISTS FOR (e:Emotion) REQUIRE e.name IS UNIQUE",
    "CREATE CONSTRAINT concept_name IF NOT EXISTS FOR (c:Concept) REQUIRE c.name IS UNIQUE",
    "CREATE CONSTRAINT scripture_key IF NOT EXISTS FOR (s:Scripture) REQUIRE s.key IS UNIQUE",
)

CREATE_VECTOR_INDEX = """
CREATE VECTOR INDEX $index_name IF NOT EXISTS
FOR (v:Verse) ON (v.embedding)
OPTIONS { indexConfig: {
    `vector.dimensions`: $dimensions,
    `vector.similarity_function`: 'cosine'
} }
"""

CREATE_FULLTEXT_INDEX = """
CREATE FULLTEXT INDEX $index_name IF NOT EXISTS
FOR (v:Verse) ON EACH [v.text_english, v.commentary, v.transliteration]
"""

DROP_ALL = "MATCH (n) DETACH DELETE n"

# Ingestion
MERGE_SCRIPTURE = """
MERGE (s:Scripture {key: $key})
SET s.name = $name
RETURN s.key AS key
"""

MERGE_VERSE = """
MATCH (s:Scripture {key: $source})
MERGE (v:Verse {id: $id})
SET v.source          = $source,
    v.chapter         = $chapter,
    v.verse           = $verse,
    v.book            = $book,
    v.citation        = $citation,
    v.text_english    = $text_english,
    v.text_sanskrit   = $text_sanskrit,
    v.transliteration = $transliteration,
    v.commentary      = $commentary,
    v.embedding       = $embedding
MERGE (v)-[:PART_OF]->(s)
WITH v
// Label edges are fully derived from the weak labels, so clear them before
// rewriting. MERGE alone only ever adds: re-tagging a verse (e.g. after a
// translation swap) would otherwise leave every previous label attached, and
// the graph would keep asserting tags the tagger no longer produces.
CALL (v) {
    MATCH (v)-[old:HAS_THEME|ADDRESSES_EMOTION|MENTIONS]->()
    DELETE old
}
CALL (v) {
    UNWIND $themes AS theme
    MERGE (t:Theme {name: theme})
    MERGE (v)-[:HAS_THEME]->(t)
}
CALL (v) {
    UNWIND $emotions AS emotion
    MERGE (e:Emotion {name: emotion})
    MERGE (v)-[r:ADDRESSES_EMOTION]->(e)
    SET r.weight = 1.0
}
CALL (v) {
    UNWIND $concepts AS concept
    MERGE (c:Concept {name: concept})
    MERGE (v)-[:MENTIONS]->(c)
}
RETURN v.id AS id
"""

#: Verses that share at least ``$min_shared`` *non-hub* themes/concepts become
#: siblings.
#:
#: Hub suppression (the ``$max_df`` clause) is IDF thinking applied to the graph:
#: a label carried by a quarter of the corpus ("relationships": 969/3836) says
#: nothing about whether two verses belong together, but it generates a
#: quadratic number of pairs on its own. Without the clause the 3,836-verse
#: corpus derives 269,649 RELATED_TO edges (max degree 1,092); with ``$max_df``
#: at 8% of the corpus it derives 2,546 - suppression removes 99.06% of the
#: derived edges, and every survivor means something.
#: (Purohit labels, measured 2 Sep 2026. These figures move whenever tagging
#: changes - see docs/kg_measurements.md, and re-measure after semantic
#: re-tagging.)
#:
#: ``$max_df`` is *document frequency*, in verses, not a fraction - the caller
#: multiplies :attr:`app.config.Settings.kg_hub_label_max_fraction` by the live
#: verse count so the threshold scales with the corpus.
LINK_RELATED_VERSES = """
MATCH (x)<-[:HAS_THEME|MENTIONS]-(v:Verse)
WHERE x:Theme OR x:Concept
WITH x, count(v) AS df
WHERE df <= $max_df
MATCH (a:Verse)-[:HAS_THEME|MENTIONS]->(x)<-[:HAS_THEME|MENTIONS]-(b:Verse)
WHERE a.id < b.id
WITH a, b, count(DISTINCT x) AS shared
WHERE shared >= $min_shared
MERGE (a)-[r:RELATED_TO]->(b)
SET r.shared = shared, r.score = toFloat(shared) / 5.0
RETURN count(r) AS created
"""

#: Labels excluded by the hub filter, for the build report.
HUB_LABELS = """
MATCH (x)<-[:HAS_THEME|MENTIONS]-(v:Verse)
WHERE x:Theme OR x:Concept
WITH x, count(v) AS df
WHERE df > $max_df
RETURN labels(x)[0] AS label, x.name AS name, df
ORDER BY df DESC
"""

#: A label that lost its last verse (a re-tag can do that - "gunas" went from 44
#: verses to 1 when the translation changed) would otherwise linger as an
#: orphan node and inflate the node counts.
DELETE_ORPHAN_LABELS = """
MATCH (n)
WHERE (n:Theme OR n:Emotion OR n:Concept) AND NOT (n)<-[]-()
DELETE n
RETURN count(n) AS deleted
"""

#: RELATED_TO is fully derived, so a rebuild must clear it first - otherwise
#: yesterday's hub edges survive today's stricter threshold.
DELETE_RELATED_EDGES = """
MATCH ()-[r:RELATED_TO]->()
CALL (r) { DELETE r } IN TRANSACTIONS OF 10000 ROWS
"""

#: Post-build counts. Written as COUNT{} subqueries so that a label with zero
#: nodes reports 0 instead of eliminating the whole row - the chained-MATCH
#: version silently returned nothing when any single label was empty.
GRAPH_STATS = """
RETURN count { MATCH (v:Verse) RETURN v }                   AS verses,
       count { MATCH (t:Theme) RETURN t }                   AS themes,
       count { MATCH (e:Emotion) RETURN e }                 AS emotions,
       count { MATCH (c:Concept) RETURN c }                 AS concepts,
       count { MATCH (:Verse)-[r:RELATED_TO]->(:Verse) RETURN r } AS related_edges,
       count { MATCH (v:Verse) WHERE v.book IS NOT NULL RETURN v } AS verses_with_book
"""

# Retrieval
#: Tail query for ``neo4j_graphrag.retrievers.HybridCypherRetriever``.
#:
#: The package supplies the ``node``/``score`` variables from the fused vector +
#: full-text search; this tail performs the 1-2 hop expansion and returns both
#: the verse payload and the *paths* that justify it. ``$emotions`` is the list
#: of detected emotion labels, which is how affect steers the graph walk.
#:
#: ``$emotions`` is a **required** parameter: Cypher has no defaulting, so a
#: caller that omits it gets ``ParameterMissing``. Every call site must go
#: through :data:`app.pipeline.retrieval.DEFAULT_QUERY_PARAMS`, which supplies an
#: empty list, so the parameter can never be absent.
#:
#: The ``metadata`` column exists so that even the library's *default* record
#: formatter (which does ``content=str(record)``, ``metadata=record.get(
#: "metadata")``) still yields a machine-readable payload instead of a repr
#: string with no metadata. See ``verse_result_formatter``.
HYBRID_RETRIEVAL_QUERY = """
WITH node AS v, score AS search_score
OPTIONAL MATCH (v)-[:ADDRESSES_EMOTION]->(e:Emotion)
WHERE e.name IN $emotions
WITH v, search_score, collect(DISTINCT e.name) AS matched_emotions
OPTIONAL MATCH (v)-[:HAS_THEME]->(t:Theme)
WITH v, search_score, matched_emotions, collect(DISTINCT t.name) AS themes
OPTIONAL MATCH (v)-[:RELATED_TO]-(sibling:Verse)-[:ADDRESSES_EMOTION]->(se:Emotion)
WHERE se.name IN $emotions
WITH v, search_score, matched_emotions, themes,
     collect(DISTINCT {id: sibling.id, via: se.name})[..3] AS related_via_emotion
RETURN
    v.id              AS id,
    v.source          AS source,
    v.chapter         AS chapter,
    v.verse           AS verse,
    v.book            AS book,
    v.text_english    AS text_english,
    v.text_sanskrit   AS text_sanskrit,
    v.transliteration AS transliteration,
    v.commentary      AS commentary,
    v.citation        AS citation,
    themes            AS themes,
    matched_emotions  AS matched_emotions,
    related_via_emotion AS related_via_emotion,
    search_score      AS search_score,
    {
        id: v.id,
        source: v.source,
        chapter: v.chapter,
        verse: v.verse,
        book: v.book,
        text_english: v.text_english,
        text_sanskrit: v.text_sanskrit,
        transliteration: v.transliteration,
        commentary: v.commentary,
        citation: v.citation,
        themes: themes,
        matched_emotions: matched_emotions,
        related_via_emotion: related_via_emotion,
        search_score: search_score
    }                 AS metadata
ORDER BY search_score DESC
"""

#: Driver-level fallback: the whole hybrid search in one statement.
#:
#: Used when the installed ``neo4j-graphrag`` cannot parameterise the retrieval
#: query (no ``query_params`` on ``search()``). It reproduces the package's
#: "naive" hybrid ranker - query each index for ``$top_k``, normalise each
#: channel by its own maximum, then take the per-node maximum - and then runs
#: the identical tail, so results stay comparable across both paths.
MANUAL_HYBRID_QUERY = """
CALL {
    CALL db.index.vector.queryNodes($vector_index_name, $top_k, $query_vector)
    YIELD node, score
    WITH collect({node: node, score: score}) AS hits
    WITH hits, reduce(m = 0.0, h IN hits | CASE WHEN h.score > m THEN h.score ELSE m END) AS top
    UNWIND hits AS hit
    RETURN hit.node AS node,
           CASE WHEN top > 0 THEN hit.score / top ELSE 0.0 END AS score
    UNION
    CALL db.index.fulltext.queryNodes($fulltext_index_name, $query_text, {limit: $top_k})
    YIELD node, score
    WITH collect({node: node, score: score}) AS hits
    WITH hits, reduce(m = 0.0, h IN hits | CASE WHEN h.score > m THEN h.score ELSE m END) AS top
    UNWIND hits AS hit
    RETURN hit.node AS node,
           CASE WHEN top > 0 THEN hit.score / top ELSE 0.0 END AS score
}
WITH node, max(score) AS score
ORDER BY score DESC
LIMIT $top_k
"""

#: Pure-graph channel: verses reachable from the detected emotions, used to
#: rescue relevant verses that the vector index ranked poorly.
EMOTION_EXPANSION_QUERY = """
UNWIND $emotions AS emotion_name
MATCH (e:Emotion {name: emotion_name})<-[r:ADDRESSES_EMOTION]-(v:Verse)
WITH v, emotion_name, coalesce(r.weight, 1.0) AS weight
RETURN v.id AS id, collect({emotion: emotion_name, weight: weight}) AS matches
LIMIT $limit
"""

#: Two-hop theme walk from a set of seed verses.
THEME_EXPANSION_QUERY = """
UNWIND $seed_ids AS seed_id
MATCH (seed:Verse {id: seed_id})-[:HAS_THEME]->(t:Theme)<-[:HAS_THEME]-(v:Verse)
WHERE v.id <> seed.id
RETURN v.id AS id, seed.id AS seed, collect(DISTINCT t.name)[..3] AS shared_themes
LIMIT $limit
"""

FETCH_VERSES_BY_ID = """
UNWIND $ids AS verse_id
MATCH (v:Verse {id: verse_id})
OPTIONAL MATCH (v)-[:HAS_THEME]->(t:Theme)
OPTIONAL MATCH (v)-[:ADDRESSES_EMOTION]->(e:Emotion)
RETURN v.id AS id, v.source AS source, v.chapter AS chapter, v.verse AS verse,
       v.book AS book, v.text_english AS text_english, v.text_sanskrit AS text_sanskrit,
       v.transliteration AS transliteration, v.commentary AS commentary,
       collect(DISTINCT t.name) AS themes, collect(DISTINCT e.name) AS emotions
"""


def format_index_ddl(template: str, index_name: str, dimensions: int | None = None) -> str:
    """Neo4j forbids parameters in DDL, so substitute the names here.

    Index names are validated against a strict pattern by the caller
    (:mod:`kg.build_graph`), so this stays injection-safe.
    """
    query = template.replace("$index_name", index_name)
    if dimensions is not None:
        query = query.replace("$dimensions", str(int(dimensions)))
    return query
