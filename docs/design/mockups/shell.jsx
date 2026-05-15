/* global React */
// Shared shell elements: top chrome nav, watchlist data, common ticker fixtures.

const NAV_ITEMS = [
  { key: "home",     label: "今日",   href: "/" },
  { key: "stock",    label: "行情",   href: "/stock/AAPL", active: true },
  { key: "watchlist",label: "自选股", href: "/watchlist" },
  { key: "holdings", label: "持仓",   href: "/holdings" },
  { key: "trades",   label: "交易",   href: "/trades" },
  { key: "recaps",   label: "复盘",   href: "/recaps" },
  { key: "alerts",   label: "告警",   href: "/alerts" },
];

// Build watchlist with realistic snapshots. Each item gets a small 30-day sparkline.
function buildWatchlist() {
  const items = [
    { ticker: "AAPL",  name: "Apple Inc.",        price: 221.34,  chg:  2.41, pct:  1.10, vol: "62.4M", mc: "3.41T", seed: 42 },
    { ticker: "NVDA",  name: "NVIDIA Corp.",      price: 138.07,  chg: -3.18, pct: -2.25, vol: "238M",  mc: "3.39T", seed: 17 },
    { ticker: "MSFT",  name: "Microsoft",         price: 442.95,  chg:  5.82, pct:  1.33, vol: "21.0M", mc: "3.29T", seed: 9  },
    { ticker: "TSLA",  name: "Tesla, Inc.",       price: 264.18,  chg:  9.74, pct:  3.83, vol: "104M",  mc: "843B",  seed: 71 },
    { ticker: "GOOGL", name: "Alphabet Inc. A",   price: 197.84,  chg:  0.62, pct:  0.31, vol: "18.2M", mc: "2.42T", seed: 23 },
    { ticker: "AMZN",  name: "Amazon.com",        price: 232.55,  chg:  1.18, pct:  0.51, vol: "32.7M", mc: "2.47T", seed: 11 },
    { ticker: "META",  name: "Meta Platforms",    price: 588.13,  chg: -4.27, pct: -0.72, vol: "11.5M", mc: "1.49T", seed: 88 },
    { ticker: "AVGO",  name: "Broadcom Inc.",     price: 178.32,  chg:  3.91, pct:  2.24, vol: "26.8M", mc: "833B",  seed: 56 },
    { ticker: "TSM",   name: "TSMC ADR",          price: 196.45,  chg:  2.18, pct:  1.12, vol: "9.4M",  mc: "1.02T", seed: 33 },
    { ticker: "SPY",   name: "S&P 500 ETF",       price: 597.18,  chg:  1.42, pct:  0.24, vol: "44.0M", mc: "—",     seed: 5  },
  ];
  return items.map(it => {
    const candles = MPData.generateCandles({ seed: it.seed, count: 30, basePrice: it.price / (1 + it.pct/100/3), vol: 0.012 });
    // adjust final close to match price
    candles[candles.length-1].close = it.price;
    return { ...it, sparkValues: candles.map(c => c.close) };
  });
}

window.MPShell = { NAV_ITEMS, buildWatchlist };
