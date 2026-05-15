// MarketPulse stock detail K-line chart.
// Uses TradingView lightweight-charts v4 loaded from jsDelivr.
// Reads ticker from <div id="chart-main" data-ticker="AAPL">.

(function () {
  const T = window.MP_CHART_THEME || {};
  const SIGNAL_STYLES = {
    ema_golden_cross:  { shape: "arrowUp",   color: T.signalGoldenCross    || "#16a34a", text: "金叉" },
    ema_death_cross:   { shape: "arrowDown", color: T.signalDeathCross     || "#dc2626", text: "死叉" },
    rsi_overbought:    { shape: "circle",    color: T.signalOverbought     || "#f59e0b", text: "超买" },
    rsi_oversold:      { shape: "circle",    color: T.signalOversold       || "#3b82f6", text: "超卖" },
    bollinger_upper:   { shape: "square",    color: T.signalBollingerUpper || "#a855f7", text: "上轨" },
    bollinger_lower:   { shape: "square",    color: T.signalBollingerLower || "#6366f1", text: "下轨" },
  };

  // Module-level state shared across initial render and lazy-load chunks.
  // Stored on `window` so subsequent fetches in the same session can prepend.
  function freshState() {
    return {
      ticker: null,
      bars: [],
      ema12: [], ema26: [],
      sma50: [], sma200: [],
      bb_upper: [], bb_lower: [],
      rsi: [],
      macd: { line: [], signal: [], histogram: [] },
      signal_markers: [],
      oldestLoaded: null,
      hasMoreHistory: true,
      loadingMore: false,
      // Series handles populated during renderCharts so prependChunk can setData.
      mainChart: null, candleSeries: null, volSeries: null,
      ema12Series: null, ema26Series: null,
      sma50Series: null, sma200Series: null,
      bbUpperSeries: null, bbLowerSeries: null,
      rsiChart: null, rsiSeries: null,
      macdChart: null, macdLineSeries: null, macdSignalSeries: null, macdHistSeries: null,
    };
  }

  // Convert null/undefined indicator points into lightweight-charts
  // whitespace items ({time: T}). The series array length now matches
  // the candle series, so logical indices align across all panes — this
  // is what allows syncPair to use logical-range subscription (see below)
  // and what prevents the prepend-cascade loop diagnosed on 2026-05-12.
  // Whitespace items reserve the bar's time-axis slot without drawing,
  // so the visual result is identical to filtering them out.
  function updateOhlcBar(bar) {
    const el = document.getElementById("chart-ohlc-bar");
    if (!el || !bar) return;
    const open  = bar.open  ?? bar.value;
    const high  = bar.high  ?? bar.value;
    const low   = bar.low   ?? bar.value;
    const close = bar.close ?? bar.value;
    if (open == null || close == null) return;
    el.querySelector('[data-ohlc="open"]').textContent  = open.toFixed(2);
    el.querySelector('[data-ohlc="high"]').textContent  = high.toFixed(2);
    el.querySelector('[data-ohlc="low"]').textContent   = low.toFixed(2);
    el.querySelector('[data-ohlc="close"]').textContent = close.toFixed(2);
    const change = close - open;
    const pct = open !== 0 ? (change / open) * 100 : 0;
    const changeEl = el.querySelector('[data-ohlc="change"]');
    const sign = change >= 0 ? "+" : "";
    changeEl.textContent = `${sign}${change.toFixed(2)} (${sign}${pct.toFixed(2)}%)`;
    changeEl.className = "font-semibold " + (change >= 0 ? "text-green-600" : "text-red-600");
  }

  function withWhitespace(series) {
    return series.map(p =>
      (p.value === null || p.value === undefined) ? { time: p.time } : p
    );
  }

  function renderCharts(payload, ticker) {
    const mainEl = document.getElementById("chart-main");
    mainEl.innerHTML = "";
    document.getElementById("chart-rsi").innerHTML = "";
    document.getElementById("chart-macd").innerHTML = "";

    // Reset state for the new render (new ticker, new period, or first load).
    const s = freshState();
    s.ticker = ticker;
    window.__mpChartState = s;

    if (!payload.bars || payload.bars.length === 0) {
      mainEl.innerHTML =
        '<p class="text-slate-500 text-sm py-8 text-center">暂无 K 线数据</p>';
      return;
    }

    // Snapshot the initial data into state so future prepends can extend it.
    s.bars = payload.bars.slice();
    s.ema12 = (payload.ema12 || []).slice();
    s.ema26 = (payload.ema26 || []).slice();
    s.sma50 = (payload.sma50 || []).slice();
    s.sma200 = (payload.sma200 || []).slice();
    s.bb_upper = (payload.bb_upper || []).slice();
    s.bb_lower = (payload.bb_lower || []).slice();
    s.rsi = (payload.rsi || []).slice();
    s.macd.line = (payload.macd?.line || []).slice();
    s.macd.signal = (payload.macd?.signal || []).slice();
    s.macd.histogram = (payload.macd?.histogram || []).slice();
    s.signal_markers = (payload.signal_markers || []).slice();
    s.oldestLoaded = s.bars[0].time;

    const commonOpts = {
      autoSize: true,
      layout: { background: { color: T.background || "#ffffff" }, textColor: T.textColor || "#334155" },
      grid: { vertLines: { color: T.gridLines || "#e2e8f0" }, horzLines: { color: T.gridLines || "#e2e8f0" } },
      timeScale: { borderColor: T.borderColor || "#cbd5e1", rightOffset: 12 },
      crosshair: { mode: 0 },
    };

    const lineOpts = (extras) => Object.assign({
      lineWidth: 1, priceLineVisible: false,
    }, extras);

    // === Main chart ===
    s.mainChart = LightweightCharts.createChart(mainEl, commonOpts);
    s.candleSeries = s.mainChart.addCandlestickSeries({
      upColor: T.upColor || "#16a34a", downColor: T.downColor || "#dc2626",
      borderVisible: false,
      wickUpColor: T.wickUpColor || "#16a34a", wickDownColor: T.wickDownColor || "#dc2626",
    });
    s.candleSeries.setData(s.bars);
    // Initial OHLC bar: show the latest bar.
    if (s.bars.length > 0) updateOhlcBar(s.bars[s.bars.length - 1]);

    function addLineIfData(data, opts, handleKey) {
      const dense = withWhitespace(data);
      if (dense.length === 0) return null;
      const line = s.mainChart.addLineSeries(opts);
      line.setData(dense);
      if (handleKey) s[handleKey] = line;
      return line;
    }
    s.ema12Series   = addLineIfData(s.ema12,   lineOpts({ color: T.ema12 || "#0ea5e9", title: "EMA12" }),    "ema12Series");
    s.ema26Series   = addLineIfData(s.ema26,   lineOpts({ color: T.ema26 || "#f59e0b", title: "EMA26" }),    "ema26Series");
    s.sma50Series   = addLineIfData(s.sma50,   lineOpts({ color: T.sma50 || "#8b5cf6", title: "SMA50" }),    "sma50Series");
    s.sma200Series  = addLineIfData(s.sma200,  lineOpts({ color: T.sma200 || "#64748b", title: "SMA200" }),  "sma200Series");
    s.bbUpperSeries = addLineIfData(s.bb_upper, lineOpts({ color: T.bbUpper || "#a855f7", lineStyle: 2, title: "BB上轨" }), "bbUpperSeries");
    s.bbLowerSeries = addLineIfData(s.bb_lower, lineOpts({ color: T.bbLower || "#a855f7", lineStyle: 2, title: "BB下轨" }), "bbLowerSeries");
    applyToggles();

    s.volSeries = s.mainChart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "",
      scaleMargins: { top: 0.85, bottom: 0 },
      lastValueVisible: false,
      priceLineVisible: false,
    });
    s.volSeries.setData(s.bars.map(b => ({
      time: b.time, value: b.volume,
      color: b.close >= b.open ? "rgba(14,138,95,0.35)" : "rgba(192,57,43,0.35)",
    })));

    if (s.signal_markers.length > 0) {
      const markers = s.signal_markers.map(m => {
        const style = SIGNAL_STYLES[m.type] || { shape: "circle", color: "#475569", text: m.type };
        return {
          time: m.time, position: "aboveBar",
          color: style.color, shape: style.shape, text: style.text,
        };
      });
      s.candleSeries.setMarkers(markers);
    }

    // === RSI pane ===
    const rsiData = withWhitespace(s.rsi);
    if (rsiData.length > 0) {
      s.rsiChart = LightweightCharts.createChart(
        document.getElementById("chart-rsi"),
        Object.assign({}, commonOpts, {
          rightPriceScale: { scaleMargins: { top: 0.15, bottom: 0.15 } },
        }),
      );
      s.rsiSeries = s.rsiChart.addLineSeries(lineOpts({ color: T.rsiLine || "#9333ea" }));
      s.rsiSeries.setData(rsiData);
      const ob = s.rsiChart.addLineSeries(lineOpts({
        color: T.rsiOverbought || "#fca5a5", lineStyle: 2, lastValueVisible: false,
      }));
      ob.setData(rsiData.map(p => ({ time: p.time, value: 70 })));
      const os = s.rsiChart.addLineSeries(lineOpts({
        color: T.rsiOversold || "#93c5fd", lineStyle: 2, lastValueVisible: false,
      }));
      os.setData(rsiData.map(p => ({ time: p.time, value: 30 })));
    }

    // === MACD pane ===
    const macdLine = withWhitespace(s.macd.line);
    if (macdLine.length > 0) {
      s.macdChart = LightweightCharts.createChart(
        document.getElementById("chart-macd"),
        Object.assign({}, commonOpts, {
          rightPriceScale: { scaleMargins: { top: 0.15, bottom: 0.15 } },
        }),
      );
      s.macdLineSeries = s.macdChart.addLineSeries(lineOpts({ color: T.macdLine || "#0ea5e9" }));
      s.macdLineSeries.setData(macdLine);
      s.macdSignalSeries = s.macdChart.addLineSeries(lineOpts({ color: T.macdSignal || "#f59e0b" }));
      s.macdSignalSeries.setData(withWhitespace(s.macd.signal));
      s.macdHistSeries = s.macdChart.addHistogramSeries({
        priceLineVisible: false, lastValueVisible: false,
      });
      s.macdHistSeries.setData(withWhitespace(s.macd.histogram).map(p => ({
        time: p.time, value: p.value,
        color: p.value >= 0 ? (T.macdHistPositive || "rgba(22,163,74,0.6)") : (T.macdHistNegative || "rgba(220,38,38,0.6)"),
      })));
    }

    // Sync time scale across panes (main ↔ RSI ↔ MACD) via LOGICAL range.
    // Time-range sync was fragile under setData(prepended): candle.setData
    // auto-shifts logical range and preserves time range, but line.setData
    // preserves logical and lets time range shift to older bars — the
    // mismatch made syncPair propagate a stale time range and snap main
    // back to the left edge, causing the cascade loop. Logical sync is
    // correct because withWhitespace() (above) keeps all panes' arrays
    // the same length, so logical index N means the same bar everywhere.
    const syncPair = (a, b) => {
      a.timeScale().subscribeVisibleLogicalRangeChange(r => {
        if (!r) return;
        b.timeScale().setVisibleLogicalRange(r);
      });
    };
    if (s.rsiChart)  { syncPair(s.mainChart, s.rsiChart);  syncPair(s.rsiChart, s.mainChart); }
    if (s.macdChart) { syncPair(s.mainChart, s.macdChart); syncPair(s.macdChart, s.mainChart); }

    // Show ALL initial bars — the user picked this period explicitly, so
    // give them what they picked. barsBefore=0 at first paint will fire
    // exactly one lazy-load prefetch (~180 more bars as a left-side
    // buffer); the cascade-prevention machinery (logical-range syncPair
    // + explicit setVisibleLogicalRange shift after prepend, both from
    // PR #20) guarantees this can't loop.
    //
    // The previous behavior anchored to "last 60 bars" — a leftover from
    // when 60D was the default period. With 1Y/5Y/All defaults, that
    // anchor showed only ~7 months of a 5-year selection, contradicting
    // the user's choice.
    s.mainChart.timeScale().setVisibleLogicalRange({
      from: 0,
      to: s.bars.length,
    });

    // Keep top OHLC bar synced with the crosshair position.
    // Off-chart → fall back to latest bar so the strip is never empty.
    s.mainChart.subscribeCrosshairMove(param => {
      const bar = param.seriesData && param.seriesData.get
        ? param.seriesData.get(s.candleSeries)
        : null;
      if (bar) {
        updateOhlcBar(bar);
      } else if (s.bars.length > 0) {
        updateOhlcBar(s.bars[s.bars.length - 1]);
      }
    });

    // Lazy-load trigger: TradingView's official barsInLogicalRange pattern.
    // barsBefore = count of bars in the dataset earlier than the visible
    // range. Unlike range.from, this is invariant to prepend — after a
    // chunk lands, barsBefore grows by chunk.bars.length, so the same
    // trigger expression stays stable across cascading loads. Threshold
    // 50 is TradingView's example value; gives the yfinance fetch (~1-3s
    // through Mihomo) headroom before a fast-scrolling user hits the edge.
    s.mainChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (!range) return;
      const info = s.candleSeries.barsInLogicalRange(range);
      if (info && info.barsBefore < 50) loadMoreHistory();
    });
  }

  function applyToggles() {
    const s = window.__mpChartState;
    if (!s) return;
    const bbOn  = document.getElementById("toggle-bb")?.checked  ?? true;
    const smaOn = document.getElementById("toggle-sma")?.checked ?? true;
    if (s.bbUpperSeries) s.bbUpperSeries.applyOptions({ visible: bbOn });
    if (s.bbLowerSeries) s.bbLowerSeries.applyOptions({ visible: bbOn });
    if (s.sma50Series)   s.sma50Series.applyOptions({ visible: smaOn });
    if (s.sma200Series)  s.sma200Series.applyOptions({ visible: smaOn });
  }

  async function loadMoreHistory() {
    const s = window.__mpChartState;
    if (!s || s.loadingMore || !s.hasMoreHistory || !s.ticker) return;
    s.loadingMore = true;
    const tickerAtRequest = s.ticker;
    // Capture visible range BEFORE the fetch — after prependChunk the
    // chart's logical indices shift and we need the pre-prepend values
    // to compute the shifted target range.
    const prevRange = s.mainChart.timeScale().getVisibleLogicalRange();
    console.debug(
      "mp-chart loadMore →",
      { ticker: s.ticker, oldestLoaded: s.oldestLoaded, prevRange },
    );
    showLoadingDot(true);
    try {
      const r = await fetch(
        `/stock/${s.ticker}/chart-data?before=${s.oldestLoaded}&count=180`,
      );
      if (!r.ok) return;  // log+retry on next scroll
      const chunk = await r.json();
      // State-object identity check catches period switches too — period
      // switch creates a new state object via freshState(), so an in-flight
      // request's captured `s` no longer matches the live state.
      if (window.__mpChartState !== s) return;
      if (s.ticker !== tickerAtRequest) return;  // ticker switch (paranoid)
      if (!chunk.bars || chunk.bars.length === 0) {
        s.hasMoreHistory = false;
        return;
      }
      prependChunk(chunk);
      s.oldestLoaded = chunk.bars[0].time;
      // Refresh OHLC bar so it stays consistent with state.bars after the
      // lazy-load (in case the user wasn't actively hovering during it).
      if (s.bars.length > 0) updateOhlcBar(s.bars[s.bars.length - 1]);
      // Explicit view shift: prependChunk's setData calls cause
      // lightweight-charts to refit (often jumping to the start of the
      // expanded dataset). Shifting the visible logical range right by
      // chunk.bars.length keeps the user anchored on the same time
      // window. Without this, barsBefore drops below threshold again
      // immediately after the load and we cascade.
      if (prevRange) {
        const newRange = {
          from: prevRange.from + chunk.bars.length,
          to: prevRange.to + chunk.bars.length,
        };
        s.mainChart.timeScale().setVisibleLogicalRange(newRange);
        console.debug(
          "mp-chart loadMore ✓",
          { chunkLen: chunk.bars.length, prevRange, newRange, barsTotal: s.bars.length },
        );
      }
    } catch (exc) {
      console.warn("lazy-load failed:", exc);
    } finally {
      s.loadingMore = false;
      showLoadingDot(false);
    }
  }

  function prependChunk(chunk) {
    const s = window.__mpChartState;
    // Bars: prepend, then setData on candle + volume series.
    s.bars = chunk.bars.concat(s.bars);
    s.candleSeries.setData(s.bars);
    s.volSeries.setData(s.bars.map(b => ({
      time: b.time, value: b.volume,
      color: b.close >= b.open ? "rgba(14,138,95,0.35)" : "rgba(192,57,43,0.35)",
    })));

    // Each line/indicator: extend the in-state array, then setData on its handle.
    const extendAndSet = (key, handle) => {
      if (!handle) return;
      const incoming = chunk[key] || [];
      s[key] = incoming.concat(s[key]);
      handle.setData(withWhitespace(s[key]));
    };
    extendAndSet("ema12",   s.ema12Series);
    extendAndSet("ema26",   s.ema26Series);
    extendAndSet("sma50",   s.sma50Series);
    extendAndSet("sma200",  s.sma200Series);
    extendAndSet("bb_upper", s.bbUpperSeries);
    extendAndSet("bb_lower", s.bbLowerSeries);
    extendAndSet("rsi",     s.rsiSeries);

    // MACD has a nested shape.
    if (s.macdLineSeries) {
      s.macd.line = (chunk.macd?.line || []).concat(s.macd.line);
      s.macdLineSeries.setData(withWhitespace(s.macd.line));
    }
    if (s.macdSignalSeries) {
      s.macd.signal = (chunk.macd?.signal || []).concat(s.macd.signal);
      s.macdSignalSeries.setData(withWhitespace(s.macd.signal));
    }
    if (s.macdHistSeries) {
      s.macd.histogram = (chunk.macd?.histogram || []).concat(s.macd.histogram);
      s.macdHistSeries.setData(withWhitespace(s.macd.histogram).map(p => ({
        time: p.time, value: p.value,
        color: p.value >= 0 ? (T.macdHistPositive || "rgba(22,163,74,0.6)") : (T.macdHistNegative || "rgba(220,38,38,0.6)"),
      })));
    }

    // Markers (rare — usually only on right side, but include for completeness)
    if (chunk.signal_markers && chunk.signal_markers.length > 0) {
      s.signal_markers = chunk.signal_markers.concat(s.signal_markers);
      const markers = s.signal_markers.map(m => {
        const style = SIGNAL_STYLES[m.type] || { shape: "circle", color: "#475569", text: m.type };
        return {
          time: m.time, position: "aboveBar",
          color: style.color, shape: style.shape, text: style.text,
        };
      });
      s.candleSeries.setMarkers(markers);
    }
  }

  function showLoadingDot(on) {
    const dot = document.getElementById("chart-loading-dot");
    if (!dot) return;
    dot.classList.toggle("opacity-0", !on);
    dot.classList.toggle("opacity-70", on);
  }

  async function load(ticker, period) {
    const r = await fetch(`/stock/${ticker}/chart-data?period=${period}`);
    if (!r.ok) {
      document.getElementById("chart-main").innerHTML =
        `<p class="text-red-600 text-sm py-8 text-center">加载失败: ${r.status}</p>`;
      return;
    }
    renderCharts(await r.json(), ticker);
  }

  const PERIOD_STORAGE_KEY = "mp.chartPeriod";
  const VALID_STORED_PERIODS = new Set(["60d", "6m", "ytd", "1y", "5y", "all"]);

  function readStoredPeriod() {
    try {
      const v = localStorage.getItem(PERIOD_STORAGE_KEY);
      return VALID_STORED_PERIODS.has(v) ? v : "1y";
    } catch {
      return "1y";
    }
  }

  function writeStoredPeriod(p) {
    try {
      if (VALID_STORED_PERIODS.has(p)) {
        localStorage.setItem(PERIOD_STORAGE_KEY, p);
      }
    } catch {
      // ignore — disabled or quota
    }
  }

  function applyActiveButton(period) {
    document.querySelectorAll("[data-period]").forEach(b => {
      const active = b.dataset.period === period;
      b.classList.toggle("bg-slate-900", active);
      b.classList.toggle("text-white", active);
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    const main = document.getElementById("chart-main");
    if (!main) return;
    const ticker = main.dataset.ticker;
    let currentPeriod = readStoredPeriod();
    applyActiveButton(currentPeriod);
    load(ticker, currentPeriod);

    document.querySelectorAll("[data-period]").forEach(btn => {
      btn.addEventListener("click", () => {
        currentPeriod = btn.dataset.period;
        writeStoredPeriod(currentPeriod);
        applyActiveButton(currentPeriod);
        load(ticker, currentPeriod);
      });
    });

    document.getElementById("toggle-bb")?.addEventListener("change", applyToggles);
    document.getElementById("toggle-sma")?.addEventListener("change", applyToggles);
  });
})();
