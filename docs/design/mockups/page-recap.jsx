/* global React */
// /recap — AI generated daily market commentary, editorial long-form layout.

function PageRecap() {
  return (
    <div style={{ width: 2560, minHeight: 1700, background: "var(--ns-background)", display: "flex", flexDirection: "column", overflowX: "hidden" }}>
      <Chrome activeKey="recaps" />

      {/* Hero */}
      <section style={{ padding: "40px 48px 24px", display: "flex", alignItems: "flex-end", justifyContent: "space-between", borderBottom: "1px solid var(--ns-outline-variant)" }}>
        <div>
          <span className="mp-eyebrow mp-eyebrow--primary">盘后复盘 · 美股</span>
          <h1 className="grotesk" style={{ fontSize: 64, fontWeight: 700, letterSpacing: "-0.04em", color: "var(--ns-navy)", margin: "8px 0 6px", lineHeight: 0.95 }}>
            2026 · 5 月 12 日
          </h1>
          <div className="mp-rule" style={{ marginTop: 14 }} />
          <p style={{ fontSize: 16, color: "var(--ns-on-surface-variant)", margin: "16px 0 0", maxWidth: 720, lineHeight: 1.6 }}>
            由 Claude 在收盘后基于您的自选股、当日持仓和大盘数据自动生成。客观、冷静、具体,提及具体的 ticker 和数字。
          </p>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 10 }}>
          <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--mp-up)", fontWeight: 600 }}>
              <span className="mp-pulse" /> 已生成 · 16:42 EDT
            </span>
            <span style={{ fontSize: 11.5, color: "var(--ns-on-surface-variant)", fontFamily: "var(--ns-font-mono)" }}>commentary-v3-zh-holdings · sonnet-4.5</span>
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            <button className="mp-btn mp-btn--ghost"><span className="material-symbols-outlined">refresh</span>重新生成</button>
            <button className="mp-btn mp-btn--ghost"><span className="material-symbols-outlined">share</span>分享</button>
            <button className="mp-btn mp-btn--ghost"><span className="material-symbols-outlined">push_pin</span>置顶</button>
            <button className="mp-btn mp-btn--navy"><span className="material-symbols-outlined">notifications_active</span>推送至订阅者</button>
          </div>
        </div>
      </section>

      {/* Top-of-page market snapshot strip */}
      <section style={{ padding: "20px 48px 24px", display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 16 }}>
        <SnapCard label="标普 500"     value="5,973.10" pct="+0.24" up={true} />
        <SnapCard label="纳指 100"     value="21,114.20" pct="+0.44" up={true} />
        <SnapCard label="道指"         value="43,118.45" pct="+0.51" up={true} />
        <SnapCard label="VIX 恐慌指数" value="14.18"    pct="−2.88" up={false} />
        <SnapCard label="美 10Y 收益率" value="4.18%"   pct="−3 bp" up={false} caret={false}/>
      </section>

      {/* Two-col body: long-form text + side rail */}
      <section style={{ padding: "0 48px 32px", display: "grid", gridTemplateColumns: "minmax(720px, 1.4fr) 720px", gap: 56 }}>

        {/* LONG-FORM */}
        <article style={{ maxWidth: 760 }}>
          <header style={{ marginBottom: 20 }}>
            <span className="mp-eyebrow mp-eyebrow--primary">编辑分析 · AI</span>
            <h2 className="grotesk" style={{ fontSize: 32, fontWeight: 700, color: "var(--ns-navy)", letterSpacing: "-0.03em", margin: "6px 0 0", lineHeight: 1.1 }}>
              半导体回吐温和,自选股盘中走强 —— 您的组合今日 +$482
            </h2>
          </header>

          <div style={{ fontFamily: "var(--ns-font-body)", fontSize: 17, lineHeight: 1.85, color: "var(--ns-on-surface)" }}>
            <p style={{ fontSize: 19, lineHeight: 1.7, color: "var(--ns-on-surface-variant)", fontWeight: 300, margin: "0 0 22px" }}>
              收盘三大指数维持窄幅红盘,资金小幅回补半导体之外的科技板块。您的自选股中 <strong style={{ color: "var(--ns-navy)" }}>AAPL</strong>、<strong style={{ color: "var(--ns-navy)" }}>TSLA</strong>、<strong style={{ color: "var(--ns-navy)" }}>AVGO</strong> 跑赢板块;NVDA 全天承压,日内最深回撤 2.6%。
            </p>

            <hr className="mp-hr" style={{ margin: "8px 0 24px" }} />

            <p style={{ margin: "0 0 16px" }}>
              <strong style={{ color: "var(--ns-navy)" }}>大盘层面</strong>,标普 500 收 <span className="mono" style={{ background: "var(--ns-surface-container-low)", padding: "0 6px", fontWeight: 600 }}>5,973.10</span> (+0.24%) ,纳斯达克 100 收 <span className="mono" style={{ background: "var(--ns-surface-container-low)", padding: "0 6px", fontWeight: 600 }}>21,114.20</span> (+0.44%) 创近两周新高。VIX 回落至 14.18,显示市场对短期波动的定价相当温和。10 年期美债收益率盘中下行约 3 bp 至 4.18%,与盘前 4.21% 相比,长端利率没有给风险资产施加额外压力。
            </p>

            <p style={{ margin: "0 0 16px" }}>
              板块结构上,半导体盘中遇阻回吐 —— 费城半导体指数 <span className="mono" style={{ color: "var(--mp-down)", fontWeight: 600 }}>−0.88%</span>,但收盘前修复约 30 个基点。NVDA 全天 −2.25% 是单只主要拖累;市场担忧 AI 资本开支节奏阶段性走弱,以及 H100 / H200 库存压力。同板块的 AVGO 因 与 AAPL 的 5 年定制芯片协议消息而 <span className="mono" style={{ color: "var(--mp-up)", fontWeight: 600 }}>+2.24%</span>,相对独立。
            </p>

            <p style={{ margin: "0 0 24px" }}>
              <strong style={{ color: "var(--ns-navy)" }}>您的持仓今日合计 +$482.18 (+0.81%)</strong>。前三贡献:AAPL +$120.50 (+1.10%, 受 AVGO 芯片协议利好与 Vision Pro 重定位消息共振)、TSLA +$609.60 (+3.83%, 单日跳涨,与 Robotaxi 路演公开排期临近有关)、META −$32.40 (−0.72%, 整体回调中相对温和)。最大单只拖累 NVDA −$251.20 (−2.25%),抵消了 AAPL + TSLA 一半的涨幅。
            </p>

            <h3 className="grotesk" style={{ fontSize: 22, fontWeight: 700, color: "var(--ns-navy)", letterSpacing: "-0.02em", margin: "8px 0 14px", display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ width: 4, height: 22, background: "var(--ns-primary)" }} />
              自选股动向
            </h3>

            <p style={{ margin: "0 0 16px" }}>
              <strong>TSLA +3.83%</strong> 是今日自选最强项。盘中先后吸纳两轮买盘:开盘 10 分钟内突破 5 月 5 日 263.10 的短期阻力,午盘回踩 261.30 之后再次走强收于 264.18。日内成交量 104M 较 20 日均量 +14%,价升量增结构维持。
            </p>

            <p style={{ margin: "0 0 16px" }}>
              <strong>AVGO +2.24%</strong> 因与 AAPL 签订 5 年定制 AI 芯片协议(首批 2026 H2 交付)迎来明显的相对强度提升。技术上突破 6 个月以来的整理区间上沿 175.40,如能在 175–178 区间获得支撑,中线观点偏多。
            </p>

            <p style={{ margin: "0 0 16px" }}>
              <strong>NVDA −2.25%</strong>,日内主导消息为 Wedbush 下调短期催化剂权重;基本面无重大变动。RSI 从 67 回落至 58,MACD 柱仍为正但开始收敛,需关注是否在 132–134 形成短期支撑,否则有进一步回踩 50 日线 (128.40) 的可能。
            </p>

            <h3 className="grotesk" style={{ fontSize: 22, fontWeight: 700, color: "var(--ns-navy)", letterSpacing: "-0.02em", margin: "16px 0 14px", display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ width: 4, height: 22, background: "var(--ns-primary)" }} />
              展望明日
            </h3>

            <p style={{ margin: "0 0 16px" }}>
              <strong>关键事件</strong>:CPI 数据于明日 08:30 EDT 公布,共识 +2.4% (核心 +2.9%)。若高于预期,可能压制估值敏感的科技板块短线表现;低于预期则利好。
            </p>

            <p style={{ margin: "0 0 16px" }}>
              <strong>财报</strong>:盘后 CSCO、PANW;盘前无重要披露。
            </p>

            <p style={{ margin: "0 0 12px" }}>
              <strong>组合层面</strong>,半导体合计权重 22.4%(NVDA + AVGO + TSM),若 CPI 超预期,留意是否触发您 5 月 6 日设定的"NVDA 跌破 130 提醒";同时 AAPL 在 222 处仍有抛压,短线突破需要量能配合。
            </p>

            <p style={{ fontSize: 13, color: "var(--ns-on-surface-variant)", borderTop: "1px solid var(--ns-outline-variant)", paddingTop: 18, marginTop: 28, lineHeight: 1.6 }}>
              <em style={{ background: "rgba(192,57,43,0.08)", padding: "2px 8px", borderRadius: 2, color: "var(--mp-down-deep)", fontStyle: "normal", fontWeight: 600 }}>免责声明</em>
              {" "}本文为模型基于历史与公开数据的描述性总结,不构成任何买入、卖出或持有的投资建议。所有数字以官方收盘报价为准。
            </p>
          </div>
        </article>

        {/* RIGHT SIDE RAIL */}
        <aside style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <PortfolioTodayCard />
          <WatchlistPerfCard />
          <KeyEventsCard />
          <PrevRecapsCard />
        </aside>

      </section>
    </div>
  );
}

function SnapCard({ label, value, pct, up, caret = true }) {
  const cls = up ? "up" : "down";
  return (
    <div className="mp-card" style={{ padding: "16px 18px" }}>
      <span className="mp-eyebrow mp-eyebrow--primary">{label}</span>
      <div className="mono tnum" style={{ fontSize: 26, fontWeight: 600, color: "var(--ns-navy)", letterSpacing: "-0.01em", marginTop: 4 }}>{value}</div>
      <div className={`mono tnum ${cls}`} style={{ fontSize: 13, fontWeight: 600, marginTop: 2, display: "flex", alignItems: "center", gap: 4 }}>
        {caret && <span className="material-symbols-outlined" style={{ fontSize: 14 }}>{up ? "trending_up" : "trending_down"}</span>}
        {pct}{caret ? "%" : ""}
      </div>
    </div>
  );
}

function PortfolioTodayCard() {
  const items = [
    { ticker: "TSLA", pl: +609.60, pct: +3.83 },
    { ticker: "AAPL", pl: +120.50, pct: +1.10 },
    { ticker: "AVGO", pl: +143.10, pct: +2.24 },
    { ticker: "MSFT", pl: +233.20, pct: +1.33 },
    { ticker: "GOOGL",pl: +31.00,  pct: +0.31 },
    { ticker: "TSM",  pl: +55.50,  pct: +1.12 },
    { ticker: "AMZN", pl: +33.20,  pct: +0.51 },
    { ticker: "SPY",  pl: +57.20,  pct: +0.24 },
    { ticker: "META", pl: -32.40,  pct: -0.72 },
    { ticker: "NVDA", pl: -251.20, pct: -2.25 },
  ];
  return (
    <section className="mp-card">
      <div className="mp-card__head">
        <span className="mp-card__title"><span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-primary)" }}>account_balance_wallet</span>组合今日</span>
        <span className="mono tnum up" style={{ fontWeight: 700, fontSize: 14 }}>+$482.18 · +0.81%</span>
      </div>
      <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {items.map((it, i) => {
          const up = it.pl >= 0;
          return (
            <li key={it.ticker} style={{ display: "grid", gridTemplateColumns: "70px 1fr 100px 70px", gap: 12, alignItems: "center", padding: "10px 18px", borderBottom: i < items.length - 1 ? "1px solid var(--ns-outline-variant)" : "0" }}>
              <span className="grotesk" style={{ fontWeight: 700, fontSize: 13, color: "var(--ns-navy)" }}>{it.ticker}</span>
              <div style={{ height: 4, background: "var(--ns-surface-container)", borderRadius: 2, position: "relative", overflow: "hidden" }}>
                <div style={{ position: "absolute", left: up ? "50%" : `${50 - Math.abs(it.pct) / 4 * 100}%`, top: 0, bottom: 0, width: Math.min(50, Math.abs(it.pct) / 4 * 100) + "%", background: up ? "var(--mp-up)" : "var(--mp-down)" }} />
                <div style={{ position: "absolute", left: "50%", top: -2, bottom: -2, width: 1, background: "var(--ns-outline)" }} />
              </div>
              <span className={"mono tnum " + (up ? "up" : "down")} style={{ fontSize: 13, fontWeight: 600, textAlign: "right" }}>{up ? "+" : ""}${it.pl.toFixed(2)}</span>
              <span className={"mono tnum " + (up ? "up" : "down")} style={{ fontSize: 12, textAlign: "right" }}>{up ? "+" : ""}{it.pct.toFixed(2)}%</span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function WatchlistPerfCard() {
  const items = MPShell.buildWatchlist();
  return (
    <section className="mp-card">
      <div className="mp-card__head">
        <span className="mp-card__title"><span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-primary)" }}>visibility</span>自选股 · 当日</span>
        <span style={{ fontSize: 11.5, color: "var(--ns-on-surface-variant)" }}>10 个标的 · 7 涨 3 跌</span>
      </div>
      <table className="mp-table">
        <thead>
          <tr>
            <th>代码</th>
            <th style={{ textAlign: "right" }}>现价</th>
            <th style={{ textAlign: "right" }}>涨跌 %</th>
            <th style={{ width: 70 }}>30D</th>
          </tr>
        </thead>
        <tbody>
          {items.map(it => {
            const up = it.chg >= 0;
            return (
              <tr key={it.ticker}>
                <td><span className="grotesk" style={{ fontWeight: 700, color: "var(--ns-navy)" }}>{it.ticker}</span></td>
                <td className="num mono tnum" style={{ fontWeight: 600, color: "var(--ns-navy)" }}>{it.price.toFixed(2)}</td>
                <td className={"num mono tnum " + (up ? "up" : "down")} style={{ fontWeight: 600 }}>{up ? "+" : ""}{it.pct.toFixed(2)}%</td>
                <td>
                  <Sparkline values={it.sparkValues} width={62} height={20}
                    color={up ? "#0e8a5f" : "#c0392b"}
                    fill={up ? "rgba(14,138,95,0.10)" : "rgba(192,57,43,0.10)"} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

function KeyEventsCard() {
  const events = [
    { time: "明日 08:30", title: "CPI · 4月", detail: "共识 +2.4% / 核心 +2.9%", priority: "high" },
    { time: "明日 盘后",  title: "CSCO 财报", detail: "EPS 预期 0.87 · 营收 13.8B", priority: "med" },
    { time: "明日 盘后",  title: "PANW 财报", detail: "EPS 预期 1.51 · 营收 2.16B", priority: "med" },
    { time: "本周五",    title: "零售销售 · 4月", detail: "共识 +0.3% MoM", priority: "low" },
    { time: "下周三",    title: "FOMC 会议纪要", detail: "5月14日 决议纪要发布", priority: "med" },
  ];
  return (
    <section className="mp-card">
      <div className="mp-card__head">
        <span className="mp-card__title"><span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-primary)" }}>event</span>关键事件</span>
      </div>
      <ul style={{ listStyle: "none", margin: 0, padding: "8px 0" }}>
        {events.map((e, i) => (
          <li key={i} style={{ padding: "10px 18px", display: "grid", gridTemplateColumns: "92px 1fr", gap: 12, borderBottom: i < events.length - 1 ? "1px solid var(--ns-outline-variant)" : "0" }}>
            <div>
              <span style={{ fontSize: 11, color: "var(--ns-on-surface-variant)", fontFamily: "var(--ns-font-mono)" }}>{e.time}</span>
              <div style={{ marginTop: 4 }}>
                <span style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: e.priority === "high" ? "var(--mp-down)" : e.priority === "med" ? "var(--ns-warning)" : "var(--ns-outline-variant)" }} />
              </div>
            </div>
            <div>
              <div className="grotesk" style={{ fontWeight: 700, fontSize: 13, color: "var(--ns-navy)" }}>{e.title}</div>
              <div style={{ fontSize: 11.5, color: "var(--ns-on-surface-variant)", marginTop: 2 }}>{e.detail}</div>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function PrevRecapsCard() {
  const days = [
    { d: "2026-05-09 · 五",  pl: "+0.41%", up: true,  title: "缩量上行,半导体强势主导" },
    { d: "2026-05-08 · 四",  pl: "-0.18%", up: false, title: "美债收益率反弹,科技回调" },
    { d: "2026-05-07 · 三",  pl: "+1.14%", up: true,  title: "TSLA Robotaxi 路演开启" },
    { d: "2026-05-06 · 二",  pl: "-0.62%", up: false, title: "CPI 前夜回调,VIX 上行" },
    { d: "2026-05-05 · 一",  pl: "+0.82%", up: true,  title: "AI 资本开支预期重启" },
  ];
  return (
    <section className="mp-card">
      <div className="mp-card__head">
        <span className="mp-card__title"><span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ns-primary)" }}>history</span>近 5 日复盘</span>
        <a href="#" style={{ fontSize: 12, color: "var(--ns-primary)", textDecoration: "none" }}>查看全部 →</a>
      </div>
      <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {days.map((d, i) => (
          <li key={i} style={{ padding: "12px 18px", display: "grid", gridTemplateColumns: "1fr auto", gap: 12, alignItems: "center", borderBottom: i < days.length - 1 ? "1px solid var(--ns-outline-variant)" : "0", cursor: "pointer" }}>
            <div style={{ minWidth: 0 }}>
              <div className="mono" style={{ fontSize: 11, color: "var(--ns-on-surface-variant)", letterSpacing: "0.02em" }}>{d.d}</div>
              <div className="grotesk" style={{ fontWeight: 600, fontSize: 13, color: "var(--ns-navy)", marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.title}</div>
            </div>
            <span className={"mono tnum " + (d.up ? "up" : "down")} style={{ fontWeight: 700, fontSize: 13 }}>{d.pl}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

window.PageRecap = PageRecap;
