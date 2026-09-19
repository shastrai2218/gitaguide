"""The Aura keep-alive must report a bad graph, not just touch it.

The script exists so a quiet beta does not let AuraDB Free pause. A version that
exited 0 whenever it *connected* would hide the failures that matter: an
instance that answers but is empty, or one that cannot be reached at all.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.config import Settings
from scripts import keepalive_neo4j


class _FakeSession:
    def __init__(self, verses: int) -> None:
        self._verses = verses

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def run(self, query: str) -> _FakeSession:
        assert "Verse" in query
        return self

    def single(self) -> dict[str, int]:
        return {"verses": self._verses}


class _FakeDriver:
    def __init__(self, verses: int = 3836, connect_error: Exception | None = None) -> None:
        self._verses = verses
        self._connect_error = connect_error
        self.closed = False

    def verify_connectivity(self) -> None:
        if self._connect_error is not None:
            raise self._connect_error

    def session(self, database: str) -> _FakeSession:
        return _FakeSession(self._verses)

    def close(self) -> None:
        self.closed = True


def _settings(password: str = "not-a-real-password") -> Settings:
    return Settings(
        _env_file=None,
        mock_mode=True,
        neo4j_uri="neo4j+s://example.databases.neo4j.io",
        neo4j_username="example",
        neo4j_password=password,
        neo4j_database="example",
    )


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch):
    """Point the script at fake settings and a fake driver it can hand back."""

    def install(driver: _FakeDriver, password: str = "not-a-real-password") -> _FakeDriver:
        monkeypatch.setattr(keepalive_neo4j, "get_settings", lambda: _settings(password))
        monkeypatch.setattr("neo4j.GraphDatabase.driver", lambda *a, **k: driver)
        return driver

    return install


def test_a_populated_graph_exits_zero(patched: Any, capsys: pytest.CaptureFixture[str]) -> None:
    driver = patched(_FakeDriver(verses=3836))
    assert keepalive_neo4j.main() == 0
    assert "3836 verses" in capsys.readouterr().out
    assert driver.closed, "the driver must be closed even on success"


def test_an_empty_graph_is_a_failure_not_a_touch(patched: Any) -> None:
    patched(_FakeDriver(verses=0))
    assert keepalive_neo4j.main() == 1


def test_an_unreachable_instance_is_a_failure_and_mentions_pausing(
    patched: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    driver = patched(_FakeDriver(connect_error=ConnectionError("no route")))
    assert keepalive_neo4j.main() == 1
    assert "paused" in capsys.readouterr().err
    assert driver.closed, "the driver must be closed after a failed connect"


def test_no_password_means_not_configured(patched: Any) -> None:
    patched(_FakeDriver(), password="")
    assert keepalive_neo4j.main() == 2


def test_the_password_is_never_printed(patched: Any, capsys: pytest.CaptureFixture[str]) -> None:
    secret = "correct-horse-battery-staple"
    patched(_FakeDriver(connect_error=ConnectionError("boom")), password=secret)
    keepalive_neo4j.main()
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err
