"""In prod, the research surface and the OpenAPI pages do not exist.

The 19 Sep 2026 privacy audit chained three routes into a complete failure on
the deployed service: ``GET /safety/logs`` listed the session and message ids
of every crisis-flagged turn, ``GET /explain/{id}`` returned that message's
verbatim text, and ``POST /chat`` took a message with no consent and no rate
limit. The beta UI calls none of them. So with ``environment=prod`` they are
not mounted - a bare 404, the same answer as any path that never existed -
while a developer's app keeps them for the eval scripts and the explanation
panel.

These tests hold both halves: what prod must not serve, and what it still
must. The exact prod route table is asserted too, so a new route has to be
added here on purpose rather than appearing in prod by default.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

#: Every path a prod deployment serves. ``/health`` for the operator and the
#: smoke test; ``/api/*`` for the beta UI. Adding a route to the app without
#: adding it here fails ``test_the_prod_route_table_is_exactly_this``.
PROD_PATHS = frozenset(
    {
        "/health",
        "/api/chat",
        "/api/consent",
        "/api/feedback",
        "/api/sessions",
        "/api/sessions/{session_id}/messages",
        "/api/sessions/{session_id}/claim",
    }
)

RESEARCH_PROBES = [
    ("GET", "/safety/logs", None),
    ("GET", "/explain/00000000-0000-0000-0000-000000000000", None),
    ("POST", "/chat", {"message": "this must not reach the pipeline"}),
]
OPENAPI_PROBES = [("GET", "/openapi.json", None), ("GET", "/docs", None), ("GET", "/redoc", None)]


def _prod_settings(**overrides: object) -> Settings:
    return Settings(mock_mode=True, environment="prod", **overrides)


@pytest.fixture(scope="module")
def prod_client() -> Iterator[TestClient]:
    with TestClient(create_app(_prod_settings())) as client:
        yield client


@pytest.fixture(scope="module")
def dev_client() -> Iterator[TestClient]:
    with TestClient(create_app(Settings(mock_mode=True, environment="dev"))) as client:
        yield client


def _call(client: TestClient, method: str, path: str, payload: dict | None):
    return client.post(path, json=payload) if method == "POST" else client.get(path)


def test_is_prod_follows_the_environment_setting() -> None:
    assert _prod_settings().is_prod is True
    for environment in ("dev", "test"):
        assert Settings(mock_mode=True, environment=environment).is_prod is False


@pytest.mark.parametrize(("method", "path", "payload"), RESEARCH_PROBES)
def test_research_routes_are_not_mounted_in_prod(
    prod_client: TestClient, method: str, path: str, payload: dict | None
) -> None:
    """A bare 404: the path does not exist, as opposed to exists-and-refuses.

    ``GET /explain/<unknown>`` on a mounted route also answers 404, but with
    ``"message not found"``; FastAPI's own ``"Not Found"`` is the only body an
    unmounted path can produce, so that is what is asserted.
    """
    response = _call(prod_client, method, path, payload)
    assert response.status_code == 404, f"{method} {path} is reachable in prod"
    assert response.json() == {"detail": "Not Found"}, f"{method} {path} is mounted"


@pytest.mark.parametrize(("method", "path", "payload"), OPENAPI_PROBES)
def test_openapi_pages_are_off_in_prod(
    prod_client: TestClient, method: str, path: str, payload: dict | None
) -> None:
    assert _call(prod_client, method, path, payload).status_code == 404


def test_the_prod_route_table_is_exactly_this() -> None:
    """Enumerated through FastAPI's own schema builder.

    ``app.openapi()`` still works with ``openapi_url=None`` - only the route
    that *serves* the schema is gone - and it is the one listing that does not
    depend on how a given FastAPI version nests included routers internally.
    """
    app = create_app(_prod_settings())
    served = set(app.openapi()["paths"])
    assert served == PROD_PATHS, (
        f"prod serves {sorted(served - PROD_PATHS)} beyond the declared table, "
        f"or lacks {sorted(PROD_PATHS - served)}. A new route must be added to "
        "PROD_PATHS deliberately - that is the review step this test creates."
    )


def test_health_and_the_beta_surface_still_serve_in_prod(prod_client: TestClient) -> None:
    """Not mounting the research routes must cost the UI nothing."""
    health = prod_client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    session = "prod-exposure-session"
    consent = prod_client.post(
        "/api/consent", json={"session_id": session, "version": Settings().consent_version}
    )
    assert consent.status_code == 200
    chat = prod_client.post(
        "/api/chat", json={"session_id": session, "message": "I am anxious about my appraisal"}
    )
    assert chat.status_code == 200
    assert chat.json()["reply"]


@pytest.mark.parametrize(("method", "path", "payload"), RESEARCH_PROBES + OPENAPI_PROBES)
def test_the_same_routes_exist_outside_prod(
    dev_client: TestClient, method: str, path: str, payload: dict | None
) -> None:
    """The eval scripts and the explanation panel still have their surface."""
    response = _call(dev_client, method, path, payload)
    assert response.status_code != 404 or response.json() != {"detail": "Not Found"}, (
        f"{method} {path} is missing outside prod too; the split lost a route"
    )


def test_the_dev_openapi_lists_the_research_routes(dev_client: TestClient) -> None:
    paths = set(dev_client.get("/openapi.json").json()["paths"])
    assert {"/chat", "/explain/{message_id}", "/safety/logs"} <= paths
