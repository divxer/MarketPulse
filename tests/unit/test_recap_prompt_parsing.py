"""Parse AI commentary output: extract Markdown body + KEY_EVENTS_JSON."""
import json


def test_parse_with_valid_marker_and_json():
    from marketpulse.recap.service import _parse_ai_output

    raw = (
        "## 大盘\n\n标普 500 收 `5,973.10` (+0.24%) 。\n\n"
        "## 板块与个股\n\n半导体回吐。\n\n"
        "---\n\n"
        "KEY_EVENTS_JSON: ["
        "{\"time\": \"16:00 EDT\", \"title\": \"AVGO 与 AAPL 协议\", \"kind\": \"deal\"}"
        "]"
    )
    commentary, events_json = _parse_ai_output(raw)
    assert "## 大盘" in commentary
    assert "## 板块与个股" in commentary
    assert "KEY_EVENTS_JSON" not in commentary
    assert events_json is not None
    events = json.loads(events_json)
    assert len(events) == 1
    assert events[0]["title"] == "AVGO 与 AAPL 协议"
    assert events[0]["kind"] == "deal"


def test_parse_without_marker_returns_raw_commentary_and_none_events():
    """No KEY_EVENTS_JSON marker → entire raw is commentary, events=None."""
    from marketpulse.recap.service import _parse_ai_output

    raw = "## 大盘\n\n这是一段没有 events 标记的复盘。"
    commentary, events_json = _parse_ai_output(raw)
    assert commentary == raw
    assert events_json is None


def test_parse_malformed_json_falls_back_to_none_events():
    """Marker present but invalid JSON → commentary preserved, events=None."""
    from marketpulse.recap.service import _parse_ai_output

    raw = "## 大盘\n\n复盘正文。\n\nKEY_EVENTS_JSON: not-a-json-array"
    commentary, events_json = _parse_ai_output(raw)
    # Note: rstrip removes the trailing newlines/whitespace after "正文。"
    assert "复盘正文" in commentary
    assert "KEY_EVENTS_JSON" not in commentary
    assert events_json is None


def test_parse_events_not_a_list_falls_back():
    """KEY_EVENTS_JSON value is a dict not a list → events=None."""
    from marketpulse.recap.service import _parse_ai_output

    raw = "正文\n\nKEY_EVENTS_JSON: {\"a\": 1}"
    commentary, events_json = _parse_ai_output(raw)
    assert events_json is None


def test_parse_strips_trailing_whitespace_in_commentary():
    from marketpulse.recap.service import _parse_ai_output

    raw = "## 大盘\n\n正文\n\n   \n\nKEY_EVENTS_JSON: []"
    commentary, events_json = _parse_ai_output(raw)
    assert commentary == "## 大盘\n\n正文"
    assert events_json == "[]"


def test_parse_empty_events_array():
    from marketpulse.recap.service import _parse_ai_output

    raw = "正文\n\nKEY_EVENTS_JSON: []"
    commentary, events_json = _parse_ai_output(raw)
    assert events_json == "[]"


def test_parse_with_marker_quoted_in_commentary():
    """AI quoting 'KEY_EVENTS_JSON:' in body — rfind ensures we split at
    the last (real) occurrence, not the first (quoted) one."""
    from marketpulse.recap.service import _parse_ai_output

    raw = (
        "## 大盘\n\n"
        "正文里提到了 KEY_EVENTS_JSON: 这个标记作为格式说明,但这并非真正的事件分隔符。\n\n"
        "KEY_EVENTS_JSON: ["
        "{\"time\": \"10:00\", \"title\": \"真正的事件\", \"kind\": \"deal\"}"
        "]"
    )
    commentary, events_json = _parse_ai_output(raw)
    # Commentary should preserve the quoted KEY_EVENTS_JSON: mention
    assert "正文里提到了 KEY_EVENTS_JSON:" in commentary
    # And events should be the JSON after the LAST occurrence
    assert events_json is not None
    events = json.loads(events_json)
    assert events[0]["title"] == "真正的事件"
