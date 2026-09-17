"""The deployed frontend must serve its client-side routes, not 404 them.

Why this module exists
---------------------
GitaGuide's frontend is a single-page app with three routes: ``/`` (the chat),
``/privacy`` and ``/disclaimer`` (the notices the consent gate links to). Only
``/`` is a real file. The other two exist because ``frontend/vercel.json``
rewrites every path to ``/index.html`` and ``main.jsx`` reads
``window.location.pathname``.

On 8 Sep 2026 both notices returned **HTTP 404** in production while the
consent gate said "By continuing you accept the Disclaimer & Beta Terms and
the Privacy Notice". The rewrite was present and correct, and ``vercel.json``
was being read - its ``headers`` block was live on every response. The cause
was ``"cleanUrls": true`` in the same file: it makes Vercel serve ``.html``
pages at extensionless paths and **308-redirect the ``.html`` form**, so
``/index.html`` stopped naming a servable file and became a redirect to ``/``.
The SPA rewrite pointed at it, resolved to nothing, and every client-side route
fell through to ``NOT_FOUND``. Measured directly: ``/index.html`` -> 308 ``/``,
``/privacy.html`` -> 308 ``/privacy`` -> 404.

Vercel's router cannot be exercised from pytest, so what is held here is the
configuration that broke it, plus the agreement between the routes the app
implements and the links that point at them. The live check belongs to
``docs/DEPLOY.md`` step 4 and to the browser walk-through.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
VERCEL_JSON = FRONTEND / "vercel.json"

#: Every path the app answers that is not a file on disk.
CLIENT_ROUTES = ("/privacy", "/disclaimer")


@pytest.fixture(scope="module")
def vercel_config() -> dict:
    return json.loads(VERCEL_JSON.read_text(encoding="utf-8"))


# The rewrite, and the directive that disabled it
def test_every_path_rewrites_to_the_single_page(vercel_config: dict) -> None:
    """Without this, only `/` exists and the notices 404."""
    rewrites = vercel_config.get("rewrites")
    assert rewrites, (
        "frontend/vercel.json declares no rewrites. /privacy and /disclaimer are "
        "client-side routes with no file behind them, so without a catch-all they "
        "return Vercel's 404 page - which is what the consent gate would link to."
    )
    catch_all = [entry for entry in rewrites if entry.get("source") in {"/(.*)", "/:path*"}]
    assert catch_all, f"no catch-all rewrite among {rewrites}"
    assert all(entry.get("destination") == "/index.html" for entry in catch_all)


def test_clean_urls_is_not_enabled(vercel_config: dict) -> None:
    """The 8 Sep production defect, in one assertion.

    `cleanUrls` makes `/index.html` a 308 redirect to `/`, so the rewrite above
    points at something that no longer serves and every client-side route 404s.
    The app has exactly one HTML file and no `.html` pages, so the directive
    buys nothing and costs the notices.
    """
    assert vercel_config.get("cleanUrls") is not True, (
        'frontend/vercel.json sets "cleanUrls": true. That turns /index.html into a '
        "308 redirect to /, which is the destination of the SPA rewrite - so every "
        "client-side route (/privacy, /disclaimer) returns 404. Measured in "
        "production on 8 Sep 2026. Leave it out: there are no .html pages to clean."
    )


def test_the_security_headers_are_still_declared(vercel_config: dict) -> None:
    """Removing cleanUrls must not have taken the headers block with it."""
    sources = {entry.get("source") for entry in vercel_config.get("headers", [])}
    assert "/(.*)" in sources and "/assets/(.*)" in sources
    keys = {
        header["key"]
        for entry in vercel_config["headers"]
        for header in entry["headers"]
    }
    assert {"X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy"} <= keys


# The routes the app implements, and the links that point at them
def test_the_router_answers_exactly_the_documented_routes() -> None:
    source = (FRONTEND / "src" / "components" / "StaticPage.jsx").read_text(encoding="utf-8")
    for route in CLIENT_ROUTES:
        assert f"'{route}'" in source, f"StaticPage.docForPath does not handle {route}"


def test_main_dispatches_on_the_path() -> None:
    """A rewrite that serves index.html for /privacy only helps if the app reads it."""
    source = (FRONTEND / "src" / "main.jsx").read_text(encoding="utf-8")
    assert "docForPath" in source
    assert "window.location.pathname" in source


@pytest.mark.parametrize("route", CLIENT_ROUTES)
def test_the_consent_gate_and_footer_link_to_routes_that_exist(route: str) -> None:
    """Any href the UI offers must be a path the rewrite plus the router answer."""
    for name in ("ConsentGate.jsx", "Composer.jsx"):
        source = (FRONTEND / "src" / "components" / name).read_text(encoding="utf-8")
        assert f'href="{route}"' in source, f"{name} does not link to {route}"


def test_no_component_links_to_a_path_the_app_cannot_serve() -> None:
    """Catches a future link to /terms or /faq that nothing would answer."""
    served = {"/", *CLIENT_ROUTES}
    for path in (FRONTEND / "src").rglob("*.jsx"):
        for href in re.findall(r'href="(/[^"]*)"', path.read_text(encoding="utf-8")):
            assert href in served, (
                f"{path.name} links to {href!r}, which is neither a file nor a route "
                f"StaticPage handles. Add it to docForPath and to CLIENT_ROUTES here, "
                "or the link will 404 for testers."
            )
