// MarketPulse stock detail K-line chart.
// Uses TradingView lightweight-charts v4 loaded from jsDelivr.
// Reads ticker from <div id="chart-main" data-ticker="AAPL">.

(function () {
  const SIGNAL_STYLES = {
    ema_golden_cross:  { shape: "arrowUp",   color: "#16a34a", text: "金叉" },
    ema_death_cross:   { shape: "arrowDown", color: "#dc2626", text: "死叉" },
    rsi_overbought:    { shape: "circle",    color: "#f59e0b", text: "超买" },
    rsi_oversold:      { shape: "circle",    color: "#3b82f6", text: "超卖" },
    bollinger_upper:   { shape: "square",    color: "#a855f7", text: "上轨" },
    bollinger_lower:   { shape: "square",    color: "#6366f1", text: "下轨" },
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

  function densify(series) {
    return series.filter(p => p.value !== null && p.value !== undefined);
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
      layout: { background: { color: "#ffffff" }, textColor: "#334155" },
      grid: { vertLines: { color: "#e2e8f0" }, horzLines: { color: "#e2e8f0" } },
      timeScale: { borderColor: "#cbd5e1", rightOffset: 12 },
      crosshair: { mode: 0 },
    };

    const lineOpts = (extras) => Object.assign({
      lineWidth: 1, priceLineVisible: false,
    }, extras);

    // === Main chart ===
    s.mainChart = LightweightCharts.createChart(mainEl, commonOpts);
    s.candleSeries = s.mainChart.addCandlestickSeries({
      upColor: "#16a34a", downColor: "#dc2626",
      borderVisible: false, wickUpColor: "#16a34a", wickDownColor: "#dc2626",
    });
    s.candleSeries.setData(s.bars);

    function addLineIfData(data, opts, handleKey) {
      const dense = densify(data);
      if (dense.length === 0) return null;
      const line = s.mainChart.addLineSeries(opts);
      line.setData(dense);
      if (handleKey) s[handleKey] = line;
      return line;
    }
    s.ema12Series   = addLineIfData(s.ema12,   lineOpts({ color: "#0ea5e9", title: "EMA12" }),    "ema12Series");
    s.ema26Series   = addLineIfData(s.ema26,   lineOpts({ color: "#f59e0b", title: "EMA26" }),    "ema26Series");
    s.sma50Series   = addLineIfData(s.sma50,   lineOpts({ color: "#8b5cf6", title: "SMA50" }),    "sma50Series");
    s.sma200Series  = addLineIfData(s.sma200,  lineOpts({ color: "#64748b", title: "SMA200" }),   "sma200Series");
    s.bbUpperSeries = addLineIfData(s.bb_upper, lineOpts({ color: "#a855f7", lineStyle: 2, title: "BB上轨" }), "bbUpperSeries");
    s.bbLowerSeries = addLineIfData(s.bb_lower, lineOpts({ color: "#a855f7", lineStyle: 2, title: "BB下轨" }), "bbLowerSeries");
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
      color: b.close >= b.open ? "rgba(22,163,74,0.4)" : "rgba(220,38,38,0.4)",
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
    const rsiData = densify(s.rsi);
    if (rsiData.length > 0) {
      s.rsiChart = LightweightCharts.createChart(
        document.getElementById("chart-rsi"),
        Object.assign({}, commonOpts, {
          rightPriceScale: { scaleMargins: { top: 0.15, bottom: 0.15 } },
        }),
      );
      s.rsiSeries = s.rsiChart.addLineSeries(lineOpts({ color: "#9333ea" }));
      s.rsiSeries.setData(rsiData);
      const ob = s.rsiChart.addLineSeries(lineOpts({
        color: "#fca5a5", lineStyle: 2, lastValueVisible: false,
      }));
      ob.setData(rsiData.map(p => ({ time: p.time, value: 70 })));
      const os = s.rsiChart.addLineSeries(lineOpts({
        color: "#93c5fd", lineStyle: 2, lastValueVisible: false,
      }));
      os.setData(rsiData.map(p => ({ time: p.time, value: 30 })));
    }

    // === MACD pane ===
    const macdLine = densify(s.macd.line);
    if (macdLine.length > 0) {
      s.macdChart = LightweightCharts.createChart(
        document.getElementById("chart-macd"),
        Object.assign({}, commonOpts, {
          rightPriceScale: { scaleMargins: { top: 0.15, bottom: 0.15 } },
        }),
      );
      s.macdLineSeries = s.macdChart.addLineSeries(lineOpts({ color: "#0ea5e9" }));
      s.macdLineSeries.setData(macdLine);
      s.macdSignalSeries = s.macdChart.addLineSeries(lineOpts({ color: "#f59e0b" }));
      s.macdSignalSeries.setData(densify(s.macd.signal));
      s.macdHistSeries = s.macdChart.addHistogramSeries({
        priceLineVisible: false, lastValueVisible: false,
      });
      s.macdHistSeries.setData(densify(s.macd.histogram).map(p => ({
        time: p.time, value: p.value,
        color: p.value >= 0 ? "rgba(22,163,74,0.6)" : "rgba(220,38,38,0.6)",
      })));
    }

    // Sync time scale across panes (main ↔ RSI ↔ MACD).
    const syncPair = (a, b) => {
      a.timeScale().subscribeVisibleTimeRangeChange(r => {
        if (!r) return;
        b.timeScale().setVisibleRange(r);
      });
    };
    if (s.rsiChart)  { syncPair(s.mainChart, s.rsiChart);  syncPair(s.rsiChart, s.mainChart); }
    if (s.macdChart) { syncPair(s.mainChart, s.macdChart); syncPair(s.macdChart, s.mainChart); }

    s.mainChart.timeScale().fitContent();

    // Refit on browser window resize. autoSize:true resizes the canvas but
    // keeps the visible time range fixed — so when the user widens the
    // window, bars stay at their original pixel width and empty space
    // appears on both sides. Re-fitting on resize stretches the bars to
    // the new width.
    //
    // NOTE: we listen to `window.resize` instead of using ResizeObserver
    // on the chart container because lightweight-charts triggers minor
    // container-size changes internally (price-scale label width recalc
    // after setData). ResizeObserver would treat those as resizes and
    // call fitContent, which combined with the lazy-load trigger below
    // would form a feedback loop (lazy-load → setData → "resize" →
    // fitContent → range.from=0 → lazy-load again).
    if (window.__mpChartResizeHandler) {
      window.removeEventListener("resize", window.__mpChartResizeHandler);
    }
    const onWindowResize = () => {
      s.mainChart.timeScale().fitContent();
      if (s.rsiChart)  s.rsiChart.timeScale().fitContent();
      if (s.macdChart) s.macdChart.timeScale().fitContent();
    };
    window.addEventListener("resize", onWindowResize);
    window.__mpChartResizeHandler = onWindowResize;

    // Lazy-load trigger: re-fetch older history when scroll nears left edge.
    s.mainChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (!range) return;
      // range.from is the LEFTMOST visible logical index relative to bars[0].
      // Trigger fetch when leftmost visible bar is within 60 of bars[0] —
      // gives the yfinance fetch (~1-3s through Mihomo) headroom to land
      // before a fast-scrolling user hits the edge.
      if (range.from < 60) {
        loadMoreHistory();
      }
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
      // Hold loadingMore=true across 2 animation frames. setData() inside
      // prependChunk schedules a deferred range-change to refit to all data,
      // which fires AFTER this function's finally block under the default
      // microtask vs rAF ordering. If we cleared loadingMore immediately,
      // that deferred range-change would re-trigger loadMoreHistory in an
      // infinite loop. Two frames is enough for lightweight-charts to
      // settle, plus our explicit setVisibleLogicalRange in prependChunk
      // wins the final state.
      await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    } catch (exc) {
      console.warn("lazy-load failed:", exc);
    } finally {
      s.loadingMore = false;
      showLoadingDot(false);
    }
  }

  function prependChunk(chunk) {
    const s = window.__mpChartState;
    const prevRange = s.mainChart.timeScale().getVisibleLogicalRange();
    const shift = chunk.bars.length;
    // Bars: prepend, then setData on candle + volume series.
    s.bars = chunk.bars.concat(s.bars);
    s.candleSeries.setData(s.bars);
    s.volSeries.setData(s.bars.map(b => ({
      time: b.time, value: b.volume,
      color: b.close >= b.open ? "rgba(22,163,74,0.4)" : "rgba(220,38,38,0.4)",
    })));

    // Each line/indicator: extend the in-state array, then setData on its handle.
    const extendAndSet = (key, handle) => {
      if (!handle) return;
      const incoming = chunk[key] || [];
      s[key] = incoming.concat(s[key]);
      handle.setData(densify(s[key]));
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
      s.macdLineSeries.setData(densify(s.macd.line));
    }
    if (s.macdSignalSeries) {
      s.macd.signal = (chunk.macd?.signal || []).concat(s.macd.signal);
      s.macdSignalSeries.setData(densify(s.macd.signal));
    }
    if (s.macdHistSeries) {
      s.macd.histogram = (chunk.macd?.histogram || []).concat(s.macd.histogram);
      s.macdHistSeries.setData(densify(s.macd.histogram).map(p => ({
        time: p.time, value: p.value,
        color: p.value >= 0 ? "rgba(22,163,74,0.6)" : "rgba(220,38,38,0.6)",
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

    // Restore the user's visible window, shifted right by the number of new
    // bars. Without this, lightweight-charts re-fits the time scale to
    // include ALL data after setData (because the chart was in fitContent
    // mode), which sets range.from back to 0 and re-triggers the lazy-load
    // subscription. Forcing range.from > 60 breaks that feedback loop.
    if (prevRange) {
      s.mainChart.timeScale().setVisibleLogicalRange({
        from: prevRange.from + shift,
        to: prevRange.to + shift,
      });
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

  document.addEventListener("DOMContentLoaded", () => {
    const main = document.getElementById("chart-main");
    if (!main) return;
    const ticker = main.dataset.ticker;
    let currentPeriod = "60d";
    load(ticker, currentPeriod);

    document.querySelectorAll("[data-period]").forEach(btn => {
      btn.addEventListener("click", () => {
        currentPeriod = btn.dataset.period;
        document.querySelectorAll("[data-period]").forEach(b => {
          const active = b === btn;
          b.classList.toggle("bg-slate-900", active);
          b.classList.toggle("text-white", active);
        });
        load(ticker, currentPeriod);
      });
    });

    document.getElementById("toggle-bb")?.addEventListener("change", applyToggles);
    document.getElementById("toggle-sma")?.addEventListener("change", applyToggles);
  });
})();
