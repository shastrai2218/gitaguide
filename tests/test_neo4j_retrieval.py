"""Tests for the Neo4j retrieval path - the silent-zero bug and its guards.

No live database and no ``neo4j`` driver: a stub retriever/driver is injected.
These tests exist because retrieval reported ``candidates_considered=0`` while
Neo4j was returning good hits, and nothing in the logs said so.

Root cause: ``neo4j-graphrag``'s default record formatter returns
``RetrieverResultItem(content=str(record), metadata=record.get("metadata"))``.
With no ``metadata`` column that is a repr string plus ``None``, and the parser
discarded every record. The fixes pinned here are the formatter, the parameter
passing, and making both failure modes loud.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from app.config import Settings
from app.embeddings import HashingEmbedder
from app.pipeline.retrieval import (
    DEFAULT_QUERY_PARAMS,
    VERSE_RECORD_KEYS,
    Neo4jHybridRetriever,
    RetrievalError,
    _parse_record_repr,
    _record_to_dict,
    _supports_query_params,
    verse_result_formatter,
)
from app.pipeline.schemas import EmotionResult, EmotionScore, IntentLabel, IntentResult

pytest.importorskip("neo4j_graphrag", reason="neo4j-graphrag not installed")

RECORD = {
    "id": "BG-2.47",
    "source": "bhagavad_gita",
    "chapter": 2,
    "verse": 47,
    "book": None,
    "text_english": "Your right is to the work itself, never to its fruits.",
    "text_sanskrit": "karmany evadhikaras te",
    "transliteration": "",
    "commentary": "",
    "citation": "Bhagavad Gita 2.47 (BG-2.47)",
    "themes": ["duty", "detachment"],
    "matched_emotions": ["nervousness"],
    "related_via_emotion": [{"id": "BG-2.14", "via": "nervousness"}],
    "search_score": 0.91,
}

#: Exactly what the library's *default* formatter produced - the bug's shape.
REPR_CONTENT = (
    "<Record id='BG-2.47' source='bhagavad_gita' chapter=2 verse=47 book=None "
    "text_english='Your right is to the work itself, never to its fruits.' "
    "text_sanskrit='karmany evadhikaras te' transliteration='' commentary='' "
    "citation='Bhagavad Gita 2.47 (BG-2.47)' themes=['duty', 'detachment'] "
    "matched_emotions=['nervousness'] related_via_emotion=[] search_score=0.91>"
)


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
class StubItem:
    """Mimics ``RetrieverResultItem`` with whatever content shape we want."""

    def __init__(self, content: Any, metadata: Any = None) -> None:
        self.content = content
        self.metadata = metadata


class StubRawResult:
    def __init__(self, items: list[Any]) -> None:
        self.items = items
        self.metadata = {"__retriever": "StubRetriever"}


class StubGraphRAGRetriever:
    """Records the kwargs ``search()`` was called with; replays a result."""

    def __init__(self, items: list[Any] | None = None, error: Exception | None = None) -> None:
        self._items = items or []
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def search(
        self,
        query_text: str,
        top_k: int = 5,
        query_params: dict[str, Any] | None = None,
    ) -> StubRawResult:
        self.calls.append(
            {"query_text": query_text, "top_k": top_k, "query_params": query_params}
        )
        if self._error is not None:
            raise self._error
        return StubRawResult(self._items)


class StubOldGraphRAGRetriever:
    """A build with no ``query_params`` support, to force the driver fallback."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def search(self, query_text: str, top_k: int = 5) -> StubRawResult:
        self.calls.append({"query_text": query_text, "top_k": top_k})
        return StubRawResult([])


class StubRecord:
    """Mimics ``neo4j.Record`` (has ``.data()``)."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def data(self) -> dict[str, Any]:
        return dict(self._payload)


class StubDriver:
    """Captures ``execute_query`` calls for the driver-level fallback."""

    def __init__(self, records: list[Any] | None = None, error: Exception | None = None) -> None:
        self.records = records if records is not None else []
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute_query(self, cypher: str, parameters: dict[str, Any], **kwargs: Any):
        self.calls.append((cypher, parameters))
        if self.error is not None:
            raise self.error
        return self.records, None, None

    def verify_connectivity(self) -> None:
        return None

    def close(self) -> None:
        return None


@pytest.fixture
def neo4j_settings() -> Settings:
    return Settings(
        mock_mode=False,
        use_real_neo4j=True,
        neo4j_password="test-password",
        embedding_dim=384,
        retrieval_candidate_k=20,
    )


def make_retriever(
    settings: Settings,
    stub: Any,
    driver: Any | None = None,
) -> Neo4jHybridRetriever:
    """Neo4jHybridRetriever with the graphrag retriever pre-injected.

    The embedder is the deterministic offline one: these tests must never load
    model weights or reach the network.
    """
    retriever = Neo4jHybridRetriever(
        settings,
        embedder=HashingEmbedder(dim=settings.embedding_dim),
        driver=driver or StubDriver(),
    )
    retriever._retriever = stub
    return retriever


def emotion_of(*labels: str) -> EmotionResult:
    return EmotionResult(
        labels=[EmotionScore(emotion=label, score=0.8) for label in labels], intensity=0.6
    )


# --------------------------------------------------------------------------- #
# The formatter: item.content must be structured
# --------------------------------------------------------------------------- #
def test_formatter_returns_a_dict_not_a_repr_string() -> None:
    item = verse_result_formatter(StubRecord(RECORD))
    assert isinstance(item.content, dict), "content must be structured, not str(record)"
    assert item.content["id"] == "BG-2.47"
    assert item.content["themes"] == ["duty", "detachment"]
    assert item.metadata == {"id": "BG-2.47"}


def test_formatter_flattens_the_metadata_column() -> None:
    """The query returns a `metadata` map; it must merge into one flat payload."""
    record = StubRecord({"id": "BG-2.47", "metadata": {"citation": "BG 2.47", "themes": ["duty"]}})
    content = verse_result_formatter(record).content
    assert content["citation"] == "BG 2.47"
    assert content["themes"] == ["duty"]
    assert "metadata" not in content


def test_formatter_accepts_a_plain_mapping() -> None:
    assert verse_result_formatter(RECORD).content["id"] == "BG-2.47"


def test_query_returns_every_key_the_parser_expects() -> None:
    """Guard against the query and the parser drifting apart again."""
    from kg.cypher_queries import HYBRID_RETRIEVAL_QUERY

    for key in VERSE_RECORD_KEYS:
        assert f"AS {key}" in HYBRID_RETRIEVAL_QUERY, f"query no longer returns {key!r}"
    assert "AS metadata" in HYBRID_RETRIEVAL_QUERY


# --------------------------------------------------------------------------- #
# The parser
# --------------------------------------------------------------------------- #
def test_parses_structured_content() -> None:
    assert _record_to_dict(StubItem(RECORD))["id"] == "BG-2.47"


def test_parses_a_neo4j_record() -> None:
    assert _record_to_dict(StubRecord(RECORD))["id"] == "BG-2.47"


def test_parses_from_metadata() -> None:
    assert _record_to_dict(StubItem("junk", metadata=RECORD))["id"] == "BG-2.47"


def test_recovers_the_repr_string_shape(caplog: pytest.LogCaptureFixture) -> None:
    """The exact shape that caused the silent zero must now be recovered, loudly."""
    with caplog.at_level(logging.WARNING, logger="app.pipeline.retrieval"):
        record = _record_to_dict(StubItem(REPR_CONTENT))
    assert record["id"] == "BG-2.47"
    assert record["chapter"] == 2
    assert record["themes"] == ["duty", "detachment"]
    assert "result_formatter is not wired" in caplog.text


def test_repr_parser_rejects_unrelated_strings() -> None:
    assert _parse_record_repr("not a record at all") == {}
    assert _parse_record_repr("<Record foo='bar'>") == {}, "no id -> unusable"


def test_unparseable_item_yields_empty_dict() -> None:
    assert _record_to_dict(StubItem(None)) == {}
    assert _record_to_dict(object()) == {}


# --------------------------------------------------------------------------- #
# Parameter passing
# --------------------------------------------------------------------------- #
def test_installed_library_accepts_query_params() -> None:
    """Verified against the installed package, not assumed from a version."""
    from neo4j_graphrag.retrievers import HybridCypherRetriever

    assert _supports_query_params(HybridCypherRetriever.search)


def test_supports_query_params_detects_an_old_signature() -> None:
    assert not _supports_query_params(StubOldGraphRAGRetriever().search)
    assert _supports_query_params(StubGraphRAGRetriever().search)


def test_kwargs_only_signature_is_accepted() -> None:
    def search(**kwargs: Any) -> None: ...

    assert _supports_query_params(search)


async def test_emotions_are_always_passed(neo4j_settings: Settings) -> None:
    """$emotions is required by the Cypher, so it must never be absent."""
    stub = StubGraphRAGRetriever([verse_result_formatter(StubRecord(RECORD))])
    retriever = make_retriever(neo4j_settings, stub)

    await retriever.retrieve("I am anxious", emotion=emotion_of("nervousness", "fear"))
    assert stub.calls[0]["query_params"]["emotions"] == ["nervousness", "fear"]

    # ...and an empty list when no emotion was detected, never a missing key.
    await retriever.retrieve("what is dharma")
    assert stub.calls[1]["query_params"] == {"emotions": []}
    assert "emotions" in DEFAULT_QUERY_PARAMS


async def test_candidates_are_actually_built(neo4j_settings: Settings) -> None:
    """The regression itself: records in -> candidates out."""
    items = [verse_result_formatter(StubRecord(RECORD))]
    retriever = make_retriever(neo4j_settings, StubGraphRAGRetriever(items))

    trace = await retriever.retrieve(
        "I am anxious about my appraisal",
        emotion=emotion_of("nervousness"),
        intent=IntentResult(intent=IntentLabel.PROFESSIONAL_DILEMMA, confidence=0.9),
    )
    assert trace.candidates_considered == 1
    assert trace.parse_failures == 0
    assert trace.verse_ids == ["BG-2.47"]
    assert trace.results[0].graph_paths, "matched_emotions must become a graph path"
    assert "ADDRESSES_EMOTION" in trace.results[0].graph_paths[0].description


# --------------------------------------------------------------------------- #
# Loud failures
# --------------------------------------------------------------------------- #
async def test_items_returned_but_none_parsed_is_an_error(
    neo4j_settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    """items > 0 and parsed == 0 must ERROR and raise, never look like 'no hits'."""
    unusable = [StubItem(None), StubItem(object())]
    retriever = make_retriever(neo4j_settings, StubGraphRAGRetriever(unusable))

    with (
        caplog.at_level(logging.ERROR, logger="app.pipeline.retrieval"),
        pytest.raises(RetrievalError, match="none could be parsed"),
    ):
        await retriever.retrieve("I am anxious", emotion=emotion_of("nervousness"))

    assert "RETRIEVAL PARSE FAILURE" in caplog.text
    assert "2 record(s) returned, 0 parsed" in caplog.text
    assert any(record.levelno == logging.ERROR for record in caplog.records)


async def test_search_exception_is_logged_and_raised(
    neo4j_settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    boom = RuntimeError("ParameterMissing: Expected parameter(s): emotions")
    retriever = make_retriever(neo4j_settings, StubGraphRAGRetriever(error=boom))

    with (
        caplog.at_level(logging.ERROR, logger="app.pipeline.retrieval"),
        pytest.raises(RetrievalError, match="hybrid search failed"),
    ):
        await retriever.retrieve("I am anxious")

    assert "RETRIEVAL SEARCH FAILED" in caplog.text
    assert "ParameterMissing" in caplog.text
    assert caplog.records[-1].exc_info is not None, "must include the traceback"


async def test_partial_parse_failure_warns_but_continues(
    neo4j_settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    items = [verse_result_formatter(StubRecord(RECORD)), StubItem(None)]
    retriever = make_retriever(neo4j_settings, StubGraphRAGRetriever(items))

    with caplog.at_level(logging.WARNING, logger="app.pipeline.retrieval"):
        trace = await retriever.retrieve("I am anxious", emotion=emotion_of("nervousness"))

    assert trace.candidates_considered == 1
    assert trace.parse_failures == 1
    assert "dropped 1/2 unparseable" in caplog.text


async def test_genuinely_empty_result_is_not_an_error(
    neo4j_settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    """Zero records is a legitimate answer and must not raise."""
    retriever = make_retriever(neo4j_settings, StubGraphRAGRetriever([]))

    with caplog.at_level(logging.INFO, logger="app.pipeline.retrieval"):
        trace = await retriever.retrieve("something with no match at all")

    assert trace.candidates_considered == 0
    assert trace.results == []
    assert "returned 0 records" in caplog.text
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


# --------------------------------------------------------------------------- #
# Driver-level fallback
# --------------------------------------------------------------------------- #
async def test_falls_back_to_the_driver_when_params_unsupported(
    neo4j_settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    """An old library must not mean dropping $emotions - go via the driver."""
    driver = StubDriver(records=[StubRecord(RECORD)])
    retriever = make_retriever(neo4j_settings, StubOldGraphRAGRetriever(), driver=driver)

    with caplog.at_level(logging.WARNING, logger="app.pipeline.retrieval"):
        trace = await retriever.retrieve("I am anxious", emotion=emotion_of("nervousness"))

    assert trace.candidates_considered == 1
    assert "cannot parameterise the retrieval query" in caplog.text

    cypher, parameters = driver.calls[0]
    assert parameters["emotions"] == ["nervousness"]
    assert parameters["vector_index_name"] == neo4j_settings.neo4j_vector_index
    assert parameters["fulltext_index_name"] == neo4j_settings.neo4j_fulltext_index
    assert len(parameters["query_vector"]) == neo4j_settings.embedding_dim
    assert "db.index.vector.queryNodes" in cypher
    assert "db.index.fulltext.queryNodes" in cypher
    assert "ADDRESSES_EMOTION" in cypher, "the graph tail must still run"


async def test_driver_fallback_failure_is_logged_and_raised(
    neo4j_settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    driver = StubDriver(error=RuntimeError("index does not exist"))
    retriever = make_retriever(neo4j_settings, StubOldGraphRAGRetriever(), driver=driver)

    with (
        caplog.at_level(logging.ERROR, logger="app.pipeline.retrieval"),
        pytest.raises(RetrievalError, match="driver-level hybrid search failed"),
    ):
        await retriever.retrieve("I am anxious")

    assert "RETRIEVAL SEARCH FAILED (driver-level path" in caplog.text


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #
def test_retriever_is_constructed_with_the_formatter(
    neo4j_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole bug was a missing result_formatter - assert it is passed.

    The library class is swapped for a capturing stub: this asserts *our*
    wiring, and it opens no connection.
    """
    import neo4j_graphrag.retrievers as graphrag

    captured: dict[str, Any] = {}

    class CapturingRetriever:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(graphrag, "HybridCypherRetriever", CapturingRetriever)

    retriever = Neo4jHybridRetriever(
        neo4j_settings,
        embedder=HashingEmbedder(dim=neo4j_settings.embedding_dim),
        driver=StubDriver(),
    )
    retriever._get_retriever()

    assert captured["result_formatter"] is verse_result_formatter
    assert captured["vector_index_name"] == neo4j_settings.neo4j_vector_index
    assert captured["fulltext_index_name"] == neo4j_settings.neo4j_fulltext_index
    assert "ADDRESSES_EMOTION" in captured["retrieval_query"]
    assert "$emotions" in captured["retrieval_query"]
