"""JWT verification in app.auth, against a Clerk-shaped JWKS.

No network call is exercised here: ``app.auth._client_for`` is monkeypatched
to a stub that hands back a known key pair's public half directly, the way
``PyJWKClient`` would after fetching Clerk's real JWKS. What is under test is
the verification itself - signature, issuer, expiry - not HTTP plumbing.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth import current_user_id, verify_token
from app.config import Settings

ISSUER = "https://test.clerk.accounts.dev"


@pytest.fixture
def keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _token(private_key, *, issuer: str = ISSUER, subject: str = "user_123", expires_in: int = 3600) -> str:
    now = int(time.time())
    payload = {"sub": subject, "iss": issuer, "iat": now, "exp": now + expires_in}
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "test-key"})


def _settings(issuer: str = ISSUER) -> Settings:
    return Settings(clerk_issuer=issuer, mock_mode=True)


def _patch_client(monkeypatch: pytest.MonkeyPatch, public_key) -> None:
    """Stand in for a PyJWKClient that already fetched this public key."""
    signing_key = SimpleNamespace(key=public_key)
    fake_client = SimpleNamespace(get_signing_key_from_jwt=lambda token: signing_key)
    monkeypatch.setattr("app.auth._client_for", lambda issuer: fake_client)


def test_accepts_a_validly_signed_token(monkeypatch, keypair):
    private_key, public_key = keypair
    _patch_client(monkeypatch, public_key)
    assert verify_token(_token(private_key), _settings()) == "user_123"


def test_rejects_a_token_signed_by_a_different_key(monkeypatch, keypair):
    """A forged token: signed by a key that is not the one Clerk published."""
    _, public_key = keypair
    forged_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _patch_client(monkeypatch, public_key)
    assert verify_token(_token(forged_key), _settings()) is None


def test_rejects_an_expired_token(monkeypatch, keypair):
    private_key, public_key = keypair
    _patch_client(monkeypatch, public_key)
    expired = _token(private_key, expires_in=-10)
    assert verify_token(expired, _settings()) is None


def test_rejects_a_token_from_a_different_issuer(monkeypatch, keypair):
    """Guards against a token that is genuinely valid, just not for this app."""
    private_key, public_key = keypair
    _patch_client(monkeypatch, public_key)
    other_issuer = _token(private_key, issuer="https://someone-elses-app.clerk.accounts.dev")
    assert verify_token(other_issuer, _settings()) is None


def test_returns_none_when_no_issuer_is_configured(keypair):
    """Auth is optional end to end: an unconfigured deployment stays anonymous."""
    private_key, _ = keypair
    assert verify_token(_token(private_key), _settings(issuer="")) is None


def test_returns_none_for_garbage_input():
    assert verify_token("not-a-jwt", _settings()) is None


class _FakeRequest:
    def __init__(self, headers: dict[str, str], settings: Settings):
        self.headers = headers
        self.app = SimpleNamespace(state=SimpleNamespace(settings=settings))


def test_current_user_id_reads_the_bearer_header(monkeypatch, keypair):
    private_key, public_key = keypair
    _patch_client(monkeypatch, public_key)
    token = _token(private_key)
    request = _FakeRequest({"authorization": f"Bearer {token}"}, _settings())
    assert current_user_id(request) == "user_123"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"authorization": ""},
        {"authorization": "Bearer "},
        {"authorization": "Basic dXNlcjpwYXNz"},
    ],
)
def test_current_user_id_is_none_without_a_bearer_token(headers):
    request = _FakeRequest(headers, _settings())
    assert current_user_id(request) is None
