// MarketPulse — chart color tokens (Variant A · NineScrolls light theme).
// Mirrors the SVG mockup palette (chart-svg.jsx > themeColors()).
// Production chart.js consumes these via window.MP_CHART_THEME.

window.MP_CHART_THEME = {
  // Background and grid
  background: "#ffffff",
  textColor: "#022448",            // ns-navy
  borderColor: "#c1c6d5",          // ns-outline-variant
  gridLines: "#efecff",            // ns-surface-container

  // Candle bodies
  upColor:        "#0e8a5f",       // mp-up
  downColor:      "#c0392b",       // mp-down
  upBorder:       "#0a6b48",       // mp-up-deep
  downBorder:     "#8b251c",       // mp-down-deep
  wickUpColor:    "#0e8a5f",
  wickDownColor:  "#c0392b",

  // Indicator lines on main chart
  ema12: "#0066cc",                // ns-primary
  ema26: "#f59e0b",                // amber (kept for contrast)
  sma50: "#8b5cf6",                // purple
  sma200: "#022448",               // navy (long-term trend gets the navy)
  bbUpper: "#a855f7",              // purple, dashed
  bbLower: "#a855f7",

  // RSI
  rsiLine: "#0066cc",              // ns-primary
  rsiOverbought: "#fca5a5",
  rsiOversold: "#93c5fd",

  // MACD
  macdLine: "#0066cc",             // ns-primary
  macdSignal: "#f59e0b",           // amber
  macdHistPositive: "rgba(14,138,95,0.6)",
  macdHistNegative: "rgba(192,57,43,0.6)",

  // Signal markers
  signalGoldenCross:   "#16a34a",  // green
  signalDeathCross:    "#dc2626",  // red
  signalOverbought:    "#f59e0b",  // amber
  signalOversold:      "#3b82f6",  // blue
  signalBollingerUpper: "#a855f7",
  signalBollingerLower: "#6366f1",
};
