import json
import logging

from marketpulse.logging import configure_logging, get_logger


def test_logger_emits_json(capsys) -> None:
    configure_logging("INFO")
    log = get_logger("test")
    log.info("hello", ticker="AAPL", value=1)
    captured = capsys.readouterr()
    line = captured.out.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "hello"
    assert payload["ticker"] == "AAPL"
    assert payload["value"] == 1
