"""Manual smoke test against real yfinance + Anthropic.

Run: uv run python scripts/smoke_test.py
"""
from __future__ import annotations

import sys

from marketpulse.config import get_settings
from marketpulse.data.yfinance_client import YFinanceClient


def main() -> int:
    get_settings()  # validates env
    yf = YFinanceClient()
    print("Fetching AAPL quote …")
    q = yf.fetch_quote("AAPL")
    print(f"  price={q.price:.2f} change={q.change_pct:+.2f}%")
    print("Fetching market overview …")
    m = yf.fetch_market_overview()
    print(f"  SPY={m.spy.change_pct:+.2f}% QQQ={m.qqq.change_pct:+.2f}% VIX={m.vix.price:.2f}")
    if "--with-ai" in sys.argv:
        from marketpulse.ai.client import AnthropicClient
        ai = AnthropicClient()
        out = ai.complete(system="You are concise.", user="Say 'ok'.")
        print(f"  AI replied: {out!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
