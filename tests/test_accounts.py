"""Optional accounts on top of the anonymous session model (app/auth.py).

Every scenario here turns on a Clerk issuer so tokens verify; what is under
test is what the *routes* do with a verified identity - claiming a session,
listing an account's sessions, and refusing to touch a session someone else
already claimed - not the verification itself (that is tests/test_auth.py).

No network call happens: ``app.auth._client_for`` is monkeypatched for the
whole module to a stub that hands back one fixed key pair's public half, the
way ``PyJWKClient`` would after fetching a real JWKS once.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

import app.auth as auth_module
from app.config import Settings
from app.main import create_app

ISSUER = "https://test.clerk.accounts.dev"


@pytest.fixture(scope="module")
def keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture(scope="module", autouse=True)
def _patch_jwks(keypair) -> Iterator[None]:
    _, public_key = keypair
    signing_key = SimpleNamespace(key=public_key)
    fake_client = SimpleNamespace(get_signing_key_from_jwt=lambda token: signing_key)
    original = auth_module._client_for
    auth_module._client_for = lambda issuer: fake_client
    yield
    auth_module._client_for = original


def _token(private_key, *, subject: str) -> str:
    now = int(time.time())
    payload = {"sub": subject, "iss": ISSUER, "iat": now, "exp": now + 3600}
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "test-key"})


@pytest.fixture
def auth_header(keypair):
    private_key, _ = keypair

    def _for(subject: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {_token(private_key, subject=subject)}"}

    return _for


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    app = create_app(
        Settings(
            mock_mode=True,
            environment="test",
            rate_limit_enabled=False,
            consent_required=False,
            clerk_issuer=ISSUER,
        )
    )
    with TestClient(app) as test_client:
        yield test_client


# --------------------------------------------------------------------------- #
# Listing an account's sessions
# --------------------------------------------------------------------------- #
def test_listing_sessions_requires_sign_in(client: TestClient) -> None:
    response = client.get("/api/sessions")
    assert response.status_code == 401


def test_a_chat_turn_claims_its_session_for_the_signed_in_user(
    client: TestClient, auth_header
) -> None:
    client.post(
        "/api/chat",
        json={"session_id": "list-me-1", "message": "hello"},
        headers=auth_header("user_alice"),
    )

    mine = client.get("/api/sessions", headers=auth_header("user_alice")).json()["sessions"]
    assert any(session["id"] == "list-me-1" for session in mine)

    someone_elses = client.get("/api/sessions", headers=auth_header("user_bob")).json()["sessions"]
    assert not any(session["id"] == "list-me-1" for session in someone_elses)


def test_an_anonymous_turn_never_appears_in_anyones_account(
    client: TestClient, auth_header
) -> None:
    client.post("/api/chat", json={"session_id": "stays-anonymous", "message": "hi"})
    mine = client.get("/api/sessions", headers=auth_header("user_carol")).json()["sessions"]
    assert not any(session["id"] == "stays-anonymous" for session in mine)


# --------------------------------------------------------------------------- #
# Claiming
# --------------------------------------------------------------------------- #
def test_claim_requires_sign_in(client: TestClient) -> None:
    response = client.post("/api/sessions/some-session/claim")
    assert response.status_code == 401


def test_claiming_an_unknown_session_is_404(client: TestClient, auth_header) -> None:
    response = client.post(
        "/api/sessions/never-existed/claim", headers=auth_header("user_dave")
    )
    assert response.status_code == 404


def test_claiming_an_anonymous_session_attaches_it(client: TestClient, auth_header) -> None:
    client.post("/api/chat", json={"session_id": "pre-login-chat", "message": "hi"})

    response = client.post(
        "/api/sessions/pre-login-chat/claim", headers=auth_header("user_erin")
    )
    assert response.status_code == 200
    assert response.json() == {"session_id": "pre-login-chat", "claimed": True}

    mine = client.get("/api/sessions", headers=auth_header("user_erin")).json()["sessions"]
    assert any(session["id"] == "pre-login-chat" for session in mine)


def test_claiming_a_session_someone_else_already_claimed_does_not_reassign_it(
    client: TestClient, auth_header
) -> None:
    client.post(
        "/api/chat",
        json={"session_id": "already-fays", "message": "hi"},
        headers=auth_header("user_fay"),
    )

    response = client.post(
        "/api/sessions/already-fays/claim", headers=auth_header("user_gale")
    )
    assert response.status_code == 200
    assert response.json() == {"session_id": "already-fays", "claimed": False}

    fays = client.get("/api/sessions", headers=auth_header("user_fay")).json()["sessions"]
    assert any(session["id"] == "already-fays" for session in fays)
    gales = client.get("/api/sessions", headers=auth_header("user_gale")).json()["sessions"]
    assert not any(session["id"] == "already-fays" for session in gales)


# --------------------------------------------------------------------------- #
# Ownership on the routes that read or continue a chat
# --------------------------------------------------------------------------- #
def test_a_stranger_cannot_read_a_claimed_sessions_messages(
    client: TestClient, auth_header
) -> None:
    client.post(
        "/api/chat",
        json={"session_id": "private-to-holly", "message": "hi"},
        headers=auth_header("user_holly"),
    )

    mine = client.get(
        "/api/sessions/private-to-holly/messages", headers=auth_header("user_holly")
    )
    assert mine.status_code == 200

    anonymous = client.get("/api/sessions/private-to-holly/messages")
    assert anonymous.status_code == 403

    someone_else = client.get(
        "/api/sessions/private-to-holly/messages", headers=auth_header("user_ivan")
    )
    assert someone_else.status_code == 403


def test_a_stranger_cannot_continue_a_claimed_session(client: TestClient, auth_header) -> None:
    client.post(
        "/api/chat",
        json={"session_id": "private-to-jack", "message": "hi"},
        headers=auth_header("user_jack"),
    )

    blocked = client.post(
        "/api/chat",
        json={"session_id": "private-to-jack", "message": "let me in"},
    )
    assert blocked.status_code == 403

    also_blocked = client.post(
        "/api/chat",
        json={"session_id": "private-to-jack", "message": "let me in"},
        headers=auth_header("user_kim"),
    )
    assert also_blocked.status_code == 403

    allowed = client.post(
        "/api/chat",
        json={"session_id": "private-to-jack", "message": "continuing"},
        headers=auth_header("user_jack"),
    )
    assert allowed.status_code == 200


def test_an_unclaimed_session_stays_reachable_by_anyone_who_knows_its_id(
    client: TestClient, auth_header
) -> None:
    """The pre-accounts behaviour, unchanged, for a session nobody has claimed."""
    client.post("/api/chat", json={"session_id": "nobodys-claimed-this", "message": "hi"})

    response = client.post(
        "/api/chat",
        json={"session_id": "nobodys-claimed-this", "message": "still nobody's"},
    )
    assert response.status_code == 200

    as_someone = client.post(
        "/api/chat",
        json={"session_id": "nobodys-claimed-this", "message": "from an account"},
        headers=auth_header("user_liam"),
    )
    assert as_someone.status_code == 200
