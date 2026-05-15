/* global React */
// /trades — full transaction ledger.
// Top: KPI strip + filters + add form. Center: ledger. Right rail: monthly P&L sparkline + Robinhood importer.

const { useState: useStateT, useMemo: useMemoT } = React;

function PageTrades() {
  const [filter, setFilter] = useStateT("all");
  const [eventKind, setEventKind] = useStateT("buy");

  const allTrades = useMemoT(() => buildTradeLedger(), []);
  const filteredTrades = useMemoT(() => {
    if (filter === "all") return allTrades;
    return allTrades.filter(t => t.kind === filter);
  }, [allTrades, filter]);

  return (
    <div style={{ width: 2560, minHeight: 1700, background: "var(--ns-background)", display: "flex", flexDirection: "column", overflowX: "hidden" }}>
      <Chrome activeKey="trades" />

      {/* Hero strip */}
      <section style={{ padding: "32px 48px 24px", display: "flex", alignItems: "flex-end", justifyContent: "space-between" }}>
        <div>
          <span className="mp-eyebrow mp-eyebrow--primary">交易记录</span>
          <h1 className="grotesk" style={{ fontSize: 48, fontWeight: 700, letterSpacing: "-0.04em", color: "var(--ns-navy)", margin: "6px 0 0", lineHeight: 1 }}>
            Trade Ledger
          </h1>
          <div className="mp-rule" style={{ marginTop: 12 }} />
          <p style={{ fontSize: 14, color: "var(--ns-on-surface-variant)", margin: "12px 0 0", maxWidth: 640 }}>
            买卖、拆股、分红的完整流水。所有持仓与已实现盈亏均由此推算。可由 Robinhood CSV 导入。
          </p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="mp-btn mp-btn--ghost mp-btn--lg">
            <span className="material-symbols-outlined">upload_file</span>
            导入 Robinhood CSV
          </button>
          <button className="mp-btn mp-btn--ghost mp-btn--lg">
            <span className="material-symbols-outlined">download</span>
            导出 CSV
          </button>
        </div>
      </section>

      {/* KPI strip */}
      <section style={{ padding: "0 48px 16px", display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 16 }}>
        <KPICard label="总笔数" value="247" hint="2025-01 起" icon="receipt_long" />
        <KPICard label="已实现盈亏 · YTD" value="+$8,418.20" hint="2025 起累计 +$12,604" valueColor="var(--mp-up)" icon="payments" />
        <KPICard label="胜率" value="62.4%" hint="154 胜 / 93 负" icon="trending_up" />
        <KPICard label="平均持仓天数" value="184d" hint="中位 142d" icon="schedule" />
        <KPICard label="本月新笔数" value="14" hint="6 买 · 5 卖 · 3 分红" icon="event_available" />
      </section>

      {/* Filters + Add row */}
      <section style={{ padding: "8px 48px 16px" }}>
        <div className="mp-card" style={{ padding: 18 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16, flexWrap: "wrap" }}>
            <span className="mp-eyebrow mp-eyebrow--primary">筛选</span>
            <div style={{ display: "flex", gap: 4 }}>
              {[
                { k: "all",      label: "全部",  count: 247 },
                { k: "trade",    label: "买卖",  count: 218 },
                { k: "split",    label: "拆股",  count: 4 },
                { k: "dividend", label: "分红",  count: 25 },
              ].map(f => (
                <button key={f.k}
                  onClick={() => setFilter(f.k)}
                  className={"mp-chip " + (filter === f.k ? "mp-chip--active" : "")}
                  style={{ height: 30, padding: "0 14px" }}>
                  {f.label} <span style={{ marginLeft: 4, opacity: 0.7 }}>{f.count}</span>
                </button>
              ))}
            </div>
            <span style={{ width: 1, height: 24, background: "var(--ns-outline-variant)", margin: "0 6px" }} />
            <span style={{ fontSize: 11, color: "var(--ns-on-surface-variant)" }}>代码</span>
            <input placeholder="搜索 AAPL · NVDA …"
              style={{ height: 30, padding: "0 12px", border: "1px solid var(--ns-outline-variant)", borderRadius: 2, fontFamily: "var(--ns-font-mono)", fontSize: 12, width: 180 }} />
            <span style={{ fontSize: 11, color: "var(--ns-on-surface-variant)" }}>区间</span>
            <input placeholder="2025-01-01"
              style={{ height: 30, padding: "0 12px", border: "1px solid var(--ns-outline-variant)", borderRadius: 2, fontFamily: "var(--ns-font-mono)", fontSize: 12, width: 120 }} />
            <span style={{ color: "var(--ns-slate-400)" }}>→</span>
            <input placeholder="今天"
              style={{ height: 30, padding: "0 12px", border: "1px solid var(--ns-outline-variant)", borderRadius: 2, fontFamily: "var(--ns-font-mono)", fontSize: 12, width: 120 }} />
          </div>

          <hr className="mp-hr" style={{ margin: "0 -18px 16px" }} />

          <div style={{ display: "flex", alignItems: "flex-end", gap: 12, flexWrap: "wrap" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span className="mp-eyebrow mp-eyebrow--primary">添加记录</span>
              <div className="mp-seg">
                {[
                  { k: "buy", label: "买入" },
                  { k: "sell", label: "卖出" },
                  { k: "split", label: "拆股" },
                  { k: "dividend", label: "分红" },
                ].map(k => (
                  <button key={k.k} className={eventKind === k.k ? "is-active" : ""} onClick={() => setEventKind(k.k)}>{k.label}</button>
                ))}
              </div>
            </div>
            <FieldT label="代码" value="AAPL" w={120} />
            {eventKind === "buy" || eventKind === "sell" ? (
              <>
                <FieldT label="数量" value="10" w={100} />
                <FieldT label="价格 USD" value="221.34" w={130} />
                <FieldT label="手续费" value="0.00" w={100} />
                <FieldT label="日期" value="2026-05-12" w={140} />
              </>
            ) : eventKind === "split" ? (
              <>
                <FieldT label="比例 (1 → ?)" value="4" w={140} />
                <FieldT label="生效日期" value="2026-05-12" w={140} />
              </>
            ) : (
              <>
                <FieldT label="每股金额" value="0.25" w={120} />
                <FieldT label="总金额" value="12.50" w={120} />
                <FieldT label="除息日" value="2026-05-12" w={140} />
              </>
            )}
            <FieldT label="备注 (可选)" value="" w={260} placeholder="例如:财报后建仓" />
            <button className="mp-btn mp-btn--primary" style={{ height: 38, padding: "0 20px" }}>
              <span className="material-symbols-outlined">add</span>
              记录
            </button>
          </div>
        </div>
      </section>

      {/* Ledger + side rail */}
      <section style={{ padding: "0 48px 32px", display: "grid", gridTemplateColumns: "minmax(0, 1fr) 480px", gap: 16 }}>
        <div className="mp-card" style={{ overflow: "hidden" }}>
          <div className="mp-card__head">
            <span className="mp-card__title"><span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-primary)" }}>list_alt</span>流水 · {filteredTrades.length} 条</span>
            <span style={{ fontSize: 11.5, color: "var(--ns-on-surface-variant)" }}>按时间倒序 · 已实现盈亏 <span className="up" style={{ fontWeight: 700 }}>+$1,663.00</span> (含本页)</span>
          </div>
          <div style={{ maxHeight: 1050, overflow: "auto" }} className="mp-scroll">
            <table className="mp-table">
              <thead>
                <tr>
                  <th style={{ width: 140 }}>时间</th>
                  <th style={{ width: 90 }}>代码</th>
                  <th style={{ width: 90 }}>类型</th>
                  <th style={{ width: 120, textAlign: "right" }}>数量</th>
                  <th style={{ width: 140, textAlign: "right" }}>价格</th>
                  <th style={{ width: 140, textAlign: "right" }}>总额</th>
                  <th style={{ width: 90, textAlign: "right" }}>手续费</th>
                  <th style={{ width: 140, textAlign: "right" }}>已实现盈亏</th>
                  <th style={{ width: 120, textAlign: "right" }}>盈亏 %</th>
                  <th>备注</th>
                  <th style={{ width: 110, textAlign: "right" }}></th>
                </tr>
              </thead>
              <tbody>
                {filteredTrades.map((t, i) => <LedgerRow key={i} t={t} />)}
              </tbody>
            </table>
          </div>
          <div style={{ padding: "12px 18px", display: "flex", justifyContent: "space-between", alignItems: "center", borderTop: "1px solid var(--ns-outline-variant)", background: "var(--ns-surface-container-low)" }}>
            <span style={{ fontSize: 12, color: "var(--ns-on-surface-variant)" }}>显示 1 – {filteredTrades.length} · 总 247 条</span>
            <div style={{ display: "flex", gap: 4 }}>
              <button className="mp-btn mp-btn--ghost mp-btn--sm">‹ 上一页</button>
              <button className="mp-btn mp-btn--ghost mp-btn--sm">1</button>
              <button className="mp-btn mp-btn--navy mp-btn--sm">2</button>
              <button className="mp-btn mp-btn--ghost mp-btn--sm">3</button>
              <button className="mp-btn mp-btn--ghost mp-btn--sm">4</button>
              <button className="mp-btn mp-btn--ghost mp-btn--sm">下一页 ›</button>
            </div>
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <MonthlyRealizedCard />
          <ImportCard />
          <PnLByTickerCard />
        </div>
      </section>
    </div>
  );
}

function KPICard({ label, value, hint, valueColor, icon }) {
  return (
    <div className="mp-card" style={{ padding: "18px 20px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <span className="mp-eyebrow mp-eyebrow--primary">{label}</span>
        {icon && <span className="material-symbols-outlined" style={{ fontSize: 18, color: "var(--ns-outline-variant)" }}>{icon}</span>}
      </div>
      <div className="grotesk tnum" style={{ fontSize: 30, fontWeight: 700, letterSpacing: "-0.02em", color: valueColor || "var(--ns-navy)", lineHeight: 1.1, marginTop: 6 }}>{value}</div>
      <div style={{ fontSize: 11.5, color: "var(--ns-on-surface-variant)", marginTop: 4 }}>{hint}</div>
    </div>
  );
}

function FieldT({ label, value, w, placeholder }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span className="mp-eyebrow">{label}</span>
      <input defaultValue={value} placeholder={placeholder}
        style={{ width: w, height: 38, padding: "0 12px", border: "1px solid var(--ns-outline-variant)", borderRadius: 2, fontFamily: value && /^[0-9]/.test(value) ? "var(--ns-font-mono)" : "var(--ns-font-body)", fontSize: 13, fontWeight: 500, color: "var(--ns-navy)" }} />
    </label>
  );
}

function buildTradeLedger() {
  const trades = [
    { date: "2026-05-09", time: "14:32", ticker: "NVDA", kind: "trade", action: "buy",  qty: 30, price: 132.40, fees: 0,    pl: null,   notes: "回调买入" },
    { date: "2026-05-07", time: "09:48", ticker: "TSLA", kind: "trade", action: "sell", qty: 25, price: 268.40, fees: 0,    pl: +2384, notes: "止盈减仓 · 减一半仓位" },
    { date: "2026-05-06", time: "11:21", ticker: "AAPL", kind: "dividend",                                                    qty: 50, price: 0.25, fees: 0, total: 12.50, notes: "Q2 分红" },
    { date: "2026-05-03", time: "10:11", ticker: "META", kind: "trade", action: "buy",  qty: 8,  price: 584.20, fees: 0,    pl: null,   notes: "财报后回调 · 加仓" },
    { date: "2026-05-02", time: "09:32", ticker: "GOOGL",kind: "trade", action: "sell", qty: 15, price: 198.40, fees: 0,    pl: +618,  notes: "止盈" },
    { date: "2026-04-30", time: "—",     ticker: "TSLA", kind: "split",                                                       ratio: 3,  notes: "1 → 3 拆股 · 自动应用 · yfinance" },
    { date: "2026-04-22", time: "09:33", ticker: "AAPL", kind: "trade", action: "buy",  qty: 10, price: 168.40, fees: 0,    pl: null,   notes: "回调加仓 · MACD 金叉" },
    { date: "2026-04-18", time: "10:42", ticker: "AVGO", kind: "trade", action: "buy",  qty: 6,  price: 162.50, fees: 0,    pl: null,   notes: "AI 周期建仓" },
    { date: "2026-04-15", time: "13:08", ticker: "SPY",  kind: "trade", action: "buy",  qty: 20, price: 582.10, fees: 0,    pl: null,   notes: "定投" },
    { date: "2026-04-08", time: "10:48", ticker: "MSFT", kind: "dividend",                                                    qty: 40, price: 0.83, fees: 0, total: 33.20, notes: "Q2 分红" },
    { date: "2026-04-04", time: "11:42", ticker: "NVDA", kind: "trade", action: "sell", qty: 10, price: 154.20, fees: 0,    pl: +268,  notes: "调仓" },
    { date: "2026-03-28", time: "09:31", ticker: "NVDA", kind: "trade", action: "buy",  qty: 20, price: 127.40, fees: 0,    pl: null,   notes: "AI 周期" },
    { date: "2026-03-14", time: "14:01", ticker: "AAPL", kind: "trade", action: "buy",  qty: 20, price: 175.90, fees: 0,    pl: null,   notes: "财报后" },
    { date: "2026-03-10", time: "10:15", ticker: "TSM",  kind: "trade", action: "buy",  qty: 5,  price: 178.40, fees: 0,    pl: null,   notes: "晶圆代工龙头" },
    { date: "2026-03-04", time: "10:38", ticker: "AMZN", kind: "trade", action: "buy",  qty: 12, price: 218.50, fees: 0,    pl: null,   notes: "云业务持续" },
    { date: "2026-02-28", time: "—",     ticker: "AVGO", kind: "split",                                                       ratio: 10, notes: "1 → 10 拆股 · 自动应用" },
    { date: "2026-02-22", time: "09:48", ticker: "META", kind: "trade", action: "buy",  qty: 4,  price: 612.00, fees: 0,    pl: null,   notes: "" },
    { date: "2026-02-14", time: "10:12", ticker: "MSFT", kind: "trade", action: "sell", qty: 10, price: 458.20, fees: 0,    pl: +382.50,notes: "" },
  ];
  return trades;
}

function LedgerRow({ t }) {
  if (t.kind === "trade") {
    const up = t.action === "buy";
    return (
      <tr>
        <td className="muted mono" style={{ fontSize: 12 }}>
          <div>{t.date}</div>
          <div style={{ color: "var(--ns-slate-400)", fontSize: 11 }}>{t.time} EDT</div>
        </td>
        <td><a href="#" style={{ color: "var(--ns-navy)", fontWeight: 700, textDecoration: "none", fontFamily: "var(--ns-font-headline)", letterSpacing: "-0.01em" }}>{t.ticker}</a></td>
        <td>
          <span className={"mp-chip " + (up ? "mp-chip--periwinkle" : "mp-chip--down")} style={{ height: 22 }}>
            {up ? "买入" : "卖出"}
          </span>
        </td>
        <td className="num" style={{ fontWeight: 500 }}>{t.qty}</td>
        <td className="num">${t.price.toFixed(2)}</td>
        <td className="num" style={{ fontWeight: 600, color: "var(--ns-navy)" }}>${(t.qty * t.price).toFixed(2)}</td>
        <td className="num muted">{t.fees ? "$" + t.fees.toFixed(2) : "—"}</td>
        <td className={"num " + (t.pl == null ? "muted" : t.pl >= 0 ? "up" : "down")} style={{ fontWeight: 600 }}>
          {t.pl == null ? "—" : (t.pl >= 0 ? "+" : "") + "$" + t.pl.toFixed(2)}
        </td>
        <td className={"num " + (t.pl == null ? "muted" : t.pl >= 0 ? "up" : "down")}>
          {t.pl != null ? (t.pl >= 0 ? "+" : "") + ((t.pl / (t.qty * t.price)) * 100).toFixed(2) + "%" : "—"}
        </td>
        <td className="muted" style={{ fontSize: 12, maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.notes}</td>
        <td style={{ textAlign: "right" }}>
          <span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-slate-400)", cursor: "pointer", marginRight: 10 }}>edit</span>
          <span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-slate-400)", cursor: "pointer" }}>delete_outline</span>
        </td>
      </tr>
    );
  }
  if (t.kind === "split") {
    return (
      <tr style={{ background: "rgba(141,82,231,0.04)" }}>
        <td className="muted mono" style={{ fontSize: 12 }}>{t.date}</td>
        <td><a href="#" style={{ color: "var(--ns-navy)", fontWeight: 700, textDecoration: "none", fontFamily: "var(--ns-font-headline)" }}>{t.ticker}</a></td>
        <td><span className="mp-chip" style={{ background: "rgba(141,82,231,0.12)", color: "#5e2cb4", border: "0", height: 22 }}>拆股</span></td>
        <td colSpan={6} className="muted" style={{ fontSize: 12.5 }}>
          1 → <span className="mono" style={{ color: "var(--ns-navy)", fontWeight: 600 }}>{t.ratio}</span> 拆股 · 自动重算 {t.ticker} 持仓 · <span className="muted">{t.notes}</span>
        </td>
        <td colSpan={1} className="muted" />
        <td style={{ textAlign: "right" }}>
          <span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-slate-400)", cursor: "pointer" }}>delete_outline</span>
        </td>
      </tr>
    );
  }
  if (t.kind === "dividend") {
    return (
      <tr style={{ background: "rgba(14,138,95,0.04)" }}>
        <td className="muted mono" style={{ fontSize: 12 }}>{t.date}</td>
        <td><a href="#" style={{ color: "var(--ns-navy)", fontWeight: 700, textDecoration: "none", fontFamily: "var(--ns-font-headline)" }}>{t.ticker}</a></td>
        <td><span className="mp-chip mp-chip--up" style={{ height: 22 }}>分红</span></td>
        <td className="num" style={{ fontWeight: 500 }}>{t.qty}</td>
        <td className="num">${t.price.toFixed(4)}/股</td>
        <td className="num" style={{ fontWeight: 600, color: "var(--mp-up)" }}>+${t.total.toFixed(2)}</td>
        <td className="muted">—</td>
        <td className="num up" style={{ fontWeight: 600 }}>+${t.total.toFixed(2)}</td>
        <td className="muted">—</td>
        <td className="muted" style={{ fontSize: 12 }}>{t.notes}</td>
        <td style={{ textAlign: "right" }}>
          <span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-slate-400)", cursor: "pointer" }}>delete_outline</span>
        </td>
      </tr>
    );
  }
}

function MonthlyRealizedCard() {
  // Sample 14 months
  const months = [
    { m: "2025-03", pl: +812 }, { m: "2025-04", pl: -284 }, { m: "2025-05", pl: +1182 },
    { m: "2025-06", pl: +268 }, { m: "2025-07", pl: -118 }, { m: "2025-08", pl: +942 },
    { m: "2025-09", pl: +1840 },{ m: "2025-10", pl: -622 }, { m: "2025-11", pl: +1480 },
    { m: "2025-12", pl: +2204 },{ m: "2026-01", pl: +388 }, { m: "2026-02", pl: +382 },
    { m: "2026-03", pl: -208 }, { m: "2026-04", pl: +268 }, { m: "2026-05", pl: +3002 },
  ];
  const maxAbs = Math.max(...months.map(m => Math.abs(m.pl)));
  return (
    <section className="mp-card">
      <div className="mp-card__head">
        <span className="mp-card__title"><span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-primary)" }}>insights</span>月度已实现盈亏</span>
        <span style={{ fontSize: 11.5, color: "var(--ns-on-surface-variant)" }}>15 个月 · 累计 <span className="up" style={{ fontWeight: 700 }}>+$11,536</span></span>
      </div>
      <div style={{ padding: "20px 18px 16px" }}>
        <div style={{ display: "flex", gap: 4, alignItems: "flex-end", height: 140 }}>
          {months.map((m, i) => {
            const pct = Math.abs(m.pl) / maxAbs;
            return (
              <div key={i} style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "flex-end", height: "100%" }}>
                <div style={{
                  height: pct * 100 + "%",
                  background: m.pl >= 0 ? "var(--mp-up)" : "var(--mp-down)",
                  borderRadius: "2px 2px 0 0",
                  marginBottom: 4,
                  minHeight: 2,
                  position: "relative",
                }} title={`${m.m}: ${m.pl >= 0 ? "+" : ""}$${m.pl}`}>
                  {i === months.length - 1 && (
                    <span className="mono" style={{ position: "absolute", top: -18, left: "50%", transform: "translateX(-50%)", fontSize: 10, fontWeight: 700, color: "var(--mp-up)", whiteSpace: "nowrap" }}>+$3k</span>
                  )}
                </div>
                <div className="mono" style={{ fontSize: 9, color: "var(--ns-slate-400)", textAlign: "center", letterSpacing: "0.02em" }}>{m.m.slice(5)}</div>
              </div>
            );
          })}
        </div>
        <hr className="mp-hr" style={{ margin: "16px 0 12px" }} />
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--ns-on-surface-variant)" }}>
          <span>最佳月 · <span className="up mono" style={{ fontWeight: 700 }}>+$3,002</span> (2026-05)</span>
          <span>最差月 · <span className="down mono" style={{ fontWeight: 700 }}>-$622</span> (2025-10)</span>
        </div>
      </div>
    </section>
  );
}

function ImportCard() {
  return (
    <section className="mp-card">
      <div className="mp-card__head">
        <span className="mp-card__title"><span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-primary)" }}>upload_file</span>从 Robinhood 导入</span>
      </div>
      <div style={{ padding: 20 }}>
        <div style={{ border: "1px dashed var(--ns-outline-variant)", borderRadius: 2, padding: "24px 16px", textAlign: "center", background: "var(--ns-surface-container-low)" }}>
          <span className="material-symbols-outlined" style={{ fontSize: 36, color: "var(--ns-primary)" }}>cloud_upload</span>
          <div style={{ fontSize: 14, fontWeight: 600, color: "var(--ns-navy)", marginTop: 6 }}>拖入 Robinhood CSV</div>
          <div style={{ fontSize: 11.5, color: "var(--ns-on-surface-variant)", marginTop: 4 }}>或 <span style={{ color: "var(--ns-primary)", textDecoration: "underline" }}>点击选择文件</span></div>
        </div>
        <div style={{ marginTop: 12, fontSize: 11.5, color: "var(--ns-on-surface-variant)", lineHeight: 1.6 }}>
          上次导入 · <span className="mono" style={{ color: "var(--ns-navy)", fontWeight: 600 }}>2026-04-02</span> · 28 条新交易 · 4 条重复跳过
        </div>
      </div>
    </section>
  );
}

function PnLByTickerCard() {
  const rows = [
    { ticker: "AAPL", pl: +2447,  pct: +28.39, color: "#0066cc" },
    { ticker: "TSLA", pl: +2384,  pct: +21.40, color: "#022448" },
    { ticker: "NVDA", pl: +1108,  pct: +9.20,  color: "#4d94ff" },
    { ticker: "META", pl: +822,   pct: +12.04, color: "#0e8a5f" },
    { ticker: "MSFT", pl: +383,   pct: +5.20,  color: "#9b59b6" },
    { ticker: "GOOGL",pl: +618,   pct: +8.40,  color: "#16a085" },
    { ticker: "AVGO", pl: +118,   pct: +1.81,  color: "#c0570c" },
    { ticker: "AMZN", pl: -312,   pct: -2.40,  color: "#c0392b" },
  ];
  const maxAbs = Math.max(...rows.map(r => Math.abs(r.pl)));
  return (
    <section className="mp-card">
      <div className="mp-card__head">
        <span className="mp-card__title"><span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-primary)" }}>leaderboard</span>按代码 · 已实现盈亏</span>
        <span style={{ fontSize: 11.5, color: "var(--ns-on-surface-variant)" }}>2025 起累计</span>
      </div>
      <ul style={{ listStyle: "none", margin: 0, padding: "10px 16px 18px" }}>
        {rows.map(r => {
          const up = r.pl >= 0;
          return (
            <li key={r.ticker} style={{ display: "grid", gridTemplateColumns: "50px 1fr 90px 64px", gap: 10, alignItems: "center", padding: "7px 0" }}>
              <span className="grotesk" style={{ fontWeight: 700, fontSize: 13, color: "var(--ns-navy)" }}>{r.ticker}</span>
              <div style={{ height: 8, background: "var(--ns-surface-container)", borderRadius: 2, position: "relative", overflow: "hidden" }}>
                <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: Math.abs(r.pl) / maxAbs * 100 + "%", background: up ? "var(--mp-up)" : "var(--mp-down)" }} />
              </div>
              <span className={"mono tnum " + (up ? "up" : "down")} style={{ fontSize: 12, fontWeight: 600, textAlign: "right" }}>{up ? "+" : ""}${r.pl}</span>
              <span className={"mono tnum " + (up ? "up" : "down")} style={{ fontSize: 11.5, textAlign: "right" }}>{up ? "+" : ""}{r.pct.toFixed(1)}%</span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

window.PageTrades = PageTrades;
