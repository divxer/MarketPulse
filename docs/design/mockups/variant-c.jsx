/* global React */
// Variant C — Modern minimal · light theme, full-width chart, floating panels.

const { useState: useStateC, useMemo: useMemoC } = React;

function VariantC() {
  const [period, setPeriod] = useStateC("6M");
  const watchlist = useMemoC(() => MPShell.buildWatchlist(), []);
  const meta = watchlist[0];
  const periodCounts = { "60D": 60, "6M": 130, "YTD": 95, "1Y": 250, "5Y": 260, "All": 320 };
  const count = periodCounts[period] || 130;
  const candles = useMemoC(() => {
    const c = MPData.generateCandles({ seed: meta.seed + count, count, basePrice: meta.price * 0.78, vol: 0.018, drift: 0.0018 });
    const last = c[c.length - 1];
    last.close = meta.price;
    last.open = meta.price - meta.chg;
    last.high = Math.max(last.high, meta.price);
    return c;
  }, [meta, count]);

  return (
    <div className="vc-root" style={{ width: 2560, minHeight: 1700, display: "flex", flexDirection: "column", overflowX: "hidden" }}>
      <Chrome activeKey="stock" />
      <VCHero meta={meta} />

      <div style={{ padding: "0 48px 32px", display: "flex", flexDirection: "column", gap: 24 }}>

        {/* ─── CHART HERO ─── */}
        <section style={{ position: "relative" }}>
          <VCChartToolbar period={period} setPeriod={setPeriod} />

          <div style={{ position: "relative", background: "white", border: "1px solid var(--ns-outline-variant)", borderRadius: 4, padding: "20px 24px 24px", boxShadow: "var(--ns-shadow-card)" }}>
            {/* Floating OHLC */}
            <div className="vc-glass" style={{ position: "absolute", top: 32, left: 40, padding: "12px 18px", borderRadius: 4, zIndex: 2, minWidth: 320 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                <span className="mp-eyebrow mp-eyebrow--primary">{meta.ticker} · 日 K</span>
                <span className="mp-chip mp-chip--up" style={{ height: 18, fontSize: 9.5 }}>实时</span>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(5, auto)", gap: "2px 18px", fontFamily: "var(--ns-font-mono)", fontSize: 12 }}>
                <span><span style={{ color: "var(--ns-slate-400)" }}>开</span> <span style={{ fontWeight: 600 }}>219.80</span></span>
                <span><span style={{ color: "var(--ns-slate-400)" }}>高</span> <span style={{ color: "var(--mp-up)", fontWeight: 600 }}>222.10</span></span>
                <span><span style={{ color: "var(--ns-slate-400)" }}>低</span> <span style={{ color: "var(--mp-down)", fontWeight: 600 }}>218.95</span></span>
                <span><span style={{ color: "var(--ns-slate-400)" }}>收</span> <span style={{ fontWeight: 700, color: "var(--ns-navy)" }}>{meta.price.toFixed(2)}</span></span>
                <span><span style={{ color: "var(--ns-slate-400)" }}>量</span> <span style={{ fontWeight: 600 }}>{meta.vol}</span></span>
              </div>
            </div>

            {/* Floating Position panel */}
            <div className="vc-glass" style={{ position: "absolute", top: 32, right: 40, padding: "14px 18px", borderRadius: 4, zIndex: 2, minWidth: 320 }}>
              <VCFloatingPosition meta={meta} />
            </div>

            {/* AI inline insight */}
            <div className="vc-glass" style={{ position: "absolute", bottom: 32, left: 40, padding: "10px 14px", borderRadius: 4, zIndex: 2, maxWidth: 520, display: "flex", gap: 12, alignItems: "flex-start" }}>
              <span className="material-symbols-outlined" style={{ color: "var(--ns-primary)", fontSize: 18 }}>auto_awesome</span>
              <div style={{ fontSize: 12.5, lineHeight: 1.5, color: "var(--ns-on-surface)" }}>
                <span className="mp-eyebrow mp-eyebrow--primary" style={{ marginRight: 8 }}>AI · INLINE</span>
                <strong style={{ color: "var(--ns-navy)", fontWeight: 600 }}>上行趋势延续。</strong>
                MACD 金叉 6 日,RSI 62.4 未超买,布林上轨距现价 3.1%。日线收盘高于 SMA50 已 28 个交易日。
              </div>
            </div>

            <CandleChart candles={candles} width={2400} height={620} theme="light" showVolume showSMA showBB />
          </div>
        </section>

        {/* ─── INDICATOR ROW ─── */}
        <section style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24 }}>
          <div className="mp-card" style={{ padding: 24 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 12 }}>
              <div>
                <span className="mp-eyebrow mp-eyebrow--primary">RSI · 14</span>
                <div className="grotesk" style={{ fontSize: 22, fontWeight: 700, color: "var(--ns-navy)", letterSpacing: "-0.01em", marginTop: 2 }}>62.4 <span style={{ fontSize: 13, color: "var(--mp-up)", fontWeight: 600, marginLeft: 6 }}>偏强 · 未超买</span></div>
              </div>
              <span className="mono" style={{ fontSize: 11, color: "var(--ns-on-surface-variant)" }}>30 / 50 / 70 区间</span>
            </div>
            <RSIChart candles={candles} width={1170} height={140} theme="light" />
          </div>
          <div className="mp-card" style={{ padding: 24 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 12 }}>
              <div>
                <span className="mp-eyebrow mp-eyebrow--primary">MACD · 12 / 26 / 9</span>
                <div className="grotesk" style={{ fontSize: 22, fontWeight: 700, color: "var(--ns-navy)", letterSpacing: "-0.01em", marginTop: 2 }}>
                  +0.43 <span style={{ fontSize: 13, color: "var(--mp-up)", fontWeight: 600, marginLeft: 6 }}>金叉延续 · 柱 +6</span>
                </div>
              </div>
              <span className="mono" style={{ fontSize: 11, color: "var(--ns-on-surface-variant)" }}>DIF +1.84 · DEA +1.41</span>
            </div>
            <MACDChart candles={candles} width={1170} height={140} theme="light" />
          </div>
        </section>

        {/* ─── AI long-form + side rail ─── */}
        <section style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 24 }}>
          <VCAILongCard meta={meta} />
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <VCRecordCard meta={meta} />
            <VCNewsCard />
          </div>
        </section>

        {/* ─── Recent trades — full width ─── */}
        <VCTradesCard ticker={meta.ticker} />
      </div>
    </div>
  );
}

function VCHero({ meta }) {
  const up = meta.chg >= 0;
  return (
    <section style={{ padding: "32px 48px 24px", display: "flex", alignItems: "flex-end", gap: 48 }}>
      <div>
        <span className="mp-eyebrow mp-eyebrow--primary">个股 · NASDAQ</span>
        <h1 className="grotesk" style={{ fontSize: 56, fontWeight: 700, letterSpacing: "-0.04em", color: "var(--ns-navy)", margin: "6px 0 4px", lineHeight: 1 }}>
          {meta.ticker} <span style={{ fontWeight: 300, color: "var(--ns-on-surface-variant)" }}>·</span> <span style={{ fontSize: 32, fontWeight: 400, color: "var(--ns-on-surface)", letterSpacing: "-0.02em" }}>{meta.name}</span>
        </h1>
        <div className="mp-rule" style={{ marginTop: 12 }} />
      </div>

      <div style={{ marginLeft: "auto", display: "flex", alignItems: "baseline", gap: 24 }}>
        <div style={{ textAlign: "right" }}>
          <span className="mp-eyebrow">现价 · USD</span>
          <div className="mono tnum" style={{ fontSize: 72, fontWeight: 600, color: "var(--ns-navy)", letterSpacing: "-0.04em", lineHeight: 1 }}>{meta.price.toFixed(2)}</div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="mp-eyebrow">日涨跌</span>
          <span className={`grotesk tnum ${up ? "up" : "down"}`} style={{ fontSize: 28, fontWeight: 700 }}>
            {up ? "+" : ""}{meta.chg.toFixed(2)}
          </span>
          <span className={`mono tnum ${up ? "up" : "down"}`} style={{ fontSize: 16, fontWeight: 600 }}>
            {up ? "+" : ""}{meta.pct.toFixed(2)}%
          </span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginLeft: 16 }}>
          <button className="mp-btn mp-btn--ghost mp-btn--lg">
            <span className="material-symbols-outlined">star_border</span> 加自选
          </button>
          <button className="mp-btn mp-btn--navy mp-btn--lg">
            <span className="material-symbols-outlined">auto_awesome</span> AI 分析
          </button>
        </div>
      </div>
    </section>
  );
}

function VCChartToolbar({ period, setPeriod }) {
  const periods = ["60D", "6M", "YTD", "1Y", "5Y", "All"];
  return (
    <div style={{ display: "flex", alignItems: "center", marginBottom: 12, gap: 16 }}>
      <span className="mp-eyebrow">区间</span>
      <div style={{ display: "flex", gap: 4 }}>
        {periods.map(p => (
          <button key={p}
            onClick={() => setPeriod(p)}
            style={{
              padding: "8px 16px",
              fontFamily: "var(--ns-font-headline)",
              fontWeight: 600,
              fontSize: 12,
              letterSpacing: "0.04em",
              background: period === p ? "var(--ns-navy)" : "transparent",
              color: period === p ? "white" : "var(--ns-on-surface-variant)",
              border: "1px solid",
              borderColor: period === p ? "var(--ns-navy)" : "var(--ns-outline-variant)",
              borderRadius: 2,
              cursor: "pointer",
              transition: "all 200ms",
            }}>{p}</button>
        ))}
      </div>
      <span style={{ flex: 1 }} />
      <div style={{ display: "flex", gap: 4 }}>
        <span className="mp-chip mp-chip--active"><span className="material-symbols-outlined" style={{ fontSize: 13 }}>check_box</span>BOLL</span>
        <span className="mp-chip mp-chip--active"><span className="material-symbols-outlined" style={{ fontSize: 13 }}>check_box</span>SMA 50/200</span>
        <span className="mp-chip"><span className="material-symbols-outlined" style={{ fontSize: 13 }}>add</span>添加指标</span>
      </div>
      <div style={{ display: "flex", gap: 4 }}>
        <button className="mp-btn mp-btn--ghost mp-btn--sm"><span className="material-symbols-outlined">candlestick_chart</span></button>
        <button className="mp-btn mp-btn--ghost mp-btn--sm"><span className="material-symbols-outlined">show_chart</span></button>
        <button className="mp-btn mp-btn--ghost mp-btn--sm"><span className="material-symbols-outlined">fullscreen</span></button>
      </div>
    </div>
  );
}

function VCFloatingPosition({ meta }) {
  const qty = 50, avg = 172.40, cost = qty * avg, mv = qty * meta.price, pl = mv - cost, plPct = pl / cost * 100;
  const up = pl >= 0;
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8 }}>
        <span className="mp-eyebrow mp-eyebrow--primary">您的持仓</span>
        <span style={{ fontSize: 10.5, color: "var(--ns-on-surface-variant)" }}>{qty} 股 · 均价 ${avg.toFixed(2)}</span>
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 }}>
        <span style={{ fontSize: 11, color: "var(--ns-on-surface-variant)" }}>市值</span>
        <span className="mono tnum" style={{ fontSize: 18, fontWeight: 700, color: "var(--ns-navy)" }}>${mv.toFixed(2)}</span>
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8 }}>
        <span style={{ fontSize: 11, color: "var(--ns-on-surface-variant)" }}>未实现盈亏</span>
        <span className={`grotesk tnum ${up ? "up" : "down"}`} style={{ fontSize: 22, fontWeight: 700 }}>
          {up ? "+" : ""}${Math.abs(pl).toFixed(2)}
        </span>
      </div>
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <span className={`mono tnum ${up ? "up" : "down"}`} style={{ fontSize: 12, fontWeight: 600 }}>
          {up ? "+" : ""}{plPct.toFixed(2)}% · 今日 +${(qty * meta.chg).toFixed(2)}
        </span>
      </div>
    </div>
  );
}

function VCAILongCard({ meta }) {
  return (
    <section className="mp-card" style={{ padding: "28px 32px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 16 }}>
        <div>
          <span className="mp-eyebrow mp-eyebrow--primary">AI 研究报告</span>
          <h2 className="grotesk" style={{ fontSize: 28, fontWeight: 700, color: "var(--ns-navy)", letterSpacing: "-0.02em", margin: "4px 0 4px" }}>{meta.ticker} · {meta.name}</h2>
          <span style={{ fontSize: 12, color: "var(--ns-on-surface-variant)", fontFamily: "var(--ns-font-mono)" }}>analysis-v2-zh · claude-sonnet-4.5 · 生成于 09:41 · 缓存 24h</span>
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <button className="mp-btn mp-btn--ghost mp-btn--sm"><span className="material-symbols-outlined">refresh</span>重新生成</button>
          <button className="mp-btn mp-btn--ghost mp-btn--sm"><span className="material-symbols-outlined">content_copy</span>复制</button>
        </div>
      </div>
      <div className="ai-md" style={{ fontSize: 14.5 }}>
        <h2>基本面</h2>
        <p>{meta.ticker} 当前股价 <em>$221.34</em>,市值 <em>$3.41T</em>,市盈率 <em>36.4</em>。最近一季营业收入 <em>$94.93B</em>(同比 +6.1%),净利润 <em>$23.43B</em>(同比 +7.7%)。服务业务占比持续上升至 <strong>27%</strong>,iPhone 销售连续三季同比转正,大中华区营收同比 <strong>−2.4%</strong> 仍有压力。自由现金流 <em>$108.8B</em>(TTM)依然行业最高,资产负债结构稳健,净现金口径已转正约 <em>−$39.4B</em>。</p>
        <h2>技术面</h2>
        <p>日 K 处于上行通道,<strong>SMA50</strong> 在 $211.40 形成强支撑,<strong>SMA200</strong> $198.90 远在下方。RSI(14) <em>62.4</em> 偏强但未超买;MACD 金叉延续,柱状值连续 6 日为正。布林上轨 <em>$228.10</em> 距现价约 3.1%。近 5 日成交量较 20 日均量放大 <strong>+8%</strong>,价升量增的结构尚未出现背离。</p>
        <h2>风险</h2>
        <ul>
          <li>苹果智能 (Apple Intelligence) 商业化节奏慢于市场预期,可能压制估值。</li>
          <li>大中华区营收已连续三季下滑,关税与本土竞争双重压力。</li>
          <li>P/E 36.4 处于 5 年区间上沿 (中位 25.8),对盈利失速敏感。</li>
          <li>欧盟 DMA 处罚条款若按草案落地,潜在罚金接近上一季净利润 8%。</li>
        </ul>
      </div>
    </section>
  );
}

function VCRecordCard({ meta }) {
  return (
    <section className="mp-card">
      <div className="mp-card__head">
        <span className="mp-card__title"><span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-primary)" }}>edit_note</span>记一笔</span>
        <span className="mp-chip mp-chip--periwinkle" style={{ height: 22 }}>{meta.ticker}</span>
      </div>
      <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 14 }}>
        <div className="mp-seg" style={{ width: "100%", display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr" }}>
          <button className="is-active">买入</button>
          <button>卖出</button>
          <button>拆股</button>
          <button>分红</button>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10 }}>
          <Field2C label="数量" value="10" />
          <Field2C label="价格 USD" value={meta.price.toFixed(2)} />
          <Field2C label="日期" value="2026-05-12" />
        </div>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 14px", background: "var(--ns-surface-container-low)", borderRadius: 2 }}>
          <span style={{ fontSize: 11.5, color: "var(--ns-on-surface-variant)" }}>合计 · 含 $0.00 手续费</span>
          <span className="mono tnum grotesk" style={{ fontSize: 18, fontWeight: 700, color: "var(--ns-navy)" }}>${(meta.price * 10).toFixed(2)}</span>
        </div>
        <button className="mp-btn mp-btn--primary" style={{ height: 42, justifyContent: "center" }}>
          <span className="material-symbols-outlined">add</span>
          确认买入 · 提交
        </button>
      </div>
    </section>
  );
}
function Field2C({ label, value }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <span className="mp-eyebrow">{label}</span>
      <input defaultValue={value}
        style={{ border: "1px solid var(--ns-outline-variant)", borderRadius: 2, padding: "10px 12px", fontFamily: "var(--ns-font-mono)", fontSize: 14, fontWeight: 600, color: "var(--ns-navy)" }} />
    </label>
  );
}

function VCNewsCard() {
  const items = [
    { src: "Reuters", ago: "2h", title: "Apple 砍掉 Vision Pro 二代生产计划,转向更轻的智能眼镜",  tag: "产品" },
    { src: "Bloomberg", ago: "4h", title: "AAPL 与博通签订 5 年定制 AI 芯片协议,首批 2026 H2 交付", tag: "AI", hot: true },
    { src: "WSJ", ago: "7h", title: "iPhone 16 中国销量 Q1 同比 +12%,iOS 18.4 中文版功能落地", tag: "中国" },
    { src: "FT", ago: "1d", title: "欧盟 DMA 罚单初稿外泄,潜在罚金最高 18 亿欧元", tag: "监管" },
  ];
  return (
    <section className="mp-card">
      <div className="mp-card__head">
        <span className="mp-card__title"><span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-primary)" }}>newspaper</span>最新新闻</span>
        <a href="#" style={{ fontSize: 12, color: "var(--ns-primary)", textDecoration: "none" }}>查看全部 →</a>
      </div>
      <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {items.map((n, i) => (
          <li key={i} style={{ padding: "14px 18px", borderBottom: i < items.length - 1 ? "1px solid var(--ns-outline-variant)" : "0" }}>
            <div style={{ display: "flex", gap: 8, marginBottom: 4, alignItems: "center" }}>
              <span className="mp-eyebrow mp-eyebrow--primary">{n.src}</span>
              <span style={{ fontSize: 11, color: "var(--ns-slate-400)" }}>{n.ago} 前</span>
              {n.hot && <span className="mp-chip mp-chip--down" style={{ height: 18, fontSize: 9.5 }}>HOT</span>}
              <span className="mp-chip" style={{ height: 18, fontSize: 9.5 }}>{n.tag}</span>
            </div>
            <p style={{ margin: 0, fontSize: 13.5, lineHeight: 1.55, color: "var(--ns-navy)", fontWeight: 500 }}>{n.title}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}

function VCTradesCard({ ticker }) {
  const trades = [
    { date: "2026-04-22", action: "buy",  qty: 10, price: 168.40, notes: "回调加仓",   pl: null },
    { date: "2026-03-14", action: "buy",  qty: 20, price: 175.90, notes: "财报后",     pl: null },
    { date: "2025-12-08", action: "sell", qty: 15, price: 251.20, notes: "止盈减仓",   pl: +1182.50 },
    { date: "2025-09-30", action: "buy",  qty: 20, price: 218.55, notes: "Vision Pro 发布前", pl: null },
    { date: "2025-06-14", action: "buy",  qty: 15, price: 173.10, notes: "建仓",       pl: null },
  ];
  return (
    <section className="mp-card">
      <div className="mp-card__head">
        <span className="mp-card__title"><span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-primary)" }}>swap_horiz</span>{ticker} 交易记录</span>
        <span style={{ fontSize: 12, color: "var(--ns-on-surface-variant)" }}>
          共 5 笔 · 已实现盈亏 <span className="mono tnum" style={{ color: "var(--mp-up)", fontWeight: 600 }}>+$1,182.50</span>
        </span>
      </div>
      <table className="mp-table">
        <thead>
          <tr>
            <th>时间</th>
            <th>类型</th>
            <th style={{ textAlign: "right" }}>数量</th>
            <th style={{ textAlign: "right" }}>价格</th>
            <th style={{ textAlign: "right" }}>总额</th>
            <th style={{ textAlign: "right" }}>当时市值占比</th>
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
                <td className="num" style={{ fontWeight: 500 }}>{t.qty}</td>
                <td className="num">${t.price.toFixed(2)}</td>
                <td className="num" style={{ fontWeight: 600, color: "var(--ns-navy)" }}>${(t.qty * t.price).toFixed(2)}</td>
                <td className="num muted">{(12 + i*2).toFixed(1)}%</td>
                <td className={"num " + (t.pl == null ? "muted" : t.pl >= 0 ? "up" : "down")} style={{ fontWeight: 600 }}>
                  {t.pl == null ? "—" : (t.pl >= 0 ? "+" : "") + "$" + t.pl.toFixed(2)}
                </td>
                <td className="muted" style={{ fontSize: 12 }}>{t.notes}</td>
                <td style={{ textAlign: "right" }}>
                  <span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-slate-400)", cursor: "pointer" }}>edit</span>
                  <span style={{ width: 12, display: "inline-block" }} />
                  <span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-slate-400)", cursor: "pointer" }}>delete_outline</span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

window.VariantC = VariantC;
