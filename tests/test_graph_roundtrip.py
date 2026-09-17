"""Export → import round trip against a real, throwaway Neo4j.

Why this test exists
--------------------
The previous exporter wrote ``.cypher`` files by interpolating ``json.dumps()``
into ``UNWIND [...] AS row``. JSON quotes its map keys; Cypher requires bare
identifiers. **Every generated file was invalid at line 1**, and it survived
review because the only checks were on the writer: does it produce a file, does
the file contain the expected counts. Both passed. Neither executed a single
statement.

So this test refuses to check the writer. It stands up an actual Neo4j, runs the
real export, runs the real import, and then asks the *database* what it holds.
A statement that does not parse cannot pass this.

Running it
----------
Needs Docker and pulls ``neo4j:5-community`` on first use, so it is opt-in::

    GITAGUIDE_RUN_GRAPH_ROUNDTRIP=1 pytest tests/test_graph_roundtrip.py -m slow

It uses port 7688 to avoid colliding with a local development Neo4j on 7687,
and removes its container afterwards even on failure.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        os.environ.get("GITAGUIDE_RUN_GRAPH_ROUNDTRIP") != "1",
        reason="needs Docker; set GITAGUIDE_RUN_GRAPH_ROUNDTRIP=1 to run",
    ),
]

CONTAINER = "gitaguide-roundtrip-pytest"
PASSWORD = "roundtriptest"
BOLT_PORT = 7688

#: Small but structurally complete: two scriptures, every relationship type,
#: and text with the quotes and Devanagari that broke the generated Cypher.
FIXTURE_VERSES = [
    {
        "id": "BG-2.47",
        "source": "bhagavad_gita",
        "chapter": 2,
        "verse": 47,
        "book": None,
        "text_english": 'He said "let right deeds be thy motive", not the fruit.',
        "text_sanskrit": "कर्मण्येवाधिकारस्ते",
        "transliteration": "karmany evadhikaras te",
        "commentary": "Detachment from results; O'Brien's note, line 1.",
        "citation": "Bhagavad Gita 2.47 (BG-2.47)",
        "embedding": [0.1] * 384,
    },
    {
        "id": "AS-1.10.10",
        "source": "arthashastra",
        "chapter": 10,
        "verse": 10,
        "book": 1,
        "text_english": "Whoever is versed in polity shall keep his senses under control.",
        "text_sanskrit": "",
        "transliteration": "",
        "commentary": "",
        "citation": "Arthashastra Book 1, Chapter 10, Sutra 10 (AS-1.10.10)",
        "embedding": [0.2] * 384,
    },
]


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=check, timeout=300
    )


@pytest.fixture(scope="module")
def neo4j_container() -> Iterator[dict[str, str]]:
    """A disposable Neo4j. Removed afterwards, including on failure."""
    docker("rm", "-f", CONTAINER, check=False)
    docker(
        "run", "-d", "--name", CONTAINER,
        "-p", f"{BOLT_PORT}:7687",
        "-e", f"NEO4J_AUTH=neo4j/{PASSWORD}",
        "-e", "NEO4J_PLUGINS=[]",
        "neo4j:5-community",
    )
    try:
        deadline = time.time() + 180
        while time.time() < deadline:
            probe = docker(
                "exec", CONTAINER, "cypher-shell", "-u", "neo4j", "-p", PASSWORD,
                "RETURN 1", check=False,
            )
            if probe.returncode == 0:
                break
            time.sleep(5)
        else:  # pragma: no cover - only on a very slow machine
            pytest.fail("the Neo4j container did not become ready within 180s")

        yield {
            "GITAGUIDE_NEO4J_URI": f"bolt://localhost:{BOLT_PORT}",
            "GITAGUIDE_NEO4J_USERNAME": "neo4j",
            "GITAGUIDE_NEO4J_PASSWORD": PASSWORD,
            "GITAGUIDE_NEO4J_DATABASE": "neo4j",
            "GITAGUIDE_MOCK_MODE": "false",
            "GITAGUIDE_USE_REAL_NEO4J": "true",
            # Export and import must agree on the encoder, or the import
            # refuses (test_graph_manifest.py covers the refusal offline).
            "GITAGUIDE_EMBEDDER_PRECISION": "fp32",
        }
    finally:
        docker("rm", "-f", CONTAINER, check=False)


def run_module(module: str, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    """Run a script in a subprocess so it reads the env like an operator would."""
    import sys

    return subprocess.run(
        [sys.executable, "-m", module, *args],
        capture_output=True,
        text=True,
        env={**os.environ, **env},
        cwd=Path(__file__).resolve().parent.parent,
        timeout=900,
    )


@pytest.fixture(scope="module")
def export_dir(tmp_path_factory: pytest.TempPathFactory, neo4j_container: dict[str, str]):
    """Seed the container, export from it, then wipe it ready for the import."""
    from neo4j import GraphDatabase

    uri = neo4j_container["GITAGUIDE_NEO4J_URI"]
    driver = GraphDatabase.driver(uri, auth=("neo4j", PASSWORD))
    try:
        driver.execute_query("MATCH (n) DETACH DELETE n", {}, database_="neo4j")
        driver.execute_query(
            "UNWIND $rows AS row MERGE (v:Verse {id: row.id}) SET v += row", {"rows": FIXTURE_VERSES}
        )
        driver.execute_query(
            "MERGE (s:Scripture {key: 'bhagavad_gita'}) SET s.name = 'Bhagavad Gita' "
            "MERGE (t:Scripture {key: 'arthashastra'}) SET t.name = 'Arthashastra'"
        )
        driver.execute_query(
            "MATCH (v:Verse {id: 'BG-2.47'}), (s:Scripture {key: 'bhagavad_gita'}) "
            "MERGE (v)-[:PART_OF]->(s) "
            "MERGE (e:Emotion {name: 'nervousness'}) MERGE (v)-[:ADDRESSES_EMOTION]->(e) "
            "MERGE (t:Theme {name: 'duty'}) MERGE (v)-[:HAS_THEME]->(t) "
            "MERGE (c:Concept {name: 'danda'}) MERGE (v)-[:MENTIONS]->(c)"
        )
        driver.execute_query(
            "MATCH (v:Verse {id: 'AS-1.10.10'}), (s:Scripture {key: 'arthashastra'}) "
            "MERGE (v)-[:PART_OF]->(s) "
            "MERGE (e:Emotion {name: 'anger'}) MERGE (v)-[:ADDRESSES_EMOTION]->(e) "
            "MERGE (t:Theme {name: 'governance'}) MERGE (v)-[:HAS_THEME]->(t) "
            "MERGE (c:Concept {name: 'danda'}) MERGE (v)-[:MENTIONS]->(c)"
        )
        driver.execute_query(
            "MATCH (a:Verse {id: 'BG-2.47'}), (b:Verse {id: 'AS-1.10.10'}) "
            "MERGE (a)-[r:RELATED_TO]->(b) SET r.weight = 0.5, r.shared = ['danda']"
        )
    finally:
        driver.close()

    out = tmp_path_factory.mktemp("graph_export")
    # The fixture's vectors are synthetic ([0.1] * 384); no encoder wrote them,
    # so the precision check is skipped here and exercised offline instead.
    result = run_module(
        "scripts.export_neo4j", neo4j_container, "--out", str(out), "--skip-precision-check"
    )
    assert result.returncode == 0, f"export failed:\n{result.stdout}\n{result.stderr}"

    # Wipe, so the import has to create everything rather than finding it.
    driver = GraphDatabase.driver(uri, auth=("neo4j", PASSWORD))
    try:
        driver.execute_query("MATCH (n) DETACH DELETE n", {}, database_="neo4j")
    finally:
        driver.close()
    return out


def graph_counts(uri: str) -> dict[str, int]:
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(uri, auth=("neo4j", PASSWORD))
    try:
        records, _, _ = driver.execute_query(
            "MATCH (v:Verse) RETURN count(v) AS verses, count(v.embedding) AS embedded",
            {},
            database_="neo4j",
        )
        counts = dict(records[0])
        records, _, _ = driver.execute_query(
            "MATCH ()-[r]->() RETURN type(r) AS rel, count(r) AS n", {}, database_="neo4j"
        )
        for row in records:
            counts[row["rel"]] = row["n"]
        counts["relationships_total"] = sum(
            value for key, value in counts.items() if key.isupper()
        )
        return counts
    finally:
        driver.close()


# The round trip
def test_import_reproduces_the_exported_graph(
    neo4j_container: dict[str, str], export_dir: Path
) -> None:
    """The check that would have caught the invalid-Cypher bug.

    The import must actually execute against a database. A statement that does
    not parse cannot make these counts come out right.
    """
    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))

    result = run_module(
        "scripts.import_neo4j", neo4j_container, "--source", str(export_dir)
    )
    assert result.returncode == 0, f"import failed:\n{result.stdout}\n{result.stderr}"

    counts = graph_counts(neo4j_container["GITAGUIDE_NEO4J_URI"])
    assert counts["verses"] == manifest["verses"]
    assert counts["embedded"] == manifest["verses_with_embedding"]
    for rel_type, expected in manifest["relationships"].items():
        assert counts.get(rel_type, 0) == expected, f"{rel_type} did not round-trip"
    assert counts["relationships_total"] == manifest["relationships_total"]


def test_verse_properties_survive_quotes_and_devanagari(
    neo4j_container: dict[str, str], export_dir: Path
) -> None:
    """Content that the string-built Cypher would have mangled or broken on."""
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        neo4j_container["GITAGUIDE_NEO4J_URI"], auth=("neo4j", PASSWORD)
    )
    try:
        records, _, _ = driver.execute_query(
            "MATCH (v:Verse {id: 'BG-2.47'}) RETURN v.text_english AS english, "
            "v.text_sanskrit AS sanskrit, v.commentary AS commentary, "
            "size(v.embedding) AS dim",
            {},
            database_="neo4j",
        )
    finally:
        driver.close()

    row = records[0]
    assert row["english"] == FIXTURE_VERSES[0]["text_english"], "double quotes were mangled"
    assert row["sanskrit"] == "कर्मण्येवाधिकारस्ते", "Devanagari did not survive"
    assert "O'Brien" in row["commentary"], "an apostrophe was mangled"
    assert row["dim"] == 384, "the embedding was not stored as a 384-float list"


def test_the_vector_index_exists_and_is_online(
    neo4j_container: dict[str, str], export_dir: Path
) -> None:
    """A POPULATING index returns poor neighbours instead of erroring."""
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        neo4j_container["GITAGUIDE_NEO4J_URI"], auth=("neo4j", PASSWORD)
    )
    try:
        records, _, _ = driver.execute_query(
            "SHOW INDEXES YIELD name, type, state RETURN name, type, state",
            {},
            database_="neo4j",
        )
    finally:
        driver.close()

    indexes = {row["name"]: row for row in records}
    vector = indexes.get("verseEmbeddingIndex")
    assert vector is not None, "the vector index was not created"
    assert vector["type"] == "VECTOR"
    assert vector["state"] == "ONLINE"

    fulltext = indexes.get("verseTextIndex")
    assert fulltext is not None and fulltext["type"] == "FULLTEXT"


def test_the_import_is_idempotent(
    neo4j_container: dict[str, str], export_dir: Path
) -> None:
    """Re-running must not duplicate anything - a dropped upload is resumed."""
    before = graph_counts(neo4j_container["GITAGUIDE_NEO4J_URI"])

    result = run_module(
        "scripts.import_neo4j", neo4j_container, "--source", str(export_dir)
    )
    assert result.returncode == 0, f"re-import failed:\n{result.stdout}\n{result.stderr}"

    assert graph_counts(neo4j_container["GITAGUIDE_NEO4J_URI"]) == before


def test_the_import_refuses_an_export_from_the_other_encoder(
    neo4j_container: dict[str, str], export_dir: Path
) -> None:
    """Same files, same database, a different GITAGUIDE_EMBEDDER_PRECISION: refused.

    The real script, the real environment path, exit code 2 before a single
    connection is made.
    """
    refused = run_module(
        "scripts.import_neo4j",
        {**neo4j_container, "GITAGUIDE_EMBEDDER_PRECISION": "int8"},
        "--source",
        str(export_dir),
    )
    assert refused.returncode == 2
    assert "fp32" in refused.stderr and "int8" in refused.stderr

    # And nothing changed in the target.
    assert graph_counts(neo4j_container["GITAGUIDE_NEO4J_URI"])["verses"] == 2


def test_verification_fails_on_a_partial_graph(
    neo4j_container: dict[str, str], export_dir: Path
) -> None:
    """The guard has to be able to fail, or it is decoration.

    Deletes a verse, expects a non-zero exit and a named mismatch, then repairs
    the graph by re-running the import - which is also the documented recovery.
    """
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        neo4j_container["GITAGUIDE_NEO4J_URI"], auth=("neo4j", PASSWORD)
    )
    try:
        driver.execute_query(
            "MATCH (v:Verse {id: 'BG-2.47'}) DETACH DELETE v", {}, database_="neo4j"
        )
    finally:
        driver.close()

    failed = run_module(
        "scripts.import_neo4j", neo4j_container, "--source", str(export_dir), "--verify-only"
    )
    assert failed.returncode == 1, "a partial graph must be a hard failure"
    combined = failed.stdout + failed.stderr
    assert "verses:" in combined, "the failure must name what did not match"
    assert "mismatch" in combined

    repaired = run_module(
        "scripts.import_neo4j", neo4j_container, "--source", str(export_dir)
    )
    assert repaired.returncode == 0, "re-running must repair a partial import"
