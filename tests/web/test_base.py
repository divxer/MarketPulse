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
