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

  // Holds series handles so toggle checkboxes can flip visibility without redraw.
  const seriesRefs = { bb_upper: null, bb_lower: null, sma50: null, sma200: null };

  function densify(series) {
    return series.filter(p => p.value !== null && p.value !== undefined);
  }

  function renderCharts(payload) {
    const mainEl = document.getElementById("chart-main");
    mainEl.innerHTML = "";
    document.getElementById("chart-rsi").innerHTML = "";
    document.getElementById("chart-macd").innerHTML = "";
    seriesRefs.bb_upper = seriesRefs.bb_lower = seriesRefs.sma50 = seriesRefs.sma200 = null;

    if (!payload.bars || payload.bars.length === 0) {
      mainEl.innerHTML =
        '<p class="text-slate-500 text-sm py-8 text-center">暂无 K 线数据</p>';
      return;
    }

    const commonOpts = {
      autoSize: true,  // resize with the container (mobile-friendly)
      layout: { background: { color: "#ffffff" }, textColor: "#334155" },
      grid: { vertLines: { color: "#e2e8f0" }, horzLines: { color: "#e2e8f0" } },
      // rightOffset reserves N empty bars on the right so the price-scale labels
      // ("BB上轨 10.71" etc.) don't overlap the latest candles.
      timeScale: { borderColor: "#cbd5e1", rightOffset: 12 },
      crosshair: { mode: 0 },  // magnet mode — snaps to bars
    };

    // Per-series default options: suppress dashed horizontal "last price" guide
    // lines so overlays don't add additional clutter on the chart body.
    const lineOpts = (extras) => Object.assign({
      lineWidth: 1, priceLineVisible: false,
    }, extras);

    // === Main chart: candles + EMA/SMA + Bollinger + volume ===
    const mainChart = LightweightCharts.createChart(mainEl, commonOpts);
    const candleSeries = mainChart.addCandlestickSeries({
      upColor: "#16a34a", downColor: "#dc2626",
      borderVisible: false, wickUpColor: "#16a34a", wickDownColor: "#dc2626",
    });
    candleSeries.setData(payload.bars);

    function addLineIfData(series, opts) {
      const data = densify(series);
      if (data.length === 0) return null;
      const line = mainChart.addLineSeries(opts);
      line.setData(data);
      return line;
    }
    addLineIfData(payload.ema12,   lineOpts({ color: "#0ea5e9", title: "EMA12" }));
    addLineIfData(payload.ema26,   lineOpts({ color: "#f59e0b", title: "EMA26" }));
    seriesRefs.sma50    = addLineIfData(payload.sma50,    lineOpts({ color: "#8b5cf6", title: "SMA50" }));
    seriesRefs.sma200   = addLineIfData(payload.sma200,   lineOpts({ color: "#64748b", title: "SMA200" }));
    seriesRefs.bb_upper = addLineIfData(payload.bb_upper, lineOpts({ color: "#a855f7", lineStyle: 2, title: "BB上轨" }));
    seriesRefs.bb_lower = addLineIfData(payload.bb_lower, lineOpts({ color: "#a855f7", lineStyle: 2, title: "BB下轨" }));
    // Apply current toggle state (in case user toggled off before reload)
    applyToggles();

    // Volume as histogram in an overlay pane at the bottom of the main chart.
    // lastValueVisible=false hides the giant "13.44M" floating label that
    // was the worst offender obscuring the latest candles.
    const volSeries = mainChart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "",
      scaleMargins: { top: 0.85, bottom: 0 },
      lastValueVisible: false,
      priceLineVisible: false,
    });
    volSeries.setData(payload.bars.map(b => ({
      time: b.time, value: b.volume,
      color: b.close >= b.open ? "rgba(22,163,74,0.4)" : "rgba(220,38,38,0.4)",
    })));

    if (payload.signal_markers && payload.signal_markers.length > 0) {
      const markers = payload.signal_markers.map(m => {
        const style = SIGNAL_STYLES[m.type] || { shape: "circle", color: "#475569", text: m.type };
        return {
          time: m.time, position: "aboveBar",
          color: style.color, shape: style.shape, text: style.text,
        };
      });
      candleSeries.setMarkers(markers);
    }

    // === RSI pane ===
    const rsiData = densify(payload.rsi);
    let rsiChart = null;
    if (rsiData.length > 0) {
      rsiChart = LightweightCharts.createChart(
        document.getElementById("chart-rsi"),
        Object.assign({}, commonOpts, {
          // Extra top margin so the "75.00" label isn't clipped by the pane edge.
          rightPriceScale: { scaleMargins: { top: 0.15, bottom: 0.15 } },
        }),
      );
      const rsiSeries = rsiChart.addLineSeries(lineOpts({ color: "#9333ea" }));
      rsiSeries.setData(rsiData);
      // Reference lines: keep the floating label off (70/30 are static).
      const ob = rsiChart.addLineSeries(lineOpts({
        color: "#fca5a5", lineStyle: 2, lastValueVisible: false,
      }));
      ob.setData(rsiData.map(p => ({ time: p.time, value: 70 })));
      const os = rsiChart.addLineSeries(lineOpts({
        color: "#93c5fd", lineStyle: 2, lastValueVisible: false,
      }));
      os.setData(rsiData.map(p => ({ time: p.time, value: 30 })));
    }

    // === MACD pane ===
    const macdLine = densify(payload.macd.line);
    let macdChart = null;
    if (macdLine.length > 0) {
      macdChart = LightweightCharts.createChart(
        document.getElementById("chart-macd"),
        Object.assign({}, commonOpts, {
          rightPriceScale: { scaleMargins: { top: 0.15, bottom: 0.15 } },
        }),
      );
      const line = macdChart.addLineSeries(lineOpts({ color: "#0ea5e9" }));
      line.setData(macdLine);
      const sig = macdChart.addLineSeries(lineOpts({ color: "#f59e0b" }));
      sig.setData(densify(payload.macd.signal));
      const hist = macdChart.addHistogramSeries({
        priceLineVisible: false, lastValueVisible: false,
      });
      hist.setData(densify(payload.macd.histogram).map(p => ({
        time: p.time, value: p.value,
        color: p.value >= 0 ? "rgba(22,163,74,0.6)" : "rgba(220,38,38,0.6)",
      })));
    }

    // Sync time scale: main -> RSI/MACD. Also reverse-sync RSI/MACD scrolls back
    // to main so the user can pan from any pane.
    const syncPair = (a, b) => {
      a.timeScale().subscribeVisibleTimeRangeChange(r => {
        if (!r) return;
        b.timeScale().setVisibleRange(r);
      });
    };
    if (rsiChart)  { syncPair(mainChart, rsiChart);  syncPair(rsiChart, mainChart); }
    if (macdChart) { syncPair(mainChart, macdChart); syncPair(macdChart, mainChart); }

    mainChart.timeScale().fitContent();
  }

  function applyToggles() {
    const bbOn  = document.getElementById("toggle-bb")?.checked  ?? true;
    const smaOn = document.getElementById("toggle-sma")?.checked ?? true;
    if (seriesRefs.bb_upper) seriesRefs.bb_upper.applyOptions({ visible: bbOn });
    if (seriesRefs.bb_lower) seriesRefs.bb_lower.applyOptions({ visible: bbOn });
    if (seriesRefs.sma50)    seriesRefs.sma50.applyOptions({ visible: smaOn });
    if (seriesRefs.sma200)   seriesRefs.sma200.applyOptions({ visible: smaOn });
  }

  async function load(ticker, period) {
    const r = await fetch(`/stock/${ticker}/chart-data?period=${period}`);
    if (!r.ok) {
      document.getElementById("chart-main").innerHTML =
        `<p class="text-red-600 text-sm py-8 text-center">加载失败: ${r.status}</p>`;
      return;
    }
    renderCharts(await r.json());
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
