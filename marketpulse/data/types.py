from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Quote:
    ticker: str
    price: float
    change_pct: float
    volume: int
    avg_volume_20d: int
    fetched_at: datetime
    stale: bool = False


@dataclass(frozen=True)
class Bar:
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class NewsItem:
    ticker: str
    headline: str
    url: str
    published_at: datetime
    source: str
    summary: str | None = None


@dataclass(frozen=True)
class Fundamentals:
    ticker: str
    market_cap: float | None
    pe_ratio: float | None
    eps: float | None
    sector: str | None
    industry: str | None


@dataclass(frozen=True)
class IndexQuote:
    symbol: str
    price: float
    change_pct: float


@dataclass(frozen=True)
class MarketOverview:
    spy: IndexQuote
    qqq: IndexQuote
    dia: IndexQuote
    vix: IndexQuote
    fetched_at: datetime
