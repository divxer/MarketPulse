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
    from marketpulse.web.static_versioning import (
        configure,
        static_version,
    )
    from pathlib import Path

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
