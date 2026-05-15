/* global React */
// Variant A — TradingView style stock detail
// Three-pane layout: left watchlist · center chart stack · right context panes

const { useState, useMemo } = React;

function VariantA() {
  const [period, setPeriod] = useState("6M");
  const [bbOn, setBbOn] = useState(true);
  const [smaOn, setSmaOn] = useState(true);
  const [selected, setSelected] = useState("AAPL");

  const watchlist = useMemo(() => MPShell.buildWatchlist(), []);
  const meta = watchlist.find(w => w.ticker === selected) || watchlist[0];

  const periodCounts = { "1D": 78, "5D": 30, "60D": 60, "6M": 130, "YTD": 95, "1Y": 250, "5Y": 260, "All": 300 };
  const count = periodCounts[period] || 130;

  const candles = useMemo(() => {
    const c = MPData.generateCandles({ seed: meta.seed + count, count, basePrice: meta.price * 0.78, vol: 0.018, drift: 0.0018 });
    // Force last candle to meta.price for realism
    const last = c[c.length - 1];
    const delta = meta.price - last.close;
    last.close = meta.price;
    last.open = meta.price - meta.chg;
    last.high = Math.max(last.high, meta.price) + Math.abs(delta);
    last.low = Math.min(last.low, last.open, meta.price);
    return c;
  }, [meta, count]);

  // Chart sizing — these match the column widths below.
  const CHART_W = 1620;
  return (
    <div style={{ width: 2560, minHeight: 1640, background: "var(--ns-background)", display: "flex", flexDirection: "column", overflowX: "hidden" }}>
      <Chrome activeKey="stock" />
      <SymbolStrip meta={meta} />
      <ChartToolbar period={period} setPeriod={setPeriod} bbOn={bbOn} setBbOn={setBbOn} smaOn={smaOn} setSmaOn={setSmaOn} />

      <div style={{ display: "grid", gridTemplateColumns: "280px minmax(0, 1fr) 440px", gap: 16, padding: "16px 24px 32px", flex: 1 }}>
        {/* LEFT RAIL — watchlist */}
        <LeftWatchlist items={watchlist} selected={selected} onSelect={setSelected} />

        {/* CENTER — chart stack */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
          <div className="mp-card" style={{ padding: "12px 16px 0" }}>
            <OHLCBar meta={meta} candles={candles} />
            <CandleChart candles={candles} width={CHART_W} height={520} showVolume={true} showSMA={smaOn} showBB={bbOn} theme="light" />
          </div>
          <div className="mp-card" style={{ padding: "8px 16px" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
              <span className="mp-eyebrow" style={{ letterSpacing: "0.16em" }}>RSI · 14</span>
              <span className="mono" style={{ fontSize: 12, color: "var(--ns-on-surface-variant)" }}>
                值 <span style={{ color: "var(--mp-up)", fontWeight: 700 }}>62.4</span> · 区间 30 / 70
              </span>
            </div>
            <RSIChart candles={candles} width={CHART_W} height={130} theme="light" />
          </div>
          <div className="mp-card" style={{ padding: "8px 16px" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
              <span className="mp-eyebrow" style={{ letterSpacing: "0.16em" }}>MACD · 12 / 26 / 9</span>
              <span className="mono" style={{ fontSize: 12, color: "var(--ns-on-surface-variant)" }}>
                DIF <span style={{ color: "var(--ns-primary)", fontWeight: 700 }}>+1.84</span>
                <span style={{ margin: "0 6px", color: "var(--ns-outline)"}}>·</span>
                DEA <span style={{ color: "var(--ns-warning)", fontWeight: 700 }}>+1.41</span>
                <span style={{ margin: "0 6px", color: "var(--ns-outline)"}}>·</span>
                HIST <span style={{ color: "var(--mp-up)", fontWeight: 700 }}>+0.43</span>
              </span>
            </div>
            <MACDChart candles={candles} width={CHART_W} height={130} theme="light" />
          </div>
          <RecentTradesBlock ticker={meta.ticker} />
        </div>

        {/* RIGHT RAIL — position, record trade, AI analysis, news */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
          <PositionCard meta={meta} />
          <RecordTradeCard meta={meta} />
          <AIAnalysisCard ticker={meta.ticker} />
          <NewsList ticker={meta.ticker} />
        </div>
      </div>
    </div>
  );
}

// ─── Chrome / top nav ────────────────────────────────
function Chrome({ activeKey }) {
  return (
    <header className="mp-chrome">
      <div className="mp-chrome__brand">
        <img src="assets/logo.svg" alt="" width="32" height="32" style={{ display: "block" }} />
        <span className="mp-chrome__wordmark">MarketPulse</span>
      </div>
      <div className="mp-chrome__divider" />
      <nav className="mp-chrome__nav">
        {MPShell.NAV_ITEMS.map(item => (
          <a key={item.key} href={item.href} className={activeKey === item.key ? "is-active" : ""}>
            {item.label}
          </a>
        ))}
      </nav>
      <div className="mp-chrome__search">
        <span className="material-symbols-outlined">search</span>
        <span>搜索代码、公司或新闻…</span>
        <kbd>⌘K</kbd>
      </div>
      <div className="mp-chrome__actions">
        <button className="mp-chrome__iconbtn" title="市场状态">
          <span className="material-symbols-outlined">monitoring</span>
        </button>
        <button className="mp-chrome__iconbtn" title="告警">
          <span className="material-symbols-outlined">notifications</span>
        </button>
        <div className="mp-chrome__profile">
          <span className="mp-chrome__avatar">YH</span>
          <span style={{ fontSize: 12, color: "var(--ns-on-surface-variant)" }}>余怀</span>
        </div>
      </div>
    </header>
  );
}

// ─── Symbol strip ────────────────────────────────────
function SymbolStrip({ meta }) {
  const up = meta.chg >= 0;
  const arrow = up ? "trending_up" : "trending_down";
  const cls = up ? "up" : "down";
  return (
    <section style={{ display: "flex", alignItems: "center", padding: "20px 24px 16px", gap: 32, borderBottom: "1px solid var(--ns-outline-variant)" }}>
      {/* Symbol + name */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 16, minWidth: 320 }}>
        <div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
            <span className="grotesk" style={{ fontSize: 32, fontWeight: 700, letterSpacing: "-0.03em", color: "var(--ns-navy)" }}>{meta.ticker}</span>
            <span className="mp-chip mp-chip--periwinkle" style={{ height: 22 }}>NASDAQ</span>
            <span className="mp-chip" style={{ height: 22 }}>USD</span>
          </div>
          <div style={{ fontSize: 13, color: "var(--ns-on-surface-variant)", marginTop: 4 }}>
            {meta.name} · 实时 · <span style={{ color: "var(--mp-up)", display: "inline-flex", alignItems: "center", gap: 4 }}>
              <span className="mp-pulse" /> 美股盘中
            </span>
          </div>
        </div>
      </div>

      {/* Price */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 16, paddingRight: 24, borderRight: "1px solid var(--ns-outline-variant)" }}>
        <span className="mono tnum" style={{ fontSize: 44, fontWeight: 600, letterSpacing: "-0.02em", lineHeight: 1, color: "var(--ns-navy)" }}>
          {meta.price.toFixed(2)}
        </span>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span className={`grotesk tnum ${cls}`} style={{ fontSize: 18, fontWeight: 700, lineHeight: 1, display: "inline-flex", alignItems: "center", gap: 4 }}>
            <span className="material-symbols-outlined" style={{ fontSize: 18 }}>{arrow}</span>
            {meta.chg >= 0 ? "+" : ""}{meta.chg.toFixed(2)} ({meta.chg >= 0 ? "+" : ""}{meta.pct.toFixed(2)}%)
          </span>
          <span style={{ fontSize: 11, color: "var(--ns-on-surface-variant)" }}>
            盘后 <span className="mono tnum" style={{ color: "var(--ns-on-surface)" }}>221.50</span>
            <span className={cls} style={{ marginLeft: 4 }}>+0.16 (+0.07%)</span>
          </span>
        </div>
      </div>

      {/* OHLC quick stats */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, auto)", gap: "4px 28px", flex: 1, fontSize: 12 }}>
        <StatPair label="开盘" value="219.80" />
        <StatPair label="最高" value="222.10" />
        <StatPair label="成交量" value={meta.vol} />
        <StatPair label="市值" value={meta.mc} />
        <StatPair label="昨收" value={(meta.price - meta.chg).toFixed(2)} />
        <StatPair label="最低" value="218.95" />
        <StatPair label="平均量" value="58.7M" />
        <StatPair label="市盈率" value="36.4" />
      </div>

      {/* Right: actions */}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <button className="mp-btn mp-btn--ghost">
          <span className="material-symbols-outlined">star_border</span>
          加自选
        </button>
        <button className="mp-btn mp-btn--ghost">
          <span className="material-symbols-outlined">edit_note</span>
          记一笔
        </button>
        <button className="mp-btn mp-btn--navy">
          <span className="material-symbols-outlined">auto_awesome</span>
          AI 分析
        </button>
      </div>
    </section>
  );
}
function StatPair({ label, value }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span style={{ fontSize: 10, letterSpacing: "0.16em", color: "var(--ns-slate-500)", fontFamily: "var(--ns-font-headline)", textTransform: "uppercase", fontWeight: 700 }}>{label}</span>
      <span className="mono tnum" style={{ fontSize: 13, color: "var(--ns-navy)", fontWeight: 600 }}>{value}</span>
    </div>
  );
}

// ─── Toolbar above chart ─────────────────────────────
function ChartToolbar({ period, setPeriod, bbOn, setBbOn, smaOn, setSmaOn }) {
  const periods = ["1D", "5D", "60D", "6M", "YTD", "1Y", "5Y", "All"];
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 16, padding: "10px 24px", borderBottom: "1px solid var(--ns-outline-variant)", background: "white" }}>
      <span className="mp-eyebrow mp-eyebrow--primary">区间</span>
      <div className="mp-seg">
        {periods.map(p => (
          <button key={p} className={period === p ? "is-active" : ""} onClick={() => setPeriod(p)}>{p}</button>
        ))}
      </div>
      <span className="mp-eyebrow mp-eyebrow--primary" style={{ marginLeft: 16 }}>指标</span>
      <div style={{ display: "flex", gap: 6 }}>
        <button className={"mp-chip" + (bbOn ? " mp-chip--active" : "")} onClick={() => setBbOn(!bbOn)}>
          <span className="material-symbols-outlined" style={{ fontSize: 13 }}>{bbOn ? "check_box" : "check_box_outline_blank"}</span>
          BOLL · 20,2
        </button>
        <button className={"mp-chip" + (smaOn ? " mp-chip--active" : "")} onClick={() => setSmaOn(!smaOn)}>
          <span className="material-symbols-outlined" style={{ fontSize: 13 }}>{smaOn ? "check_box" : "check_box_outline_blank"}</span>
          SMA · 50 / 200
        </button>
        <button className="mp-chip">+ MA · EMA · VOL · KDJ</button>
      </div>
      <span style={{ flex: 1 }} />
      <div style={{ display: "flex", gap: 4 }}>
        <button className="mp-btn mp-btn--ghost mp-btn--sm" title="蜡烛">
          <span className="material-symbols-outlined">candlestick_chart</span>
          蜡烛
        </button>
        <button className="mp-btn mp-btn--ghost mp-btn--sm" title="折线">
          <span className="material-symbols-outlined">show_chart</span>
        </button>
        <button className="mp-btn mp-btn--ghost mp-btn--sm" title="面积">
          <span className="material-symbols-outlined">area_chart</span>
        </button>
      </div>
      <div style={{ display: "flex", gap: 4 }}>
        <button className="mp-btn mp-btn--ghost mp-btn--sm">
          <span className="material-symbols-outlined">compare_arrows</span>
          对比
        </button>
        <button className="mp-btn mp-btn--ghost mp-btn--sm">
          <span className="material-symbols-outlined">fullscreen</span>
        </button>
        <button className="mp-btn mp-btn--ghost mp-btn--sm">
          <span className="material-symbols-outlined">download</span>
          导出
        </button>
      </div>
    </div>
  );
}

// ─── Inline OHLC bar above chart ─────────────────────
function OHLCBar({ meta, candles }) {
  const last = candles[candles.length - 1];
  const prev = candles[candles.length - 2] || last;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 18, padding: "10px 4px 12px", fontSize: 12, fontFamily: "var(--ns-font-mono)" }}>
      <span style={{ fontFamily: "var(--ns-font-headline)", fontWeight: 700, color: "var(--ns-navy)", letterSpacing: "-0.02em" }}>
        {meta.ticker} · 日 K
      </span>
      <Field label="O" value={last.open.toFixed(2)} />
      <Field label="H" value={last.high.toFixed(2)} color="var(--mp-up)" />
      <Field label="L" value={last.low.toFixed(2)} color="var(--mp-down)" />
      <Field label="C" value={last.close.toFixed(2)} bold />
      <Field label="V" value={meta.vol} />
      <span style={{ marginLeft: "auto", display: "flex", gap: 16, alignItems: "center" }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "var(--ns-on-surface-variant)" }}>
          <span style={{ width: 10, height: 2, background: "var(--ns-primary)", display: "inline-block" }} /> SMA 50
        </span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "var(--ns-on-surface-variant)" }}>
          <span style={{ width: 10, height: 2, background: "var(--ns-navy)", display: "inline-block" }} /> SMA 200
        </span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "var(--ns-on-surface-variant)" }}>
          <span style={{ width: 10, height: 2, background: "rgba(0,102,204,0.5)", display: "inline-block" }} /> BOLL · 20
        </span>
      </span>
    </div>
  );
}
function Field({ label, value, color, bold }) {
  return (
    <span style={{ display: "inline-flex", gap: 6, alignItems: "baseline" }}>
      <span style={{ color: "var(--ns-slate-400)", fontWeight: 600 }}>{label}</span>
      <span style={{ color: color || "var(--ns-navy)", fontWeight: bold ? 700 : 500 }}>{value}</span>
    </span>
  );
}

// ─── Left rail — watchlist ───────────────────────────
function LeftWatchlist({ items, selected, onSelect }) {
  return (
    <aside className="mp-card" style={{ height: "fit-content" }}>
      <div className="mp-card__head" style={{ padding: "12px 14px" }}>
        <span className="mp-card__title">自选股</span>
        <button className="mp-btn mp-btn--ghost mp-btn--sm" style={{ height: 24 }}>
          <span className="material-symbols-outlined">add</span>
        </button>
      </div>
      <div style={{ display: "flex", borderBottom: "1px solid var(--ns-outline-variant)", padding: "0 4px", fontSize: 11 }}>
        <Tab label="自选 (10)" active />
        <Tab label="美股" />
        <Tab label="A股" />
        <Tab label="ETF" />
      </div>
      <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {items.map(it => {
          const up = it.chg >= 0;
          const isActive = it.ticker === selected;
          return (
            <li key={it.ticker}
                onClick={() => onSelect(it.ticker)}
                style={{
                  padding: "10px 14px",
                  borderBottom: "1px solid var(--ns-outline-variant)",
                  cursor: "pointer",
                  background: isActive ? "var(--ns-surface-container)" : "transparent",
                  borderLeft: isActive ? "3px solid var(--ns-primary)" : "3px solid transparent",
                }}>
              <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 8 }}>
                <div style={{ minWidth: 0 }}>
                  <div className="grotesk" style={{ fontWeight: 700, fontSize: 13, color: "var(--ns-navy)", letterSpacing: "-0.01em" }}>{it.ticker}</div>
                  <div style={{ fontSize: 10.5, color: "var(--ns-on-surface-variant)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{it.name}</div>
                </div>
                <Sparkline values={it.sparkValues} width={56} height={20}
                  color={up ? "#0e8a5f" : "#c0392b"}
                  fill={up ? "rgba(14,138,95,0.12)" : "rgba(192,57,43,0.12)"} />
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginTop: 4 }}>
                <span className="mono tnum" style={{ fontSize: 13, fontWeight: 600, color: "var(--ns-navy)" }}>{it.price.toFixed(2)}</span>
                <span className={`mono tnum ${up ? "up" : "down"}`} style={{ fontSize: 11.5, fontWeight: 600 }}>
                  {up ? "+" : ""}{it.pct.toFixed(2)}%
                </span>
              </div>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
function Tab({ label, active }) {
  return (
    <div style={{
      padding: "8px 12px",
      fontFamily: "var(--ns-font-headline)",
      fontWeight: 600,
      fontSize: 11,
      letterSpacing: "0.04em",
      color: active ? "var(--ns-navy)" : "var(--ns-on-surface-variant)",
      borderBottom: active ? "2px solid var(--ns-primary)" : "2px solid transparent",
      cursor: "pointer",
    }}>{label}</div>
  );
}

// ─── Right rail — Position card ──────────────────────
function PositionCard({ meta }) {
  const qty = 50;
  const avg = 172.40;
  const cost = qty * avg;
  const mv = qty * meta.price;
  const pl = mv - cost;
  const plPct = (pl / cost) * 100;
  const todayPl = qty * meta.chg;
  const up = pl >= 0;
  return (
    <section className="mp-card">
      <div className="mp-card__head">
        <span className="mp-card__title"><span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-primary)" }}>account_balance_wallet</span>持仓</span>
        <span style={{ fontSize: 11, color: "var(--ns-on-surface-variant)" }}>更新于 09:43:21 PT</span>
      </div>
      <div style={{ padding: 18, display: "grid", gridTemplateColumns: "1fr 1fr", gap: "14px 24px" }}>
        <Stat label="数量" value={`${qty} 股`} large />
        <Stat label="均价" value={`$${avg.toFixed(2)}`} large />
        <Stat label="总成本" value={`$${cost.toFixed(2)}`} />
        <Stat label="市值" value={`$${mv.toFixed(2)}`} bold />
        <div style={{ gridColumn: "span 2", display: "flex", flexDirection: "column", gap: 4, padding: "12px 14px", background: up ? "rgba(14,138,95,0.06)" : "rgba(192,57,43,0.06)", borderLeft: `3px solid ${up ? "var(--mp-up)" : "var(--mp-down)"}`, borderRadius: 2 }}>
          <span className="mp-eyebrow">未实现盈亏</span>
          <div className="grotesk tnum" style={{ fontSize: 28, fontWeight: 700, color: up ? "var(--mp-up)" : "var(--mp-down)", letterSpacing: "-0.02em" }}>
            {up ? "+" : ""}${Math.abs(pl).toFixed(2)}
          </div>
          <div className={`mono tnum ${up ? "up" : "down"}`} style={{ fontSize: 13, fontWeight: 600 }}>
            {up ? "+" : ""}{plPct.toFixed(2)}% · 今日 {todayPl >= 0 ? "+" : ""}${todayPl.toFixed(2)}
          </div>
        </div>
      </div>
      <div style={{ display: "flex", borderTop: "1px solid var(--ns-outline-variant)" }}>
        <Footstat label="持仓天数" value="312" />
        <Footstat label="占组合" value="18.4%" />
        <Footstat label="累计分红" value="$48.60" />
      </div>
    </section>
  );
}
function Stat({ label, value, bold, large }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
      <span className="mp-eyebrow" style={{ letterSpacing: "0.16em" }}>{label}</span>
      <span className="mono tnum" style={{ fontSize: large ? 20 : 16, fontWeight: bold ? 700 : 600, color: "var(--ns-navy)" }}>{value}</span>
    </div>
  );
}
function Footstat({ label, value }) {
  return (
    <div style={{ flex: 1, padding: "10px 14px", borderRight: "1px solid var(--ns-outline-variant)", textAlign: "center" }}>
      <div className="mp-eyebrow" style={{ fontSize: 9 }}>{label}</div>
      <div className="mono tnum" style={{ fontSize: 13, fontWeight: 600, color: "var(--ns-navy)", marginTop: 3 }}>{value}</div>
    </div>
  );
}

// ─── Right rail — Record Trade ───────────────────────
function RecordTradeCard({ meta }) {
  return (
    <section className="mp-card">
      <div className="mp-card__head">
        <span className="mp-card__title"><span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-primary)" }}>edit_note</span>记一笔</span>
        <span style={{ fontSize: 11, color: "var(--ns-on-surface-variant)" }}>{meta.ticker}</span>
      </div>
      <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
        <div className="mp-seg" style={{ width: "100%", display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr" }}>
          <button className="is-active">买入</button>
          <button>卖出</button>
          <button>拆股</button>
          <button>分红</button>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          <Field2 label="数量" value="10" />
          <Field2 label="价格 USD" value={meta.price.toFixed(2)} />
          <Field2 label="日期" value="2026-05-12" />
          <Field2 label="手续费" value="0.00" />
        </div>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="mp-eyebrow">备注 (可选)</span>
          <textarea rows={2} placeholder="例如: 季报后回调买入,目标 12 个月"
            style={{ border: "1px solid var(--ns-outline-variant)", borderRadius: 2, padding: "8px 10px", fontFamily: "var(--ns-font-body)", fontSize: 13, resize: "none" }}></textarea>
        </label>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 12px", background: "var(--ns-surface-container-low)", borderRadius: 2 }}>
          <span style={{ fontSize: 11, color: "var(--ns-on-surface-variant)" }}>合计 · 含费</span>
          <span className="mono tnum grotesk" style={{ fontSize: 15, fontWeight: 700, color: "var(--ns-navy)" }}>${(meta.price * 10).toFixed(2)}</span>
        </div>
        <button className="mp-btn mp-btn--primary" style={{ height: 38 }}>
          <span className="material-symbols-outlined">add</span>
          确认买入
        </button>
      </div>
    </section>
  );
}
function Field2({ label, value }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span className="mp-eyebrow">{label}</span>
      <input defaultValue={value}
        style={{ border: "1px solid var(--ns-outline-variant)", borderRadius: 2, padding: "8px 10px", fontFamily: "var(--ns-font-mono)", fontSize: 13, fontWeight: 600, color: "var(--ns-navy)" }} />
    </label>
  );
}

// ─── Right rail — AI Analysis ────────────────────────
function AIAnalysisCard({ ticker }) {
  return (
    <section className="mp-card">
      <div className="mp-card__head">
        <span className="mp-card__title"><span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-primary)" }}>auto_awesome</span>AI 研究报告</span>
        <span style={{ fontSize: 10.5, color: "var(--ns-on-surface-variant)", fontFamily: "var(--ns-font-mono)" }}>analysis-v2-zh · claude-sonnet</span>
      </div>
      <div className="ai-md" style={{ padding: "12px 18px 18px" }}>
        <h2>基本面</h2>
        <p><strong>{ticker}</strong> 当前股价 <em>$221.34</em>,市值 <em>$3.41T</em>,市盈率 <em>36.4</em>。最近一季营业收入 <em>$94.93B</em>(同比 +6.1%),净利润 <em>$23.43B</em>(同比 +7.7%)。服务业务占比持续上升至 <strong>27%</strong>,iPhone 销售连续三季同比转正,大中华区营收同比 <strong>-2.4%</strong> 仍有压力。</p>
        <h2>技术面</h2>
        <p>日 K 处于上行通道,<strong>SMA50</strong> 在 $211.40 形成强支撑,<strong>SMA200</strong> $198.90 远在下方。RSI(14) <em>62.4</em> 偏强但未超买;MACD 金叉延续,柱状值连续 6 日为正。布林上轨 <em>$228.10</em> 距现价约 3.1%。近 5 日成交量较 20 日均量放大 <strong>+8%</strong>。</p>
        <h2>风险</h2>
        <ul>
          <li>苹果智能 (Apple Intelligence) 商业化节奏慢于市场预期可能压制估值。</li>
          <li>大中华区营收已连续三季下滑,关税与本土竞争双重压力。</li>
          <li>P/E 36.4 处于 5 年区间上沿 (中位 25.8),对盈利失速敏感。</li>
        </ul>
      </div>
      <div style={{ borderTop: "1px solid var(--ns-outline-variant)", padding: "10px 18px", display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 11, color: "var(--ns-on-surface-variant)" }}>
        <span>生成于 09:41 · 缓存 24h</span>
        <span style={{ display: "flex", gap: 12 }}>
          <span style={{ display: "inline-flex", gap: 4, alignItems: "center", cursor: "pointer" }}><span className="material-symbols-outlined" style={{ fontSize: 14 }}>refresh</span>重新生成</span>
          <span style={{ display: "inline-flex", gap: 4, alignItems: "center", cursor: "pointer" }}><span className="material-symbols-outlined" style={{ fontSize: 14 }}>content_copy</span>复制</span>
        </span>
      </div>
    </section>
  );
}

// ─── Right rail — News ────────────────────────────────
function NewsList({ ticker }) {
  const items = [
    { src: "Reuters",   ago: "2h", title: `Apple 砍掉 Vision Pro 二代生产计划,转向更轻的智能眼镜`, tag: "产品" },
    { src: "Bloomberg", ago: "4h", title: `${ticker} 与博通签订 5 年定制 AI 芯片协议,首批 2026 年 H2 交付`, tag: "AI", hot: true },
    { src: "WSJ",       ago: "7h", title: `iPhone 16 中国销量 Q1 同比 +12%,iOS 18.4 中文版功能集落地`, tag: "中国" },
    { src: "FT",        ago: "1d", title: `欧盟数字市场法案罚单初稿外泄,潜在罚金最高 18 亿美元`, tag: "监管" },
    { src: "CNBC",      ago: "1d", title: `Wedbush 上调目标价至 $275 — "全球十亿设备升级周期重启"`, tag: "评级" },
  ];
  return (
    <section className="mp-card">
      <div className="mp-card__head">
        <span className="mp-card__title"><span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-primary)" }}>newspaper</span>最新新闻</span>
        <span style={{ fontSize: 11, color: "var(--ns-on-surface-variant)" }}>yfinance · 实时</span>
      </div>
      <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {items.map((n, i) => (
          <li key={i} style={{ padding: "12px 18px", borderBottom: i < items.length - 1 ? "1px solid var(--ns-outline-variant)" : "0", cursor: "pointer" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
              <span className="mp-eyebrow mp-eyebrow--primary">{n.src}</span>
              <span style={{ fontSize: 11, color: "var(--ns-slate-400)" }}>{n.ago} 前</span>
              {n.hot && <span className="mp-chip mp-chip--down" style={{ height: 18, fontSize: 9.5 }}>HOT</span>}
              <span className="mp-chip" style={{ height: 18, fontSize: 9.5 }}>{n.tag}</span>
            </div>
            <p style={{ margin: 0, fontSize: 13, lineHeight: 1.5, color: "var(--ns-navy)", fontWeight: 500 }}>{n.title}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}

// ─── Recent trades panel (under chart) ───────────────
function RecentTradesBlock({ ticker }) {
  const trades = [
    { date: "2026-04-22", action: "buy",  qty: 10, price: 168.40, pl: null },
    { date: "2026-03-14", action: "buy",  qty: 20, price: 175.90, pl: null },
    { date: "2025-12-08", action: "sell", qty: 15, price: 251.20, pl: +1182.50 },
    { date: "2025-09-30", action: "buy",  qty: 20, price: 218.55, pl: null },
    { date: "2025-06-14", action: "buy",  qty: 15, price: 173.10, pl: null },
  ];
  return (
    <section className="mp-card">
      <div className="mp-card__head">
        <span className="mp-card__title"><span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-primary)" }}>swap_horiz</span>最近交易 — {ticker}</span>
        <a href="#" style={{ fontSize: 12, color: "var(--ns-primary)", textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 4 }}>
          查看全部 5 笔
          <span className="material-symbols-outlined" style={{ fontSize: 16 }}>arrow_forward</span>
        </a>
      </div>
      <table className="mp-table">
        <thead>
          <tr>
            <th>时间</th>
            <th>类型</th>
            <th style={{ textAlign: "right" }}>数量</th>
            <th style={{ textAlign: "right" }}>价格</th>
            <th style={{ textAlign: "right" }}>总额</th>
            <th style={{ textAlign: "right" }}>已实现盈亏</th>
            <th>备注</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t, i) => {
            const up = t.action === "buy";
            return (
              <tr key={i}>
                <td className="muted mono">{t.date}</td>
                <td>
                  <span className={"mp-chip " + (up ? "mp-chip--periwinkle" : "mp-chip--down")} style={{ height: 22 }}>
                    {up ? "买入" : "卖出"}
                  </span>
                </td>
                <td className="num">{t.qty}</td>
                <td className="num">${t.price.toFixed(2)}</td>
                <td className="num">${(t.qty * t.price).toFixed(2)}</td>
                <td className={"num " + (t.pl == null ? "muted" : t.pl >= 0 ? "up" : "down")} style={{ fontWeight: 600 }}>
                  {t.pl == null ? "—" : (t.pl >= 0 ? "+" : "") + "$" + t.pl.toFixed(2)}
                </td>
                <td className="muted" style={{ fontSize: 12 }}>{i === 2 ? "止盈减仓" : i === 4 ? "建仓" : ""}</td>
                <td style={{ textAlign: "right" }}>
                  <span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-slate-400)", cursor: "pointer" }}>more_horiz</span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

Object.assign(window, { VariantA, Chrome });
