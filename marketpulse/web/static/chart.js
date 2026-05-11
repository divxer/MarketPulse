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

  function densify(series) {
    // Drop entries where value is null; lightweight-charts ignores them anyway,
    // but explicit filtering keeps the API surface small.
    return series.filter(p => p.value !== null && p.value !== undefined);
  }

  function renderCharts(payload) {
    // Clear any previous instances.
    document.getElementById("chart-main").innerHTML = "";
    document.getElementById("chart-rsi").innerHTML = "";
    document.getElementById("chart-macd").innerHTML = "";

    if (!payload.bars || payload.bars.length === 0) {
      document.getElementById("chart-main").innerHTML =
        '<p class="text-slate-500 text-sm py-8 text-center">暂无 K 线数据</p>';
      return;
    }

    const commonOpts = {
      layout: { background: { color: "#ffffff" }, textColor: "#334155" },
      grid: { vertLines: { color: "#e2e8f0" }, horzLines: { color: "#e2e8f0" } },
      timeScale: { borderColor: "#cbd5e1" },
    };

    // === Main chart: candles + EMA/SMA + Bollinger + volume ===
    const mainChart = LightweightCharts.createChart(
      document.getElementById("chart-main"),
      Object.assign({ height: 400 }, commonOpts),
    );
    const candleSeries = mainChart.addCandlestickSeries({
      upColor: "#16a34a", downColor: "#dc2626",
      borderVisible: false, wickUpColor: "#16a34a", wickDownColor: "#dc2626",
    });
    candleSeries.setData(payload.bars);

    function addLineIfData(series, opts) {
      const data = densify(series);
      if (data.length === 0) return;
      const line = mainChart.addLineSeries(opts);
      line.setData(data);
    }
    addLineIfData(payload.ema12,    { color: "#0ea5e9", lineWidth: 1, title: "EMA12" });
    addLineIfData(payload.ema26,    { color: "#f59e0b", lineWidth: 1, title: "EMA26" });
    addLineIfData(payload.sma50,    { color: "#8b5cf6", lineWidth: 1, title: "SMA50" });
    addLineIfData(payload.sma200,   { color: "#64748b", lineWidth: 1, title: "SMA200" });
    addLineIfData(payload.bb_upper, { color: "#a855f7", lineWidth: 1, lineStyle: 2, title: "BB上轨" });
    addLineIfData(payload.bb_lower, { color: "#a855f7", lineWidth: 1, lineStyle: 2, title: "BB下轨" });

    // Volume as histogram in a separate overlay pane.
    const volSeries = mainChart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "",
      scaleMargins: { top: 0.85, bottom: 0 },
    });
    volSeries.setData(payload.bars.map(b => ({
      time: b.time, value: b.volume,
      color: b.close >= b.open ? "rgba(22,163,74,0.4)" : "rgba(220,38,38,0.4)",
    })));

    // Signal markers on the candle series.
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
    if (rsiData.length > 0) {
      const rsiChart = LightweightCharts.createChart(
        document.getElementById("chart-rsi"),
        Object.assign({ height: 120 }, commonOpts),
      );
      const rsiSeries = rsiChart.addLineSeries({ color: "#9333ea", lineWidth: 1 });
      rsiSeries.setData(rsiData);
      const ob = rsiChart.addLineSeries({ color: "#fca5a5", lineWidth: 1, lineStyle: 2 });
      ob.setData(rsiData.map(p => ({ time: p.time, value: 70 })));
      const os = rsiChart.addLineSeries({ color: "#93c5fd", lineWidth: 1, lineStyle: 2 });
      os.setData(rsiData.map(p => ({ time: p.time, value: 30 })));
      mainChart.timeScale().subscribeVisibleTimeRangeChange(r => r && rsiChart.timeScale().setVisibleRange(r));
    }

    // === MACD pane ===
    const macdLine = densify(payload.macd.line);
    if (macdLine.length > 0) {
      const macdChart = LightweightCharts.createChart(
        document.getElementById("chart-macd"),
        Object.assign({ height: 120 }, commonOpts),
      );
      const line = macdChart.addLineSeries({ color: "#0ea5e9", lineWidth: 1 });
      line.setData(macdLine);
      const sig = macdChart.addLineSeries({ color: "#f59e0b", lineWidth: 1 });
      sig.setData(densify(payload.macd.signal));
      const hist = macdChart.addHistogramSeries();
      hist.setData(densify(payload.macd.histogram).map(p => ({
        time: p.time, value: p.value,
        color: p.value >= 0 ? "rgba(22,163,74,0.6)" : "rgba(220,38,38,0.6)",
      })));
      mainChart.timeScale().subscribeVisibleTimeRangeChange(r => r && macdChart.timeScale().setVisibleRange(r));
    }
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
  });
})();
