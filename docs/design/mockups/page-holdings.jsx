/* global React */
// /holdings — portfolio overview

const { useMemo: useMemoH } = React;

function PageHoldings() {
  const holdings = useMemoH(() => buildHoldings(), []);
  const totals = useMemoH(() => holdings.reduce((acc, h) => ({
    cost: acc.cost + h.qty * h.avg,
    mv: acc.mv + h.qty * h.price,
  }), { cost: 0, mv: 0 }), [holdings]);
  const pl = totals.mv - totals.cost;
  const plPct = pl / totals.cost * 100;

  return (
    <div style={{ width: 2560, minHeight: 1700, background: "var(--ns-background)", display: "flex", flexDirection: "column", overflowX: "hidden" }}>
      <Chrome activeKey="holdings" />

      {/* Hero */}
      <section style={{ padding: "32px 48px 24px", display: "grid", gridTemplateColumns: "1fr 360px", gap: 48, alignItems: "flex-start" }}>
        <div>
          <span className="mp-eyebrow mp-eyebrow--primary">投资组合</span>
          <h1 className="grotesk" style={{ fontSize: 48, fontWeight: 700, letterSpacing: "-0.04em", color: "var(--ns-navy)", margin: "6px 0 0", lineHeight: 1 }}>
            Holdings · Portfolio Overview
          </h1>
          <div className="mp-rule" style={{ marginTop: 12 }} />
          <div style={{ display: "flex", alignItems: "flex-end", gap: 48, marginTop: 28 }}>
            <div>
              <span className="mp-eyebrow">总市值 · USD</span>
              <div className="mono tnum" style={{ fontSize: 60, fontWeight: 600, color: "var(--ns-navy)", letterSpacing: "-0.04em", lineHeight: 1 }}>
                ${totals.mv.toLocaleString(undefined, { maximumFractionDigits: 0 })}
              </div>
            </div>
            <div>
              <span className="mp-eyebrow">未实现盈亏</span>
              <div className={"grotesk tnum " + (pl >= 0 ? "up" : "down")} style={{ fontSize: 32, fontWeight: 700, letterSpacing: "-0.02em", lineHeight: 1.05 }}>
                {pl >= 0 ? "+" : ""}${pl.toLocaleString(undefined, { maximumFractionDigits: 0 })}
              </div>
              <div className={"mono tnum " + (pl >= 0 ? "up" : "down")} style={{ fontSize: 14, fontWeight: 600, marginTop: 2 }}>
                {pl >= 0 ? "+" : ""}{plPct.toFixed(2)}%
              </div>
            </div>
            <div>
              <span className="mp-eyebrow">今日</span>
              <div className="grotesk tnum up" style={{ fontSize: 32, fontWeight: 700, letterSpacing: "-0.02em", lineHeight: 1.05 }}>+$482.18</div>
              <div className="mono tnum up" style={{ fontSize: 14, fontWeight: 600, marginTop: 2 }}>+0.81% · 9 涨 1 跌</div>
            </div>
          </div>
        </div>

        {/* Big donut */}
        <DonutCard holdings={holdings} totalMV={totals.mv} />
      </section>

      {/* KPI strip */}
      <section style={{ padding: "0 48px 16px", display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 16 }}>
        <KPICard label="总成本 · 含手续费" value={"$" + totals.cost.toLocaleString(undefined, { maximumFractionDigits: 0 })} hint="247 笔交易累计" icon="payments" />
        <KPICard label="市值" value={"$" + totals.mv.toLocaleString(undefined, { maximumFractionDigits: 0 })} hint="实时 · 09:42:18 PT" icon="account_balance_wallet" />
        <KPICard label="未实现盈亏" value={(pl >= 0 ? "+" : "") + "$" + pl.toLocaleString(undefined, { maximumFractionDigits: 0 })} hint={(plPct).toFixed(2) + "% · 持仓盈亏"} valueColor="var(--mp-up)" icon="trending_up" />
        <KPICard label="已实现盈亏 · 2025起" value="+$12,604" hint="247 笔 · 胜率 62.4%" valueColor="var(--mp-up)" icon="payments" />
        <KPICard label="累计分红" value="+$418.20" hint="含本月 12.50" valueColor="var(--mp-up)" icon="redeem" />
      </section>

      {/* 3-col row: Allocation / Sectors / Contributors */}
      <section style={{ padding: "16px 48px 16px", display: "grid", gridTemplateColumns: "1.4fr 1fr 1.4fr", gap: 16 }}>
        <AllocationCard holdings={holdings} totalMV={totals.mv} />
        <SectorCard />
        <ContributorsCard holdings={holdings} />
      </section>

      {/* Holdings table — full width */}
      <section style={{ padding: "0 48px 16px" }}>
        <div className="mp-card">
          <div className="mp-card__head">
            <span className="mp-card__title"><span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-primary)" }}>table_chart</span>持仓明细 · 10 个标的</span>
            <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
              <span style={{ fontSize: 11.5, color: "var(--ns-on-surface-variant)" }}>排序 <span style={{ color: "var(--ns-navy)", fontWeight: 600 }}>市值 ↓</span></span>
              <button className="mp-btn mp-btn--ghost mp-btn--sm"><span className="material-symbols-outlined">filter_list</span>筛选</button>
              <button className="mp-btn mp-btn--ghost mp-btn--sm"><span className="material-symbols-outlined">download</span>导出</button>
            </div>
          </div>
          <table className="mp-table">
            <thead>
              <tr>
                <th style={{ width: 120 }}>代码</th>
                <th>名称</th>
                <th>板块</th>
                <th style={{ textAlign: "right" }}>数量</th>
                <th style={{ textAlign: "right" }}>均价</th>
                <th style={{ textAlign: "right" }}>现价</th>
                <th style={{ textAlign: "right" }}>今日 %</th>
                <th style={{ textAlign: "right" }}>总成本</th>
                <th style={{ textAlign: "right" }}>市值</th>
                <th style={{ textAlign: "right" }}>未实现盈亏</th>
                <th style={{ textAlign: "right" }}>盈亏 %</th>
                <th style={{ width: 200 }}>30日走势</th>
                <th style={{ width: 200 }}>占组合</th>
                <th style={{ width: 80 }}></th>
              </tr>
            </thead>
            <tbody>
              {holdings.map((h, i) => <HoldingRow key={i} h={h} totalMV={totals.mv} />)}
            </tbody>
            <tfoot>
              <tr style={{ background: "var(--ns-surface-container-low)" }}>
                <td colSpan={7} style={{ padding: "14px 14px", fontFamily: "var(--ns-font-headline)", fontWeight: 700, fontSize: 12, letterSpacing: "0.04em", color: "var(--ns-navy)" }}>合计 · 10 个标的</td>
                <td className="num mono tnum" style={{ fontWeight: 700, color: "var(--ns-navy)" }}>${totals.cost.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
                <td className="num mono tnum" style={{ fontWeight: 700, color: "var(--ns-navy)" }}>${totals.mv.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
                <td className="num mono tnum up" style={{ fontWeight: 700 }}>+${pl.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
                <td className="num mono tnum up" style={{ fontWeight: 700 }}>+{plPct.toFixed(2)}%</td>
                <td colSpan={3}></td>
              </tr>
            </tfoot>
          </table>
        </div>
      </section>

      {/* Bottom: monthly + AI risk */}
      <section style={{ padding: "0 48px 32px", display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <HoldingsMonthlyCard />
        <AIRiskCard />
      </section>
    </div>
  );
}

function buildHoldings() {
  return [
    { ticker: "AAPL",  name: "Apple Inc.",       sector: "科技",        qty: 50, avg: 172.40, price: 221.34, todayPct: +1.10, color: "#0066cc", seed: 42 },
    { ticker: "NVDA",  name: "NVIDIA Corp.",     sector: "半导体",      qty: 80, avg: 122.40, price: 138.07, todayPct: -2.25, color: "#0e8a5f", seed: 17 },
    { ticker: "MSFT",  name: "Microsoft",        sector: "软件 / 云",   qty: 40, avg: 418.20, price: 442.95, todayPct: +1.33, color: "#022448", seed: 9 },
    { ticker: "TSLA",  name: "Tesla, Inc.",      sector: "汽车 / 出行", qty: 60, avg: 226.40, price: 264.18, todayPct: +3.83, color: "#9b59b6", seed: 71 },
    { ticker: "GOOGL", name: "Alphabet A",       sector: "互联网",      qty: 50, avg: 173.40, price: 197.84, todayPct: +0.31, color: "#16a085", seed: 23 },
    { ticker: "META",  name: "Meta Platforms",   sector: "互联网",      qty: 12, avg: 522.50, price: 588.13, todayPct: -0.72, color: "#4d94ff", seed: 88 },
    { ticker: "AMZN",  name: "Amazon.com",       sector: "电商 / 云",   qty: 28, avg: 212.40, price: 232.55, todayPct: +0.51, color: "#c0570c", seed: 11 },
    { ticker: "AVGO",  name: "Broadcom Inc.",    sector: "半导体",      qty: 36, avg: 162.50, price: 178.32, todayPct: +2.24, color: "#1e3a5f", seed: 56 },
    { ticker: "TSM",   name: "TSMC ADR",         sector: "半导体",      qty: 25, avg: 178.40, price: 196.45, todayPct: +1.12, color: "#475569", seed: 33 },
    { ticker: "SPY",   name: "S&P 500 ETF",      sector: "指数 ETF",    qty: 40, avg: 542.10, price: 597.18, todayPct: +0.24, color: "#94a3b8", seed: 5 },
  ];
}

function DonutCard({ holdings, totalMV }) {
  // Render an SVG donut with segments per holding.
  const size = 280;
  const r = 110;
  const stroke = 36;
  const cx = size / 2, cy = size / 2;
  const circ = 2 * Math.PI * r;
  let offset = 0;
  return (
    <div className="mp-card" style={{ padding: "20px 22px", display: "flex", alignItems: "center", gap: 20 }}>
      <svg width={size} height={size} style={{ flexShrink: 0 }}>
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--ns-surface-container)" strokeWidth={stroke} />
        {holdings.map((h, i) => {
          const v = (h.qty * h.price) / totalMV;
          const len = v * circ;
          const el = (
            <circle key={i} cx={cx} cy={cy} r={r} fill="none"
              stroke={h.color} strokeWidth={stroke}
              strokeDasharray={`${len} ${circ - len}`}
              strokeDashoffset={-offset}
              transform={`rotate(-90 ${cx} ${cy})`} />
          );
          offset += len;
          return el;
        })}
        <text x={cx} y={cy - 4} textAnchor="middle" fontFamily="var(--ns-font-headline)" fontWeight="700" fontSize="13" letterSpacing="0.16em" fill="var(--ns-on-surface-variant)">市值</text>
        <text x={cx} y={cy + 22} textAnchor="middle" fontFamily="var(--ns-font-mono)" fontWeight="600" fontSize="22" fill="var(--ns-navy)">${(totalMV/1000).toFixed(1)}k</text>
        <text x={cx} y={cy + 42} textAnchor="middle" fontFamily="var(--ns-font-body)" fontSize="11" fill="var(--ns-on-surface-variant)">10 个标的</text>
      </svg>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, flex: 1, minWidth: 0 }}>
        <span className="mp-eyebrow mp-eyebrow--primary">主要构成</span>
        {holdings.slice(0, 5).map(h => {
          const pct = (h.qty * h.price) / totalMV * 100;
          return (
            <div key={h.ticker} style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ width: 10, height: 10, background: h.color, flexShrink: 0 }} />
              <span className="grotesk" style={{ fontWeight: 700, fontSize: 12, color: "var(--ns-navy)", letterSpacing: "-0.01em", flex: 1 }}>{h.ticker}</span>
              <span className="mono tnum" style={{ fontSize: 12, color: "var(--ns-on-surface-variant)" }}>{pct.toFixed(1)}%</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function AllocationCard({ holdings, totalMV }) {
  const rows = holdings.map(h => ({ ticker: h.ticker, value: h.qty * h.price, color: h.color, pct: (h.qty * h.price) / totalMV * 100 }));
  return (
    <section className="mp-card">
      <div className="mp-card__head">
        <span className="mp-card__title"><span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-primary)" }}>donut_small</span>持仓分布 · 按代码</span>
      </div>
      <div style={{ padding: "20px 22px" }}>
        <AllocationBar rows={rows.map(r => ({ value: r.value, color: r.color }))} height={32} />
        <ul style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px 18px", listStyle: "none", margin: "16px 0 0", padding: 0 }}>
          {rows.map(r => (
            <li key={r.ticker} style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0" }}>
              <span style={{ width: 10, height: 10, background: r.color, flexShrink: 0, borderRadius: 1 }} />
              <span className="grotesk" style={{ fontWeight: 700, fontSize: 12, color: "var(--ns-navy)" }}>{r.ticker}</span>
              <span className="mono tnum muted" style={{ fontSize: 11.5, marginLeft: "auto" }}>{r.pct.toFixed(1)}% · ${(r.value/1000).toFixed(1)}k</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function SectorCard() {
  const sectors = [
    { name: "半导体",      pct: 22.4, color: "#0066cc" },
    { name: "互联网",      pct: 18.7, color: "#0e8a5f" },
    { name: "软件 / 云",   pct: 14.2, color: "#022448" },
    { name: "汽车 / 出行", pct: 11.8, color: "#9b59b6" },
    { name: "科技",        pct: 18.4, color: "#4d94ff" },
    { name: "电商 / 云",   pct: 6.3,  color: "#c0570c" },
    { name: "指数 ETF",    pct: 8.2,  color: "#94a3b8" },
  ];
  return (
    <section className="mp-card">
      <div className="mp-card__head">
        <span className="mp-card__title"><span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-primary)" }}>category</span>板块分布</span>
        <span style={{ fontSize: 11.5, color: "var(--ns-on-surface-variant)" }}>科技含量 <span className="up" style={{ fontWeight: 700 }}>83.9%</span></span>
      </div>
      <ul style={{ listStyle: "none", margin: 0, padding: "16px 22px 20px", display: "flex", flexDirection: "column", gap: 10 }}>
        {sectors.map(s => (
          <li key={s.name} style={{ display: "grid", gridTemplateColumns: "92px 1fr 50px", gap: 10, alignItems: "center" }}>
            <span style={{ fontSize: 13, color: "var(--ns-navy)", fontWeight: 500 }}>{s.name}</span>
            <div style={{ height: 8, background: "var(--ns-surface-container)", borderRadius: 2, overflow: "hidden" }}>
              <div style={{ height: "100%", width: s.pct * 3 + "%", background: s.color }} />
            </div>
            <span className="mono tnum" style={{ fontSize: 12, fontWeight: 600, color: "var(--ns-navy)", textAlign: "right" }}>{s.pct.toFixed(1)}%</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ContributorsCard({ holdings }) {
  const ranked = holdings.map(h => {
    const pl = h.qty * (h.price - h.avg);
    const plPct = (h.price - h.avg) / h.avg * 100;
    return { ...h, pl, plPct };
  }).sort((a, b) => b.pl - a.pl);
  const maxAbs = Math.max(...ranked.map(r => Math.abs(r.pl)));
  return (
    <section className="mp-card">
      <div className="mp-card__head">
        <span className="mp-card__title"><span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-primary)" }}>leaderboard</span>贡献度 · 未实现盈亏</span>
        <span style={{ fontSize: 11.5, color: "var(--ns-on-surface-variant)" }}>10 个标的 · 排序 ↓</span>
      </div>
      <ul style={{ listStyle: "none", margin: 0, padding: "12px 22px 16px", display: "flex", flexDirection: "column", gap: 8 }}>
        {ranked.map(r => {
          const up = r.pl >= 0;
          return (
            <li key={r.ticker} style={{ display: "grid", gridTemplateColumns: "80px 1fr 120px 80px", gap: 10, alignItems: "center" }}>
              <span className="grotesk" style={{ fontWeight: 700, fontSize: 13, color: "var(--ns-navy)" }}>{r.ticker}</span>
              <div style={{ height: 8, background: "var(--ns-surface-container)", borderRadius: 2, position: "relative", overflow: "hidden" }}>
                <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: Math.abs(r.pl) / maxAbs * 100 + "%", background: up ? "var(--mp-up)" : "var(--mp-down)" }} />
              </div>
              <span className={"mono tnum " + (up ? "up" : "down")} style={{ fontSize: 13, fontWeight: 700, textAlign: "right" }}>{up ? "+" : ""}${r.pl.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
              <span className={"mono tnum " + (up ? "up" : "down")} style={{ fontSize: 11.5, textAlign: "right" }}>{up ? "+" : ""}{r.plPct.toFixed(1)}%</span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function HoldingRow({ h, totalMV }) {
  const cost = h.qty * h.avg;
  const mv = h.qty * h.price;
  const pl = mv - cost;
  const plPct = (h.price - h.avg) / h.avg * 100;
  const allocPct = mv / totalMV * 100;
  const up = pl >= 0;
  const todayUp = h.todayPct >= 0;
  const sparkCandles = MPData.generateCandles({ seed: h.seed, count: 30, basePrice: h.price * 0.9, vol: 0.012 });
  sparkCandles[sparkCandles.length - 1].close = h.price;
  return (
    <tr>
      <td><a href="#" style={{ color: "var(--ns-navy)", fontWeight: 700, textDecoration: "none", fontFamily: "var(--ns-font-headline)", letterSpacing: "-0.01em" }}>{h.ticker}</a></td>
      <td style={{ fontSize: 13, color: "var(--ns-navy)" }}>{h.name}</td>
      <td><span className="mp-chip" style={{ height: 22, fontSize: 10.5 }}>{h.sector}</span></td>
      <td className="num" style={{ fontWeight: 500 }}>{h.qty}</td>
      <td className="num">${h.avg.toFixed(2)}</td>
      <td className="num" style={{ fontWeight: 600, color: "var(--ns-navy)" }}>${h.price.toFixed(2)}</td>
      <td className={"num " + (todayUp ? "up" : "down")} style={{ fontWeight: 600 }}>{todayUp ? "+" : ""}{h.todayPct.toFixed(2)}%</td>
      <td className="num muted">${cost.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
      <td className="num" style={{ fontWeight: 700, color: "var(--ns-navy)" }}>${mv.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
      <td className={"num " + (up ? "up" : "down")} style={{ fontWeight: 700 }}>{up ? "+" : ""}${pl.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
      <td className={"num " + (up ? "up" : "down")} style={{ fontWeight: 600 }}>{up ? "+" : ""}{plPct.toFixed(2)}%</td>
      <td>
        <Sparkline values={sparkCandles.map(c => c.close)} width={180} height={24}
          color={up ? "#0e8a5f" : "#c0392b"}
          fill={up ? "rgba(14,138,95,0.10)" : "rgba(192,57,43,0.10)"} />
      </td>
      <td>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ flex: 1, height: 6, background: "var(--ns-surface-container)", borderRadius: 2, overflow: "hidden" }}>
            <div style={{ height: "100%", width: allocPct + "%", background: h.color }} />
          </div>
          <span className="mono tnum" style={{ fontSize: 11.5, fontWeight: 600, color: "var(--ns-navy)", width: 44, textAlign: "right" }}>{allocPct.toFixed(1)}%</span>
        </div>
      </td>
      <td style={{ textAlign: "right" }}>
        <span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-slate-400)", cursor: "pointer", marginRight: 10 }}>open_in_new</span>
        <span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-slate-400)", cursor: "pointer" }}>more_horiz</span>
      </td>
    </tr>
  );
}

function HoldingsMonthlyCard() {
  const months = [
    { m: "2025-03", pl: +812 }, { m: "2025-04", pl: -284 }, { m: "2025-05", pl: +1182 },
    { m: "2025-06", pl: +268 }, { m: "2025-07", pl: -118 }, { m: "2025-08", pl: +942 },
    { m: "2025-09", pl: +1840 },{ m: "2025-10", pl: -622 }, { m: "2025-11", pl: +1480 },
    { m: "2025-12", pl: +2204 },{ m: "2026-01", pl: +388 }, { m: "2026-02", pl: +382 },
    { m: "2026-03", pl: -208 }, { m: "2026-04", pl: +268 }, { m: "2026-05", pl: +3002 },
  ];
  const maxAbs = Math.max(...months.map(m => Math.abs(m.pl)));
  // Cumulative line
  let cum = 0;
  const cumValues = months.map(m => { cum += m.pl; return cum; });
  const lineMin = 0, lineMax = Math.max(...cumValues);
  return (
    <section className="mp-card">
      <div className="mp-card__head">
        <span className="mp-card__title"><span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-primary)" }}>insights</span>月度已实现盈亏 + 累计曲线</span>
        <span style={{ fontSize: 11.5, color: "var(--ns-on-surface-variant)" }}>15 个月 · 累计 <span className="up" style={{ fontWeight: 700 }}>+${cumValues[cumValues.length-1].toLocaleString()}</span></span>
      </div>
      <div style={{ padding: 24, position: "relative" }}>
        <svg width="100%" height="240" viewBox={`0 0 ${months.length * 70} 240`} preserveAspectRatio="none">
          {/* bars */}
          {months.map((m, i) => {
            const x = i * 70 + 10;
            const w = 50;
            const h = Math.abs(m.pl) / maxAbs * 110;
            const y = m.pl >= 0 ? 150 - h : 150;
            return (
              <g key={i}>
                <rect x={x} y={y} width={w} height={h} fill={m.pl >= 0 ? "var(--mp-up)" : "var(--mp-down)"} opacity="0.55" />
                <text x={x + w/2} y={y - 6} textAnchor="middle" fontFamily="var(--ns-font-mono)" fontSize="10" fontWeight="600"
                  fill={m.pl >= 0 ? "var(--mp-up-deep)" : "var(--mp-down-deep)"}>
                  {m.pl >= 0 ? "+" : "−"}${Math.abs(m.pl)}
                </text>
              </g>
            );
          })}
          <line x1={0} x2={months.length * 70} y1={150} y2={150} stroke="var(--ns-outline-variant)" />
          {/* cumulative line */}
          <path d={"M " + cumValues.map((v, i) => `${i * 70 + 35},${230 - (v - lineMin) / (lineMax - lineMin) * 100}`).join(" L ")}
            stroke="var(--ns-navy)" strokeWidth="2" fill="none" />
          {cumValues.map((v, i) => (
            <circle key={i} cx={i * 70 + 35} cy={230 - (v - lineMin) / (lineMax - lineMin) * 100} r="3" fill="var(--ns-navy)" />
          ))}
          {/* x labels */}
          {months.map((m, i) => (
            <text key={i} x={i * 70 + 35} y={250} textAnchor="middle" fontFamily="var(--ns-font-mono)" fontSize="9.5" fill="var(--ns-slate-400)" letterSpacing="0.02em">
              {m.m.slice(5)}
            </text>
          ))}
        </svg>
        <div style={{ display: "flex", gap: 16, marginTop: 8, fontSize: 11, color: "var(--ns-on-surface-variant)" }}>
          <span><span style={{ width: 10, height: 10, background: "var(--mp-up)", display: "inline-block", opacity: 0.55, marginRight: 4 }} /> 月盈利</span>
          <span><span style={{ width: 10, height: 10, background: "var(--mp-down)", display: "inline-block", opacity: 0.55, marginRight: 4 }} /> 月亏损</span>
          <span><span style={{ width: 10, height: 2, background: "var(--ns-navy)", display: "inline-block", marginRight: 4 }} /> 累计盈亏</span>
        </div>
      </div>
    </section>
  );
}

function AIRiskCard() {
  return (
    <section className="mp-card">
      <div className="mp-card__head">
        <span className="mp-card__title"><span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-primary)" }}>auto_awesome</span>AI 风险分析 · 组合</span>
        <span style={{ fontSize: 10.5, color: "var(--ns-on-surface-variant)", fontFamily: "var(--ns-font-mono)" }}>risk-v2-zh · 09:08 生成</span>
      </div>
      <div className="ai-md" style={{ padding: "20px 24px", fontSize: 14 }}>
        <h2>集中度</h2>
        <p>组合前 <strong>3 个标的</strong>(AAPL · NVDA · TSLA)合计占 <em>49.2%</em>,头部集中度高。<strong>半导体板块</strong>(NVDA · AVGO · TSM)合计 <em>22.4%</em>,与科技整体存在重叠风险:在 AI 资本开支周期下若发生宏观转弱,板块联动下行幅度可能高于历史均值。</p>
        <h2>盈亏结构</h2>
        <p>10 个标的中 <strong>9 个盈利、1 个亏损</strong>。最大正贡献 <em>AAPL +$2,447 (+28.4%)</em>,主要来自服务业务估值重定价;次贡献 <em>TSLA +$2,266 (+16.7%)</em>,Robotaxi 路演后情绪修复。最大负贡献为 <em>AMZN −$312 (−5.2%)</em>,处于高位震荡。</p>
        <h2>关注方向</h2>
        <ul>
          <li>NVDA 现价较均价 +12.8%,RSI 已回到 58,可观察是否再度进入超买区。</li>
          <li>AAPL 处于历史 P/E 上沿,Q3 财报前波动率可能放大。</li>
          <li>SPY (8.2%) 作为防御性底仓比例偏低,可考虑遇深调时加仓。</li>
          <li>当前组合 Beta ≈ 1.18,系统性风险高于大盘。</li>
        </ul>
      </div>
      <div style={{ borderTop: "1px solid var(--ns-outline-variant)", padding: "10px 18px", display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 11, color: "var(--ns-on-surface-variant)" }}>
        <span>仅描述数据 · 不构成买卖建议</span>
        <span style={{ display: "flex", gap: 16 }}>
          <span style={{ display: "inline-flex", gap: 4, alignItems: "center", cursor: "pointer" }}><span className="material-symbols-outlined" style={{ fontSize: 14 }}>refresh</span>重新生成</span>
          <span style={{ display: "inline-flex", gap: 4, alignItems: "center", cursor: "pointer" }}><span className="material-symbols-outlined" style={{ fontSize: 14 }}>content_copy</span>复制</span>
        </span>
      </div>
    </section>
  );
}

window.PageHoldings = PageHoldings;
