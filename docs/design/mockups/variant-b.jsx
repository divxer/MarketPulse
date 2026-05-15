/* global React */
// Variant B — Bloomberg-terminal dense dark
// Densely packed multi-column layout, amber accents on near-black surface,
// function-code panel titles (DES / GP / RSI / MACD / NEWS / OMS).

const { useState: useStateB, useMemo: useMemoB } = React;

function VariantB() {
  const [period, setPeriod] = useStateB("6M");
  const watchlist = useMemoB(() => MPShell.buildWatchlist(), []);
  const meta = watchlist[0];

  const periodCounts = { "1D": 60, "5D": 30, "60D": 60, "6M": 130, "YTD": 95, "1Y": 250, "5Y": 260 };
  const count = periodCounts[period] || 130;
  const candles = useMemoB(() => {
    const c = MPData.generateCandles({ seed: meta.seed + count, count, basePrice: meta.price * 0.78, vol: 0.018, drift: 0.0018 });
    const last = c[c.length - 1];
    last.close = meta.price;
    last.open = meta.price - meta.chg;
    last.high = Math.max(last.high, meta.price);
    return c;
  }, [meta, count]);

  const tapeItems = [
    { sym: "DJIA",    val: "43,118.45", chg: "+218.30", pct: "+0.51", up: true },
    { sym: "SPX",     val: "5,973.10",  chg: "+14.21",  pct: "+0.24", up: true },
    { sym: "NDX",     val: "21,114.20", chg: "+92.18",  pct: "+0.44", up: true },
    { sym: "VIX",     val: "14.18",     chg: "-0.42",   pct: "-2.88", up: false },
    { sym: "US10Y",   val: "4.18%",     chg: "-3 bp",   pct: "",      up: false },
    { sym: "WTI",     val: "$68.42",    chg: "+0.91",   pct: "+1.35", up: true },
    { sym: "GOLD",    val: "$2,617.40", chg: "-8.20",   pct: "-0.31", up: false },
    { sym: "BTC",     val: "$94,180",   chg: "+1,820",  pct: "+1.97", up: true },
    { sym: "DXY",     val: "104.22",    chg: "+0.18",   pct: "+0.17", up: true },
    { sym: "USDCNH",  val: "7.2440",    chg: "-0.0080", pct: "-0.11", up: false },
  ];

  return (
    <div className="bb-root" style={{ width: 2560, minHeight: 1700, display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <BBChrome />
      <BBTape items={tapeItems} />
      <BBQuoteStrip meta={meta} />

      <div style={{ display: "grid", gridTemplateColumns: "300px minmax(0, 1fr) 380px 340px", gap: 8, padding: 8, flex: 1 }}>
        {/* COL 1 — security description */}
        <div style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
          <BBDescPanel meta={meta} />
          <BBKeyStatsPanel meta={meta} />
          <BBHoldingPanel meta={meta} />
        </div>

        {/* COL 2 — chart + RSI + MACD */}
        <div style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
          <BBChartPanel meta={meta} candles={candles} period={period} setPeriod={setPeriod} />
          <BBRSIPanel candles={candles} />
          <BBMACDPanel candles={candles} />
        </div>

        {/* COL 3 — watchlist + recent trades */}
        <div style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
          <BBWatchPanel items={watchlist} />
          <BBTradesPanel />
        </div>

        {/* COL 4 — AI / Record / News */}
        <div style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
          <BBOrderPanel meta={meta} />
          <BBNewsPanel meta={meta} />
          <BBAIPanel meta={meta} />
        </div>
      </div>

      <BBFooter />
    </div>
  );
}

// ─── Chrome ──────────────────────────────────────────
function BBChrome() {
  return (
    <header className="bb-chrome">
      <div className="bb-chrome__brand">MARKETPULSE<span style={{ color: "var(--bb-ink-mute)", fontFamily: "var(--ns-font-mono)", fontSize: 10, marginLeft: 6, letterSpacing: 0 }}>v3.18</span></div>
      <div className="bb-chrome__cmd">
        <span className="material-symbols-outlined">terminal</span>
        <input defaultValue="AAPL US EQUITY DES" />
        <span className="bb-amb" style={{ fontFamily: "var(--ns-font-mono)", fontSize: 11, padding: "2px 6px", border: "1px solid var(--bb-amber)" }}>{"<GO>"}</span>
      </div>
      <nav className="bb-nav">
        <a className="is-active" href="#">DES</a>
        <a href="#">GP</a>
        <a href="#">FA</a>
        <a href="#">RV</a>
        <a href="#">PORT</a>
        <a href="#">TRDE</a>
        <a href="#">RCAP</a>
        <a href="#">ALRT</a>
        <a href="#">SETUP</a>
      </nav>
      <div style={{ display: "flex", alignItems: "center", gap: 16, color: "var(--bb-ink-mute)", fontFamily: "var(--ns-font-mono)", fontSize: 11 }}>
        <span style={{ color: "var(--bb-up)", display: "inline-flex", alignItems: "center", gap: 6 }}>
          <span className="mp-pulse" style={{ background: "var(--bb-up)" }} />
          NYSE · OPEN
        </span>
        <span>10:42:18 EDT</span>
        <span className="bb-amb">YH · DESK A · L2</span>
      </div>
    </header>
  );
}

function BBTape({ items }) {
  return (
    <div className="bb-tape">
      {items.map((t, i) => (
        <span key={i} className="bb-tape__item">
          <span className="bb-tape__sym">{t.sym}</span>
          <span style={{ color: "var(--bb-ink-dim)" }}>{t.val}</span>
          <span className={t.up ? "bb-up" : "bb-down"}>{t.chg}{t.pct ? ` (${t.pct}%)` : ""}</span>
        </span>
      ))}
    </div>
  );
}

function BBQuoteStrip({ meta }) {
  const up = meta.chg >= 0;
  return (
    <div style={{ display: "flex", alignItems: "stretch", background: "var(--bb-bg-2)", borderBottom: "1px solid var(--bb-line)" }}>
      {/* Symbol */}
      <div style={{ padding: "12px 24px", borderRight: "1px solid var(--bb-line)", minWidth: 360 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
          <span className="grotesk bb-amb" style={{ fontSize: 28, fontWeight: 700, letterSpacing: "-0.03em" }}>{meta.ticker}</span>
          <span style={{ color: "var(--bb-ink-dim)", fontSize: 12, fontFamily: "var(--ns-font-mono)" }}>US</span>
          <span style={{ color: "var(--bb-ink-mute)", fontSize: 11, fontFamily: "var(--ns-font-mono)" }}>EQUITY · NASDAQ · USD</span>
        </div>
        <div style={{ color: "var(--bb-ink-dim)", fontSize: 12, marginTop: 2 }}>{meta.name} · Common Stock</div>
      </div>
      {/* Price */}
      <div style={{ padding: "12px 24px", borderRight: "1px solid var(--bb-line)" }}>
        <div className="mono" style={{ fontSize: 36, fontWeight: 600, color: "var(--bb-ink)", letterSpacing: "-0.01em", lineHeight: 1 }}>
          {meta.price.toFixed(2)}<span style={{ fontSize: 14, color: "var(--bb-ink-mute)", fontWeight: 400, marginLeft: 8 }}>USD</span>
        </div>
        <div className={"mono " + (up ? "bb-up" : "bb-down")} style={{ fontSize: 14, fontWeight: 600, marginTop: 4 }}>
          {up ? "▲" : "▼"} {meta.chg >= 0 ? "+" : ""}{meta.chg.toFixed(2)} ({meta.chg >= 0 ? "+" : ""}{meta.pct.toFixed(2)}%)
        </div>
      </div>
      {/* OHLC grid */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(8, auto)", padding: "12px 20px", gap: "4px 20px", borderRight: "1px solid var(--bb-line)", alignContent: "center", fontFamily: "var(--ns-font-mono)", fontSize: 11.5 }}>
        <BBStat label="OPEN" value="219.80" />
        <BBStat label="HIGH" value="222.10" cls="bb-up" />
        <BBStat label="LOW" value="218.95" cls="bb-down" />
        <BBStat label="PREV" value={(meta.price - meta.chg).toFixed(2)} />
        <BBStat label="VOL" value={meta.vol} />
        <BBStat label="VWAP" value="220.84" />
        <BBStat label="AVG VOL 20D" value="58.7M" />
        <BBStat label="MKT CAP" value={meta.mc} />
        <BBStat label="BID" value="221.32 × 800" />
        <BBStat label="ASK" value="221.35 × 1200" />
        <BBStat label="52W HI" value="251.20" />
        <BBStat label="52W LO" value="164.07" />
        <BBStat label="P/E" value="36.4" />
        <BBStat label="EPS TTM" value="6.08" />
        <BBStat label="YIELD" value="0.43%" />
        <BBStat label="BETA" value="1.21" />
      </div>
      {/* Actions */}
      <div style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 6, marginLeft: "auto" }}>
        <button className="bb-amb" style={btnAmber}>BUY 100 @ MKT</button>
        <button style={btnDownB}>SELL 100 @ MKT</button>
        <div style={{ display: "flex", gap: 6 }}>
          <button style={btnGhostB}>☆ WATCH</button>
          <button style={btnGhostB}>🤖 AI · GO</button>
        </div>
      </div>
    </div>
  );
}
function BBStat({ label, value, cls }) {
  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      <span style={{ color: "var(--bb-ink-mute)", fontSize: 9.5, letterSpacing: "0.12em" }}>{label}</span>
      <span className={cls || "bb-dim"} style={{ fontSize: 12, fontWeight: 600 }}>{value}</span>
    </div>
  );
}
const btnAmber = { background: "var(--bb-amber)", color: "#1a0e00", border: "0", padding: "6px 14px", fontFamily: "var(--ns-font-headline)", fontWeight: 700, fontSize: 11.5, letterSpacing: "0.08em", cursor: "pointer" };
const btnDownB = { background: "var(--bb-down)", color: "#fff", border: "0", padding: "6px 14px", fontFamily: "var(--ns-font-headline)", fontWeight: 700, fontSize: 11.5, letterSpacing: "0.08em", cursor: "pointer" };
const btnGhostB = { background: "transparent", color: "var(--bb-ink-dim)", border: "1px solid var(--bb-line)", padding: "5px 10px", fontFamily: "var(--ns-font-headline)", fontWeight: 600, fontSize: 10.5, letterSpacing: "0.08em", cursor: "pointer", flex: 1 };

// ─── Panel chrome ────────────────────────────────────
function BBPanel({ code, title, right, children, padded = true }) {
  return (
    <div className="bb-panel" style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div className="bb-panel__head">
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <span className="bb-amb mono" style={{ fontSize: 10, letterSpacing: "0.12em", fontWeight: 700 }}>{code}</span>
          <span className="bb-panel__title bb-panel__title--ink">{title}</span>
        </div>
        <div className="bb-panel__actions">{right}</div>
      </div>
      <div className="bb-panel__body" style={{ padding: padded ? 12 : 0, flex: 1, minHeight: 0 }}>{children}</div>
    </div>
  );
}

// ─── Col 1: Description ──────────────────────────────
function BBDescPanel({ meta }) {
  return (
    <BBPanel code="DES" title="Security Description">
      <div style={{ fontSize: 11.5, color: "var(--bb-ink-dim)", lineHeight: 1.6 }}>
        Apple Inc. designs, manufactures and markets smartphones, personal computers, tablets, wearables and accessories worldwide. The company also offers services including AppleCare, advertising, AppleCloud, Apple Music, and the App Store.
      </div>
      <hr style={{ border: 0, borderTop: "1px solid var(--bb-line)", margin: "10px 0" }} />
      <table style={{ width: "100%", fontSize: 11, fontFamily: "var(--ns-font-mono)" }}>
        <tbody>
          <BBDefRow label="SECTOR" value="Technology" />
          <BBDefRow label="INDUSTRY" value="Consumer Electronics" />
          <BBDefRow label="EXCHANGE" value="NASDAQ-NMS" />
          <BBDefRow label="HQ" value="Cupertino, CA" />
          <BBDefRow label="EMPLOYEES" value="164,000" />
          <BBDefRow label="FOUNDED" value="1976" />
          <BBDefRow label="CEO" value="Tim Cook" />
          <BBDefRow label="ISIN" value="US0378331005" />
        </tbody>
      </table>
    </BBPanel>
  );
}
function BBDefRow({ label, value }) {
  return (
    <tr>
      <td style={{ color: "var(--bb-ink-mute)", padding: "3px 0", letterSpacing: "0.08em", fontSize: 10 }}>{label}</td>
      <td style={{ color: "var(--bb-ink)", textAlign: "right", padding: "3px 0" }}>{value}</td>
    </tr>
  );
}

function BBKeyStatsPanel({ meta }) {
  return (
    <BBPanel code="KEY" title="Key Stats · TTM">
      <table style={{ width: "100%", fontSize: 11, fontFamily: "var(--ns-font-mono)" }}>
        <tbody>
          <BBDefRow label="REVENUE" value="$394.3B" />
          <BBDefRow label="GROSS MGN" value="46.2%" />
          <BBDefRow label="OPER MGN" value="31.5%" />
          <BBDefRow label="NET INCOME" value="$96.99B" />
          <BBDefRow label="FCF" value="$108.8B" />
          <BBDefRow label="CASH" value="$65.2B" />
          <BBDefRow label="DEBT" value="$104.6B" />
          <BBDefRow label="ROE" value="160.6%" />
          <BBDefRow label="DIV YLD" value="0.43%" />
          <BBDefRow label="PAYOUT" value="14.9%" />
        </tbody>
      </table>
    </BBPanel>
  );
}

function BBHoldingPanel({ meta }) {
  const qty = 50, avg = 172.40, cost = qty * avg, mv = qty * meta.price, pl = mv - cost, plPct = pl / cost * 100;
  const up = pl >= 0;
  return (
    <BBPanel code="OMS" title="Your Position">
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, fontFamily: "var(--ns-font-mono)" }}>
        <BBField label="QTY" value={qty} />
        <BBField label="AVG" value={"$" + avg.toFixed(2)} />
        <BBField label="COST" value={"$" + cost.toFixed(2)} />
        <BBField label="MKT VAL" value={"$" + mv.toFixed(2)} />
      </div>
      <div style={{ marginTop: 10, padding: 10, background: up ? "var(--bb-up-soft)" : "var(--bb-down-soft)", border: `1px solid ${up ? "var(--bb-up)" : "var(--bb-down)"}` }}>
        <div style={{ fontSize: 9.5, letterSpacing: "0.16em", color: "var(--bb-ink-mute)" }}>UNREALIZED P&L</div>
        <div className={"mono " + (up ? "bb-up" : "bb-down")} style={{ fontSize: 22, fontWeight: 600, marginTop: 2 }}>
          {up ? "+" : ""}${Math.abs(pl).toFixed(2)}
        </div>
        <div className={"mono " + (up ? "bb-up" : "bb-down")} style={{ fontSize: 11, marginTop: 2 }}>
          {up ? "+" : ""}{plPct.toFixed(2)}% · DAY {qty * meta.chg >= 0 ? "+" : ""}${(qty * meta.chg).toFixed(2)}
        </div>
      </div>
      <div style={{ marginTop: 8, display: "flex", justifyContent: "space-between", fontFamily: "var(--ns-font-mono)", fontSize: 10.5 }}>
        <span style={{ color: "var(--bb-ink-mute)" }}>HELD 312D · 18.4% OF PORT</span>
        <span className="bb-amb">[J] HISTORY</span>
      </div>
    </BBPanel>
  );
}
function BBField({ label, value }) {
  return (
    <div>
      <div style={{ color: "var(--bb-ink-mute)", fontSize: 9.5, letterSpacing: "0.14em" }}>{label}</div>
      <div style={{ color: "var(--bb-ink)", fontSize: 13, fontWeight: 600, marginTop: 2 }}>{value}</div>
    </div>
  );
}

// ─── Col 2: Chart panels ─────────────────────────────
function BBChartPanel({ meta, candles, period, setPeriod }) {
  const periods = ["1D", "5D", "60D", "6M", "YTD", "1Y", "5Y"];
  return (
    <BBPanel
      code="GP"
      title={`Graph Price · ${meta.ticker} Daily`}
      right={
        <div style={{ display: "flex", gap: 4 }}>
          {periods.map(p => (
            <span key={p} onClick={() => setPeriod(p)}
              className={period === p ? "bb-amb" : "bb-mute"}
              style={{ padding: "3px 8px", border: `1px solid ${period === p ? "var(--bb-amber)" : "var(--bb-line)"}`, cursor: "pointer", fontFamily: "var(--ns-font-headline)", fontSize: 10, letterSpacing: "0.06em" }}>
              {p}
            </span>
          ))}
        </div>
      }
      padded={false}
    >
      <div style={{ padding: "8px 12px 0", display: "flex", justifyContent: "space-between", alignItems: "center", fontFamily: "var(--ns-font-mono)", fontSize: 11 }}>
        <div style={{ display: "flex", gap: 16 }}>
          <span><span className="bb-mute">O</span> <span className="bb-dim">219.80</span></span>
          <span><span className="bb-mute">H</span> <span className="bb-up">222.10</span></span>
          <span><span className="bb-mute">L</span> <span className="bb-down">218.95</span></span>
          <span><span className="bb-mute">C</span> <span className="bb-ink">{meta.price.toFixed(2)}</span></span>
          <span><span className="bb-mute">V</span> <span className="bb-dim">{meta.vol}</span></span>
        </div>
        <div style={{ display: "flex", gap: 12 }}>
          <span><span style={{ width: 8, height: 2, background: "#5fc7ff", display: "inline-block", marginRight: 4 }}/>SMA 50</span>
          <span><span style={{ width: 8, height: 2, background: "#ffb44a", display: "inline-block", marginRight: 4 }}/>SMA 200</span>
          <span><span style={{ width: 8, height: 2, background: "rgba(255,180,74,0.5)", display: "inline-block", marginRight: 4 }}/>BB · 20,2</span>
        </div>
      </div>
      <CandleChart candles={candles} width={1568} height={460} theme="dark" showSMA showBB showVolume />
    </BBPanel>
  );
}
function BBRSIPanel({ candles }) {
  return (
    <BBPanel code="RSI" title="Relative Strength · 14" padded={false}
      right={<span style={{ color: "var(--bb-amber)", fontFamily: "var(--ns-font-mono)", fontSize: 11 }}>62.4 · NORMAL</span>}>
      <RSIChart candles={candles} width={1568} height={120} theme="dark" />
    </BBPanel>
  );
}
function BBMACDPanel({ candles }) {
  return (
    <BBPanel code="MACD" title="MACD · 12 / 26 / 9" padded={false}
      right={<span style={{ color: "var(--bb-amber)", fontFamily: "var(--ns-font-mono)", fontSize: 11 }}>DIF +1.84 · DEA +1.41 · HIST +0.43</span>}>
      <MACDChart candles={candles} width={1568} height={120} theme="dark" />
    </BBPanel>
  );
}

// ─── Col 3: Watchlist ────────────────────────────────
function BBWatchPanel({ items }) {
  return (
    <BBPanel code="WL" title="Watchlist · 10 Symbols" padded={false}
      right={<span className="bb-mute" style={{ fontFamily: "var(--ns-font-mono)", fontSize: 10 }}>SORT: %CHG ▾</span>}>
      <table className="bb-table">
        <thead>
          <tr>
            <th>SYM</th>
            <th style={{ textAlign: "right" }}>LAST</th>
            <th style={{ textAlign: "right" }}>CHG</th>
            <th style={{ textAlign: "right" }}>%CHG</th>
            <th style={{ textAlign: "right" }}>VOL</th>
            <th>30D</th>
          </tr>
        </thead>
        <tbody>
          {items.map((it, i) => {
            const up = it.chg >= 0;
            return (
              <tr key={it.ticker} style={i === 0 ? { background: "rgba(255,180,74,0.06)" } : null}>
                <td><span className="bb-amb mono" style={{ fontWeight: 700 }}>{it.ticker}</span></td>
                <td style={{ textAlign: "right" }} className="bb-dim mono">{it.price.toFixed(2)}</td>
                <td style={{ textAlign: "right" }} className={(up ? "bb-up" : "bb-down") + " mono"}>{up ? "+" : ""}{it.chg.toFixed(2)}</td>
                <td style={{ textAlign: "right" }} className={(up ? "bb-up" : "bb-down") + " mono"}>{up ? "+" : ""}{it.pct.toFixed(2)}%</td>
                <td style={{ textAlign: "right" }} className="bb-mute mono">{it.vol}</td>
                <td style={{ width: 70 }}>
                  <Sparkline values={it.sparkValues} width={62} height={18}
                    color={up ? "#2ecc71" : "#ff5252"}
                    fill={up ? "rgba(46,204,113,0.14)" : "rgba(255,82,82,0.14)"} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </BBPanel>
  );
}

// ─── Col 3: Recent trades ────────────────────────────
function BBTradesPanel() {
  const trades = [
    { date: "04-22", action: "B", qty: 10, price: 168.40, pl: null },
    { date: "03-14", action: "B", qty: 20, price: 175.90, pl: null },
    { date: "12-08", action: "S", qty: 15, price: 251.20, pl: +1182.50 },
    { date: "09-30", action: "B", qty: 20, price: 218.55, pl: null },
    { date: "06-14", action: "B", qty: 15, price: 173.10, pl: null },
    { date: "03-04", action: "B", qty: 15, price: 184.20, pl: null },
    { date: "11-17", action: "S", qty: 10, price: 196.40, pl: +480.50 },
  ];
  return (
    <BBPanel code="TRDE" title="Trade Blotter · AAPL" padded={false}>
      <table className="bb-table">
        <thead>
          <tr>
            <th>DATE</th>
            <th>T</th>
            <th style={{ textAlign: "right" }}>QTY</th>
            <th style={{ textAlign: "right" }}>PRICE</th>
            <th style={{ textAlign: "right" }}>VAL</th>
            <th style={{ textAlign: "right" }}>R-P&L</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t, i) => (
            <tr key={i}>
              <td className="bb-mute mono">{t.date}</td>
              <td>
                <span className={"mono " + (t.action === "B" ? "bb-up" : "bb-down")}
                  style={{ fontWeight: 700, padding: "0 4px", border: `1px solid ${t.action === "B" ? "var(--bb-up)" : "var(--bb-down)"}` }}>{t.action}</span>
              </td>
              <td style={{ textAlign: "right" }} className="bb-dim mono">{t.qty}</td>
              <td style={{ textAlign: "right" }} className="bb-dim mono">{t.price.toFixed(2)}</td>
              <td style={{ textAlign: "right" }} className="bb-mute mono">{(t.qty * t.price).toFixed(0)}</td>
              <td style={{ textAlign: "right" }} className={(t.pl == null ? "bb-mute" : t.pl >= 0 ? "bb-up" : "bb-down") + " mono"}>
                {t.pl == null ? "—" : (t.pl >= 0 ? "+" : "") + t.pl.toFixed(0)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </BBPanel>
  );
}

// ─── Col 4: Order entry ──────────────────────────────
function BBOrderPanel({ meta }) {
  return (
    <BBPanel code="OMS·NEW" title="New Order · Ticket"
      right={<span className="bb-amb mono" style={{ fontSize: 10 }}>[F4] SEND</span>}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 4, marginBottom: 8 }}>
        <span style={{ ...btnAmber, padding: "5px 8px", fontSize: 11 }}>BUY</span>
        <span style={btnGhostBSm}>SELL</span>
        <span style={btnGhostBSm}>SPLIT</span>
        <span style={btnGhostBSm}>DIV</span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, fontFamily: "var(--ns-font-mono)", fontSize: 11.5 }}>
        <BBFormRow label="SYM" value={meta.ticker} />
        <BBFormRow label="DATE" value="2026-05-12" />
        <BBFormRow label="QTY" value="10" />
        <BBFormRow label="PRICE" value={meta.price.toFixed(2)} />
        <BBFormRow label="FEE" value="0.00" />
        <BBFormRow label="TIF" value="DAY" />
      </div>
      <div style={{ marginTop: 8, padding: 8, background: "var(--bb-bg-3)", border: "1px solid var(--bb-line)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span className="bb-mute mono" style={{ fontSize: 10.5 }}>EST TOTAL · INCL FEE</span>
        <span className="bb-amb mono" style={{ fontSize: 16, fontWeight: 700 }}>${(meta.price * 10).toFixed(2)}</span>
      </div>
      <div style={{ marginTop: 8, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
        <button style={btnAmber}>[F4] SEND</button>
        <button style={btnGhostB}>CLR</button>
      </div>
    </BBPanel>
  );
}
const btnGhostBSm = { background: "transparent", color: "var(--bb-ink-dim)", border: "1px solid var(--bb-line)", padding: "5px 8px", fontFamily: "var(--ns-font-headline)", fontWeight: 600, fontSize: 11, letterSpacing: "0.08em", cursor: "pointer", textAlign: "center" };
function BBFormRow({ label, value }) {
  return (
    <div>
      <div style={{ color: "var(--bb-ink-mute)", fontSize: 9.5, letterSpacing: "0.12em" }}>{label}</div>
      <input defaultValue={value} style={{ background: "var(--bb-bg)", border: "1px solid var(--bb-line)", color: "var(--bb-amber)", fontFamily: "var(--ns-font-mono)", fontSize: 12, fontWeight: 600, padding: "4px 6px", width: "100%", outline: "none" }} />
    </div>
  );
}

// ─── Col 4: News ─────────────────────────────────────
function BBNewsPanel({ meta }) {
  const items = [
    { src: "RTRS", ago: "00:42", title: "Apple scraps Vision Pro 2 production plans — pivots to lighter glasses platform", tag: "PROD" },
    { src: "BN",   ago: "01:18", title: `${meta.ticker} signs 5-yr custom AI silicon deal with AVGO · first H2 2026`, tag: "AI", hot: true },
    { src: "WSJ",  ago: "03:55", title: "iPhone 16 China unit sales +12% YoY in Q1 · iOS 18.4 Mandarin features land", tag: "CHN" },
    { src: "FT",   ago: "06:01", title: "EU DMA penalty draft leaked — fine could reach EUR 1.8bn", tag: "REG" },
    { src: "CNBC", ago: "09:48", title: 'Wedbush ups PT to $275 — "billion device upgrade super-cycle restart"', tag: "RATING" },
    { src: "BARR", ago: "14:30", title: "Buyback authorization on track for $110bn refresh at May board mtg", tag: "CAP" },
  ];
  return (
    <BBPanel code="NEWS" title="Top News · AAPL · Last 24H" padded={false}
      right={<span className="bb-mute mono" style={{ fontSize: 10 }}>{items.length} ITEMS</span>}>
      <table className="bb-table">
        <tbody>
          {items.map((n, i) => (
            <tr key={i}>
              <td className="bb-mute mono" style={{ width: 56 }}>{n.ago}</td>
              <td><span className="bb-amb mono" style={{ fontSize: 10, fontWeight: 700 }}>{n.src}</span> {n.hot && <span className="bb-down mono" style={{ fontSize: 9 }}>HOT</span>}<br/>
                <span className="bb-dim" style={{ fontSize: 11.5, lineHeight: 1.45, display: "block", marginTop: 2 }}>{n.title}</span>
                <span className="bb-mute mono" style={{ fontSize: 10 }}>· {n.tag}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </BBPanel>
  );
}

// ─── Col 4: AI Analysis ──────────────────────────────
function BBAIPanel({ meta }) {
  return (
    <BBPanel code="AI" title="Research Note · AI" padded={false}
      right={<span className="bb-amb mono" style={{ fontSize: 10 }}>v2-ZH · SONNET</span>}>
      <div style={{ padding: 12, fontSize: 11.5, lineHeight: 1.55, color: "var(--bb-ink-dim)" }}>
        <div style={{ color: "var(--bb-amber)", fontFamily: "var(--ns-font-headline)", fontWeight: 700, fontSize: 10, letterSpacing: "0.14em", marginBottom: 4 }}>基本面</div>
        <p style={{ margin: 0 }}>{meta.ticker} $221.34,市值 $3.41T,P/E 36.4。最近季报营收 <span className="bb-ink">$94.93B</span> 同比 +6.1%,净利润 <span className="bb-up">+7.7%</span>。服务业务占比上升至 27%,大中华区营收 <span className="bb-down">-2.4%</span>。</p>
        <div style={{ color: "var(--bb-amber)", fontFamily: "var(--ns-font-headline)", fontWeight: 700, fontSize: 10, letterSpacing: "0.14em", marginTop: 10, marginBottom: 4 }}>技术面</div>
        <p style={{ margin: 0 }}>日 K 上行通道。SMA50 <span className="bb-ink">$211.40</span> 支撑,RSI(14) <span className="bb-amb">62.4</span> 偏强未超买;MACD 金叉延续柱 +6 日。BOLL 上轨 $228.10 距现价 3.1%。近 5 日量较 20 日均 <span className="bb-up">+8%</span>。</p>
        <div style={{ color: "var(--bb-amber)", fontFamily: "var(--ns-font-headline)", fontWeight: 700, fontSize: 10, letterSpacing: "0.14em", marginTop: 10, marginBottom: 4 }}>风险</div>
        <ul style={{ margin: 0, paddingLeft: 14 }}>
          <li>Apple Intelligence 商业化节奏低于预期可能压制估值。</li>
          <li>大中华区营收连续三季下滑。</li>
          <li>P/E 处 5 年区间上沿,对盈利失速敏感。</li>
        </ul>
      </div>
      <div style={{ borderTop: "1px solid var(--bb-line)", padding: "6px 12px", display: "flex", justifyContent: "space-between", fontFamily: "var(--ns-font-mono)", fontSize: 10 }}>
        <span className="bb-mute">GEN 10:41 · CACHE 24H</span>
        <span className="bb-amb">[R] REGEN [C] COPY</span>
      </div>
    </BBPanel>
  );
}

// ─── Footer status bar ───────────────────────────────
function BBFooter() {
  return (
    <footer style={{ height: 28, background: "var(--bb-bg-2)", borderTop: "1px solid var(--bb-line)", display: "flex", alignItems: "center", padding: "0 16px", gap: 24, fontFamily: "var(--ns-font-mono)", fontSize: 10.5, color: "var(--bb-ink-mute)" }}>
      <span><span className="bb-up">●</span> CONNECTED · yfinance + tencent · last quote 421ms</span>
      <span>USER · YH · DESK A</span>
      <span>LAT · 23ms</span>
      <span style={{ marginLeft: "auto" }} className="bb-amb">[F1] HELP · [F4] SEND · [F8] PRT · [GO] EXEC</span>
    </footer>
  );
}

window.VariantB = VariantB;
