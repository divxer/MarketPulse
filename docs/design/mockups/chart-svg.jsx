/* global React */
// SVG chart primitives — candles, volume, line, area, RSI band, MACD histogram.
// All sized via props; consumers use ResponsiveContainer-style sizing.

const { useMemo } = React;

function CandleChart({
  candles, width, height,
  showVolume = true, showSMA = true, showBB = true,
  showAxis = true,
  theme = "light", // light | dark
  highlightIdx = null,
  className = "",
}) {
  const t = themeColors(theme);
  const padL = 0, padR = showAxis ? 60 : 8, padT = 12, padB = showVolume ? 64 : 28;
  const innerW = width - padL - padR;
  const innerH = height - padT - padB;

  const { ma50, ma200, bb } = useMemo(() => {
    const ma50 = MPData.sma(candles, 20);  // tighter ma for visibility on 90d
    const ma200 = MPData.sma(candles, 50);
    const bb = MPData.bollinger(candles, 20, 2);
    return { ma50, ma200, bb };
  }, [candles]);

  // Y range from candles + BB
  let lo = Infinity, hi = -Infinity;
  for (const c of candles) { if (c.low < lo) lo = c.low; if (c.high > hi) hi = c.high; }
  if (showBB) {
    for (const v of bb.upper) if (v != null && v > hi) hi = v;
    for (const v of bb.lower) if (v != null && v < lo) lo = v;
  }
  const range = hi - lo;
  lo -= range * 0.04; hi += range * 0.04;
  const y = (v) => padT + innerH - (v - lo) / (hi - lo) * innerH;

  // X positions
  const candleW = Math.max(2, (innerW / candles.length) * 0.72);
  const step = innerW / candles.length;
  const cx = (i) => padL + i * step + step / 2;

  // Volume scale
  let maxVol = 0;
  for (const c of candles) if (c.volume > maxVol) maxVol = c.volume;
  const volH = 40;
  const volBase = padT + innerH + 12;
  const volY = (v) => volBase + volH - (v / maxVol) * volH;

  // Grid lines (5 horizontal)
  const gridLines = [];
  for (let i = 0; i <= 4; i++) {
    const v = lo + (hi - lo) * (i / 4);
    gridLines.push({ y: y(v), label: v.toFixed(2) });
  }

  // Build BB area + lines
  const bbUpperPts = [], bbLowerPts = [], bbMidPts = [];
  for (let i = 0; i < candles.length; i++) {
    if (bb.upper[i] != null) bbUpperPts.push([cx(i), y(bb.upper[i])]);
    if (bb.lower[i] != null) bbLowerPts.push([cx(i), y(bb.lower[i])]);
    if (bb.ma[i] != null) bbMidPts.push([cx(i), y(bb.ma[i])]);
  }
  const bbArea = bbUpperPts.length
    ? `M ${bbUpperPts.map(p => p.join(",")).join(" L ")} L ${[...bbLowerPts].reverse().map(p => p.join(",")).join(" L ")} Z`
    : "";

  // SMA paths
  const ma50Pts = [], ma200Pts = [];
  for (let i = 0; i < candles.length; i++) {
    if (ma50[i] != null) ma50Pts.push([cx(i), y(ma50[i])]);
    if (ma200[i] != null) ma200Pts.push([cx(i), y(ma200[i])]);
  }
  const pathFrom = (pts) => pts.length ? "M " + pts.map(p => p.join(",")).join(" L ") : "";

  return (
    <svg width={width} height={height} className={className} style={{display:"block"}}>
      {/* background */}
      <rect x="0" y="0" width={width} height={height} fill={t.bg} />

      {/* grid */}
      {gridLines.map((g, i) => (
        <line key={i} x1={padL} x2={padL + innerW} y1={g.y} y2={g.y}
              stroke={t.grid} strokeWidth="1" strokeDasharray={i===0||i===4?"":"2 4"} />
      ))}

      {/* BB area */}
      {showBB && bbArea && (
        <path d={bbArea} fill={t.bbFill} stroke="none" />
      )}
      {showBB && (
        <>
          <path d={pathFrom(bbUpperPts)} stroke={t.bbStroke} strokeWidth="1" fill="none" />
          <path d={pathFrom(bbLowerPts)} stroke={t.bbStroke} strokeWidth="1" fill="none" />
          <path d={pathFrom(bbMidPts)} stroke={t.bbStroke} strokeWidth="1" strokeDasharray="3 3" fill="none" opacity="0.7"/>
        </>
      )}

      {/* SMA */}
      {showSMA && <path d={pathFrom(ma50Pts)} stroke={t.sma50} strokeWidth="1.4" fill="none" />}
      {showSMA && <path d={pathFrom(ma200Pts)} stroke={t.sma200} strokeWidth="1.4" fill="none" />}

      {/* Candles */}
      {candles.map((c, i) => {
        const up = c.close >= c.open;
        const fill = up ? t.up : t.down;
        const xC = cx(i);
        const oY = y(c.open), cY = y(c.close), hY = y(c.high), lY = y(c.low);
        const bodyTop = Math.min(oY, cY);
        const bodyH = Math.max(1, Math.abs(cY - oY));
        return (
          <g key={i}>
            <line x1={xC} x2={xC} y1={hY} y2={lY} stroke={fill} strokeWidth="1" />
            <rect x={xC - candleW/2} y={bodyTop} width={candleW} height={bodyH}
                  fill={fill} />
          </g>
        );
      })}

      {/* Volume bars */}
      {showVolume && candles.map((c, i) => {
        const up = c.close >= c.open;
        const xC = cx(i);
        const vY = volY(c.volume);
        return (
          <rect key={i} x={xC - candleW/2} y={vY} width={candleW}
                height={volBase + volH - vY}
                fill={up ? t.upSoft : t.downSoft} />
        );
      })}

      {/* Crosshair on highlight */}
      {highlightIdx != null && (() => {
        const c = candles[highlightIdx];
        if (!c) return null;
        const xC = cx(highlightIdx);
        return (
          <g pointerEvents="none">
            <line x1={xC} x2={xC} y1={padT} y2={padT + innerH} stroke={t.crosshair} strokeWidth="1" strokeDasharray="2 3"/>
            <line x1={padL} x2={padL + innerW} y1={y(c.close)} y2={y(c.close)} stroke={t.crosshair} strokeWidth="1" strokeDasharray="2 3"/>
          </g>
        );
      })()}

      {/* Right-axis labels */}
      {showAxis && gridLines.map((g, i) => (
        <text key={i} x={padL + innerW + 8} y={g.y + 3.5}
              fontFamily="var(--ns-font-mono)" fontSize="10.5" fill={t.axisInk}>
          {g.label}
        </text>
      ))}

      {/* Current price marker */}
      {showAxis && (() => {
        const last = candles[candles.length - 1];
        if (!last) return null;
        const yp = y(last.close);
        const up = last.close >= last.open;
        const bg = up ? t.up : t.down;
        return (
          <g>
            <line x1={padL} x2={padL + innerW} y1={yp} y2={yp} stroke={bg} strokeWidth="1" strokeDasharray="3 2" opacity="0.6"/>
            <rect x={padL + innerW + 2} y={yp - 9} width="56" height="18" fill={bg} />
            <text x={padL + innerW + 30} y={yp + 4}
                  fontFamily="var(--ns-font-mono)" fontSize="11" fontWeight="700"
                  fill="#fff" textAnchor="middle">
              {last.close.toFixed(2)}
            </text>
          </g>
        );
      })()}
    </svg>
  );
}

function RSIChart({ candles, width, height, theme = "light", highlightIdx = null }) {
  const t = themeColors(theme);
  const padL = 0, padR = 60, padT = 8, padB = 14;
  const innerW = width - padL - padR;
  const innerH = height - padT - padB;
  const rsi = useMemo(() => MPData.rsi(candles, 14), [candles]);
  const step = innerW / candles.length;
  const y = (v) => padT + innerH - (v / 100) * innerH;
  const cx = (i) => padL + i * step + step / 2;

  const pts = [];
  for (let i = 0; i < rsi.length; i++) {
    if (rsi[i] != null) pts.push([cx(i), y(rsi[i])]);
  }
  const path = pts.length ? "M " + pts.map(p => p.join(",")).join(" L ") : "";

  const last = rsi[rsi.length - 1];
  const lastColor = last == null ? t.ink : last > 70 ? t.down : last < 30 ? t.up : t.accent;

  return (
    <svg width={width} height={height} style={{display:"block"}}>
      <rect x="0" y="0" width={width} height={height} fill={t.bg} />
      {/* zones: overbought / oversold */}
      <rect x={padL} y={y(70)} width={innerW} height={y(70)-y(100) > 0 ? 0 : 0} fill="transparent" />
      <line x1={padL} x2={padL+innerW} y1={y(70)} y2={y(70)} stroke={t.down} strokeWidth="1" strokeDasharray="3 3" opacity="0.55"/>
      <line x1={padL} x2={padL+innerW} y1={y(30)} y2={y(30)} stroke={t.up} strokeWidth="1" strokeDasharray="3 3" opacity="0.55"/>
      <line x1={padL} x2={padL+innerW} y1={y(50)} y2={y(50)} stroke={t.grid} strokeWidth="1" strokeDasharray="2 4" opacity="0.7"/>
      {/* axis labels */}
      <text x={padL+innerW+8} y={y(70)+3} fontFamily="var(--ns-font-mono)" fontSize="10" fill={t.axisInk}>70</text>
      <text x={padL+innerW+8} y={y(50)+3} fontFamily="var(--ns-font-mono)" fontSize="10" fill={t.axisInk}>50</text>
      <text x={padL+innerW+8} y={y(30)+3} fontFamily="var(--ns-font-mono)" fontSize="10" fill={t.axisInk}>30</text>
      <path d={path} stroke={t.accent} strokeWidth="1.5" fill="none" />
      {last != null && (
        <>
          <circle cx={cx(rsi.length-1)} cy={y(last)} r="3" fill={lastColor} />
          <rect x={padL+innerW+2} y={y(last)-8} width="56" height="16" fill={lastColor} />
          <text x={padL+innerW+30} y={y(last)+4}
                fontFamily="var(--ns-font-mono)" fontSize="11" fontWeight="700"
                fill="#fff" textAnchor="middle">
            {last.toFixed(1)}
          </text>
        </>
      )}
      {highlightIdx != null && rsi[highlightIdx] != null && (
        <line x1={cx(highlightIdx)} x2={cx(highlightIdx)} y1={padT} y2={padT+innerH}
              stroke={t.crosshair} strokeWidth="1" strokeDasharray="2 3"/>
      )}
    </svg>
  );
}

function MACDChart({ candles, width, height, theme = "light", highlightIdx = null }) {
  const t = themeColors(theme);
  const padL = 0, padR = 60, padT = 8, padB = 14;
  const innerW = width - padL - padR;
  const innerH = height - padT - padB;
  const { line, signal, hist } = useMemo(() => MPData.macd(candles, 12, 26, 9), [candles]);
  const step = innerW / candles.length;
  const cx = (i) => padL + i * step + step / 2;

  let lo = Infinity, hi = -Infinity;
  for (const v of line) if (v != null) { if (v < lo) lo = v; if (v > hi) hi = v; }
  for (const v of signal) if (v != null) { if (v < lo) lo = v; if (v > hi) hi = v; }
  for (const v of hist) if (v != null) { if (v < lo) lo = v; if (v > hi) hi = v; }
  const span = Math.max(1, Math.abs(lo), Math.abs(hi));
  lo = -span; hi = span;
  const y = (v) => padT + innerH - (v - lo) / (hi - lo) * innerH;
  const zeroY = y(0);
  const barW = Math.max(2, step * 0.6);

  const linePts = [], sigPts = [];
  for (let i = 0; i < line.length; i++) {
    if (line[i] != null) linePts.push([cx(i), y(line[i])]);
    if (signal[i] != null) sigPts.push([cx(i), y(signal[i])]);
  }
  const pathFrom = (pts) => pts.length ? "M " + pts.map(p => p.join(",")).join(" L ") : "";

  const lastL = line[line.length - 1];
  return (
    <svg width={width} height={height} style={{display:"block"}}>
      <rect x="0" y="0" width={width} height={height} fill={t.bg} />
      <line x1={padL} x2={padL+innerW} y1={zeroY} y2={zeroY} stroke={t.grid} strokeWidth="1" />
      {/* histogram */}
      {hist.map((v, i) => {
        if (v == null) return null;
        const yV = y(v);
        const h = Math.abs(yV - zeroY);
        const positive = v >= 0;
        return (
          <rect key={i} x={cx(i) - barW/2} y={positive ? yV : zeroY}
                width={barW} height={h}
                fill={positive ? t.upSoft : t.downSoft}
                stroke={positive ? t.up : t.down} strokeWidth="0.5" />
        );
      })}
      <path d={pathFrom(linePts)} stroke={t.accent} strokeWidth="1.5" fill="none" />
      <path d={pathFrom(sigPts)} stroke={t.macdSignal} strokeWidth="1.5" fill="none" />
      {lastL != null && (
        <>
          <rect x={padL+innerW+2} y={y(lastL)-8} width="56" height="16" fill={t.accent} />
          <text x={padL+innerW+30} y={y(lastL)+4}
                fontFamily="var(--ns-font-mono)" fontSize="11" fontWeight="700"
                fill="#fff" textAnchor="middle">
            {lastL.toFixed(2)}
          </text>
        </>
      )}
      {highlightIdx != null && (
        <line x1={cx(highlightIdx)} x2={cx(highlightIdx)} y1={padT} y2={padT+innerH}
              stroke={t.crosshair} strokeWidth="1" strokeDasharray="2 3"/>
      )}
    </svg>
  );
}

// Mini sparkline for tables
function Sparkline({ values, width = 80, height = 24, color = "#0066cc", fill = "rgba(0,102,204,0.10)" }) {
  if (!values || values.length === 0) return null;
  let lo = Infinity, hi = -Infinity;
  for (const v of values) { if (v < lo) lo = v; if (v > hi) hi = v; }
  if (lo === hi) { lo -= 1; hi += 1; }
  const step = width / (values.length - 1 || 1);
  const y = (v) => height - 2 - ((v - lo) / (hi - lo)) * (height - 4);
  const pts = values.map((v, i) => [i * step, y(v)]);
  const path = "M " + pts.map(p => p.join(",")).join(" L ");
  const area = path + ` L ${width},${height} L 0,${height} Z`;
  return (
    <svg width={width} height={height} className="mp-spark">
      <path d={area} fill={fill} />
      <path d={path} stroke={color} strokeWidth="1.4" fill="none" />
    </svg>
  );
}

function AllocationBar({ rows, height = 28 }) {
  const total = rows.reduce((s, r) => s + r.value, 0);
  let x = 0;
  return (
    <svg width="100%" height={height} preserveAspectRatio="none" viewBox={`0 0 100 ${height}`} style={{display:"block"}}>
      {rows.map((r, i) => {
        const w = (r.value / total) * 100;
        const seg = <rect key={i} x={x} y="0" width={w} height={height} fill={r.color} />;
        x += w;
        return seg;
      })}
    </svg>
  );
}

function themeColors(theme) {
  if (theme === "dark") {
    return {
      bg: "transparent",
      grid: "#1f2c45",
      ink: "#e6ecf5",
      axisInk: "#93a3bf",
      up: "#2ecc71",
      down: "#ff5252",
      upSoft: "rgba(46,204,113,0.45)",
      downSoft: "rgba(255,82,82,0.45)",
      bbStroke: "rgba(255,180,74,0.5)",
      bbFill: "rgba(255,180,74,0.06)",
      sma50: "#5fc7ff",
      sma200: "#ffb44a",
      accent: "#5fc7ff",
      macdSignal: "#ffb44a",
      crosshair: "rgba(230,236,245,0.4)",
    };
  }
  return {
    bg: "transparent",
    grid: "#e2e8f0",
    ink: "#1a1a2e",
    axisInk: "#64748b",
    up: "#0e8a5f",
    down: "#c0392b",
    upSoft: "rgba(14,138,95,0.35)",
    downSoft: "rgba(192,57,43,0.35)",
    bbStroke: "rgba(0,102,204,0.4)",
    bbFill: "rgba(0,102,204,0.05)",
    sma50: "#0066cc",
    sma200: "#022448",
    accent: "#0066cc",
    macdSignal: "#c0570c",
    crosshair: "rgba(2,36,72,0.5)",
  };
}

Object.assign(window, { CandleChart, RSIChart, MACDChart, Sparkline, AllocationBar });
