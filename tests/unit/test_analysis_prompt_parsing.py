"""Parse AiService.analyze() AI response: extract VERDICTS_JSON object."""


def test_parse_with_valid_verdicts_object():
    from marketpulse.ai.service import _parse_analyze_output

    raw = (
        "## 基本面\n\n苹果财务稳健。\n\n"
        "## 技术面\n\nRSI 60。\n\n"
        "## 风险\n\nAI 资本开支。\n\n"
        "VERDICTS_JSON: "
        "{\"ticker\": \"AAPL\", \"verdict\": \"bullish\", \"rationale\": \"基本面强\"}"
    )
    md, verdict = _parse_analyze_output(raw)
    assert "## 基本面" in md
    assert "VERDICTS_JSON" not in md
    assert verdict is not None
    assert verdict["ticker"] == "AAPL"
    assert verdict["verdict"] == "bullish"
    assert verdict["rationale"] == "基本面强"


def test_parse_without_verdicts_marker_returns_none():
    from marketpulse.ai.service import _parse_analyze_output

    raw = "## 基本面\n\n没有 verdicts 标记的分析。"
    md, verdict = _parse_analyze_output(raw)
    assert md == raw
    assert verdict is None


def test_parse_malformed_verdicts_json_returns_none():
    from marketpulse.ai.service import _parse_analyze_output

    raw = "## 基本面\n\n正文。\n\nVERDICTS_JSON: not-a-json"
    md, verdict = _parse_analyze_output(raw)
    assert "## 基本面" in md
    assert verdict is None


def test_parse_verdicts_object_missing_ticker_field():
    """JSON valid but missing required field — return as-is, caller validates."""
    from marketpulse.ai.service import _parse_analyze_output

    raw = "## 基本面\n\n正文。\n\nVERDICTS_JSON: {\"verdict\": \"bullish\"}"
    md, verdict = _parse_analyze_output(raw)
    # Returns the dict; caller checks for required keys
    assert verdict == {"verdict": "bullish"}


def test_parse_marker_quoted_in_body_uses_rfind():
    """AI references KEY_EVENTS_JSON: in the body before the real one."""
    from marketpulse.ai.service import _parse_analyze_output

    raw = (
        "## 基本面\n\n"
        "VERDICTS_JSON: 这个标记是格式占位说明。\n\n"
        "VERDICTS_JSON: {\"ticker\": \"AAPL\", \"verdict\": \"bullish\", \"rationale\": \"x\"}"
    )
    md, verdict = _parse_analyze_output(raw)
    # rfind finds the LAST occurrence — the real structured tail
    assert verdict is not None
    assert verdict["ticker"] == "AAPL"
