/* global React */
// Seeded deterministic random walks → realistic-looking OHLC candles, RSI, MACD.

const PRNG = (seed) => {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 0xffffffff;
  };
};

// Generate N daily candles starting from a base price.
function generateCandles({ seed = 42, count = 90, basePrice = 220, drift = 0.0014, vol = 0.018 } = {}) {
  const rand = PRNG(seed);
  const candles = [];
  let close = basePrice;
  for (let i = 0; i < count; i++) {
    const open = close;
    // Random walk on close
    const r = (rand() - 0.5) * 2 * vol + drift;
    let c = open * (1 + r);
    // High/low range
    const range = Math.abs(rand() - 0.5) * 2 * vol * open + 0.5;
    const high = Math.max(open, c) + Math.abs(rand()) * range;
    const low = Math.min(open, c) - Math.abs(rand()) * range;
    const volume = Math.round((40_000_000 + (rand() - 0.5) * 40_000_000) * (Math.abs(r) * 30 + 0.6));
    candles.push({
      i,
      open: round2(open),
      high: round2(high),
      low: round2(low),
      close: round2(c),
      volume,
    });
    close = c;
  }
  return candles;
}

function round2(x) { return Math.round(x * 100) / 100; }

// Simple Moving Average
function sma(candles, period, field = "close") {
  const out = new Array(candles.length).fill(null);
  let sum = 0;
  for (let i = 0; i < candles.length; i++) {
    sum += candles[i][field];
    if (i >= period) sum -= candles[i - period][field];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

// Bollinger Bands (period 20, k=2)
function bollinger(candles, period = 20, k = 2) {
  const ma = sma(candles, period);
  const upper = new Array(candles.length).fill(null);
  const lower = new Array(candles.length).fill(null);
  for (let i = period - 1; i < candles.length; i++) {
    let sumSq = 0;
    for (let j = i - period + 1; j <= i; j++) {
      sumSq += Math.pow(candles[j].close - ma[i], 2);
    }
    const stdev = Math.sqrt(sumSq / period);
    upper[i] = ma[i] + k * stdev;
    lower[i] = ma[i] - k * stdev;
  }
  return { ma, upper, lower };
}

// RSI(14)
function rsi(candles, period = 14) {
  const out = new Array(candles.length).fill(null);
  let avgGain = 0, avgLoss = 0;
  for (let i = 1; i < candles.length; i++) {
    const diff = candles[i].close - candles[i - 1].close;
    const gain = Math.max(0, diff);
    const loss = Math.max(0, -diff);
    if (i <= period) {
      avgGain += gain / period;
      avgLoss += loss / period;
      if (i === period) {
        const rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
        out[i] = 100 - 100 / (1 + rs);
      }
    } else {
      avgGain = (avgGain * (period - 1) + gain) / period;
      avgLoss = (avgLoss * (period - 1) + loss) / period;
      const rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
      out[i] = 100 - 100 / (1 + rs);
    }
  }
  return out;
}

// MACD(12,26,9)
function ema(values, period) {
  const out = new Array(values.length).fill(null);
  const k = 2 / (period + 1);
  let prev = null;
  let seedSum = 0, seedCount = 0;
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (v == null) continue;
    if (prev == null) {
      seedSum += v; seedCount++;
      if (seedCount === period) {
        prev = seedSum / period;
        out[i] = prev;
      }
    } else {
      prev = v * k + prev * (1 - k);
      out[i] = prev;
    }
  }
  return out;
}
function macd(candles, fast = 12, slow = 26, signalP = 9) {
  const closes = candles.map(c => c.close);
  const emaF = ema(closes, fast);
  const emaS = ema(closes, slow);
  const line = closes.map((_, i) => (emaF[i] != null && emaS[i] != null) ? emaF[i] - emaS[i] : null);
  const signal = ema(line.map(v => v ?? 0), signalP);
  const hist = line.map((v, i) => (v != null && signal[i] != null) ? v - signal[i] : null);
  return { line, signal, hist };
}

window.MPData = {
  PRNG, generateCandles, sma, bollinger, rsi, macd, ema, round2,
};
