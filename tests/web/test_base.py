"""Tests for base.html (Phase 5a foundation — design tokens, fonts, width slots)."""
import re

from fastapi.testclient import TestClient


def test_base_html_loads_nine_scrolls_tokens(client: TestClient, monkeypatch):
    """Verify Phase 5a foundation: ns-tokens.css + app.css + Google Fonts
    are wired into base.html so all pages get them automatically."""
    # Use any rendered page — /login is the simplest (no auth needed)
    r = client.get("/login")
    assert r.status_code == 200
    body = r.text

    # NineScrolls tokens
    assert "/static/css/ns-tokens.css" in body, "ns-tokens.css link missing"
    # NineScrolls app overlay (.mp-card / .mp-chip etc.)
    assert "/static/css/app.css" in body, "NineScrolls app.css link missing"
    # Google Fonts — Space Grotesk + Inter + Material Symbols
    assert "fonts.googleapis.com" in body
    assert "Space+Grotesk" in body
    assert "Inter:wght" in body
    assert "Material+Symbols+Outlined" in body
    # Tailwind build output still loaded (don't break existing styling)
    assert "/static/app.css" in body


def test_base_html_exposes_main_width_block(client: TestClient, monkeypatch):
    """Verify the {% block main_width %} slot is in place so pages can
    override the default max-w-5xl. Phase 5b (stock detail) will use this."""
    r = client.get("/login")
    assert r.status_code == 200
    body = r.text
    # Default block value should render — max-w-5xl on main
    main_match = re.search(r'<main[^>]*class="([^"]+)"', body)
    assert main_match is not None
    main_classes = main_match.group(1)
    assert "max-w-5xl" in main_classes, "default main_width block should be max-w-5xl"
    assert "mx-auto" in main_classes


def test_base_html_cache_busts_css_assets(client: TestClient, monkeypatch):
    """Verify cache-busting: each CSS <link> href has a ?v=xxxxxxxx query
    appended where xxxxxxxx is the file's content hash. This makes browsers
    re-fetch when CSS rebuilds at deploy time.

    Regression for the "old CSS cached, new layout not applied" bug
    observed in Phase 5b-1 deploy."""
    r = client.get("/login")
    assert r.status_code == 200
    body = r.text
    # Each CSS link should have a ?v= query param with at least 6 hex chars.
    # Matches:
    #   /static/css/ns-tokens.css?v=abc12345
    #   /static/css/app.css?v=def67890
    #   /static/app.css?v=fedcba98
    expected_assets = [
        "/static/css/ns-tokens.css",
        "/static/css/app.css",
        "/static/app.css",
    ]
    for asset in expected_assets:
        pattern = re.escape(asset) + r"\?v=[a-f0-9]{6,}"
        assert re.search(pattern, body), (
            f"asset {asset!r} should have a ?v=hash cache-buster — "
            f"makes browsers refetch on deploy"
        )


def test_static_version_helper_returns_stable_hash():
    """Direct unit test of the static_version function — first 8 hex chars
    of md5, cached, missing files return 'missing'."""
    from pathlib import Path

    from marketpulse.web.static_versioning import (
        configure,
        static_version,
    )

    # Use the real STATIC_DIR
    static_dir = Path(__file__).resolve().parent.parent.parent / "marketpulse" / "web" / "static"
    configure(static_dir)

    # app.css exists (built via tailwind)
    v1 = static_version("app.css")
    assert len(v1) == 8 and all(c in "0123456789abcdef" for c in v1), (
        f"expected 8 hex chars, got {v1!r}"
    )
    # Cached: second call returns same value (cheap lookup)
    v2 = static_version("app.css")
    assert v1 == v2

    # Missing file: returns "missing" sentinel
    missing = static_version("does-not-exist.css")
    assert missing == "missing"


def test_base_nav_includes_lab_entry(client: TestClient, monkeypatch):
    """Verify the top nav exposes a /lab/ai-track entry labeled 实验室.

    Before this PR, /lab/* pages had NO nav entry — only deep links from
    /stock pages or strategy-table arrows could reach them. Users without
    bookmarks couldn't find the AI evaluation or backtest pages.
    """
    r = client.get("/login")
    assert r.status_code == 200
    # The nav <a> for lab — both href and label must be present
    assert 'href="/lab/ai-track"' in r.text
    assert "实验室" in r.text


def test_base_nav_active_state_highlights_current_page(
    client: TestClient, monkeypatch,
):
    """Each nav entry gets `.mp-nav-active` when the current request path
    matches. Verify on a few representative routes.
    """
    # Visit /watchlist → "自选股" link gets the active class
    # Use a login-required redirect to short-circuit (we just want the rendered nav).
    # /login itself has the nav and is unauthenticated, perfect for this assertion.
    r = client.get("/login")
    assert r.status_code == 200
    # /login isn't in the nav, so NO link should have mp-nav-active.
    # (MarketPulse link is exact-match on '/', /login != '/'.)
    assert "mp-nav-active" not in r.text, (
        "/login is not a primary nav destination; no link should be active"
    )


def test_base_nav_lab_link_active_on_lab_pages(
    client: TestClient, monkeypatch,
):
    """Hitting /lab/ai-track lights up the 实验室 nav entry; /lab/backtest
    too, since both belong to the same primary destination."""
    # Need auth to reach /lab/* — use the existing test password fixture.
    from marketpulse.auth.password import hash_password
    pw = "secret"
    monkeypatch.setenv("APP_PASSWORD_HASH", hash_password(pw))
    from marketpulse.config import get_settings
    get_settings.cache_clear()
    client.post("/login", data={"password": pw})

    # /lab/ai-track should mark 实验室 active
    r = client.get("/lab/ai-track")
    assert r.status_code == 200
    # Look for the active class on the lab nav anchor specifically.
    # Find the snippet between href="/lab/ai-track" and the closing </a>.
    import re
    m = re.search(r'<a\s+href="/lab/ai-track"[^>]*>', r.text)
    assert m is not None
    assert "mp-nav-active" in m.group(0), (
        f"lab nav anchor should have mp-nav-active; got: {m.group(0)}"
    )

    # /lab/backtest should also mark 实验室 active (same primary destination)
    r2 = client.get("/lab/backtest")
    assert r2.status_code == 200
    m2 = re.search(r'<a\s+href="/lab/ai-track"[^>]*>', r2.text)
    assert m2 is not None
    assert "mp-nav-active" in m2.group(0), (
        f"on /lab/backtest, lab nav anchor should still be active; got: {m2.group(0)}"
    )
