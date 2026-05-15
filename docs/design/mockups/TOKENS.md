# MarketPulse · Design Tokens

All tokens are declared in `ns-tokens.css` (the NineScrolls source of truth — do not edit downstream) and `app.css` (the MarketPulse application overlay). This document is a flat reference for handoff. Production should consume `var(--ns-*)` / `var(--mp-*)` / `var(--bb-*)` directly; do **not** copy hex values into Tailwind classes.

---

## 1 · Color

### 1.1 Brand primaries

| Token | Hex | Used for |
|---|---|---|
| `--ns-primary` | `#0066cc` | Action color. Section eyebrows, primary buttons, links, indicator-strip accent. The "MarketPulse blue." |
| `--ns-primary-container` | `#3b82f6` | Hover state on primary buttons. Lighter, more saturated. |
| `--ns-tertiary` | `#004e9f` | Deeper accent under primary. Hyper-eyebrows. |
| `--ns-navy` | `#022448` | Editorial / chrome navy. **All numeric values, all headings, all serious surfaces.** Used for the second confirm button (`buy`) and the dark Bloomberg ticker headers. |
| `--ns-navy-container` | `#1e3a5f` | Hover state on navy buttons. |
| `--ns-accent-periwinkle` | `#4d94ff` | Single-word highlights over imagery. The "lift" color. |

### 1.2 Surface architecture (cool-white with violet undertone)

| Token | Hex | Used for |
|---|---|---|
| `--ns-background` | `#fcf8ff` | Page background. |
| `--ns-surface-container-lowest` | `#ffffff` | Cards. |
| `--ns-surface-container-low` | `#f5f2ff` | Pagination strip, search field, light-mode chip background, table row hover. |
| `--ns-surface-container` | `#efecff` | Sparkline track, active-row tint. |
| `--ns-surface-container-high` | `#e8e5ff` | — |
| `--ns-surface-container-highest` | `#e2e0fc` | Canvas background behind cards (design canvas only). |

### 1.3 Ink

| Token | Hex | Used for |
|---|---|---|
| `--ns-on-surface` | `#1a1a2e` | Primary body ink. |
| `--ns-on-surface-variant` | `#414753` | Secondary ink, captions. |
| `--ns-outline` | `#727784` | Hairline rules. |
| `--ns-outline-variant` | `#c1c6d5` | Subtle borders, card edges, table dividers. |

### 1.4 MarketPulse semantic — gain / loss

These are app-scope tokens added in `app.css`, harmonised with the NineScrolls success/error but tuned warmer to read as money rather than UI state.

| Token | Hex | Used for |
|---|---|---|
| `--mp-up` | `#0e8a5f` | Gain. Up arrows, % positive, candle up bodies, buy chips (sometimes — buy more often uses `--ns-primary` to avoid confusing market-direction green with action-affirmation green). |
| `--mp-up-soft` | `#d8f0e4` | Buy chip background (Tailwind-blue button alternative). |
| `--mp-up-deep` | `#0a6b48` | Histogram label text on positive month. |
| `--mp-down` | `#c0392b` | Loss. |
| `--mp-down-soft` | `#fadcd6` | Sell chip background. |
| `--mp-down-deep` | `#8b251c` | Histogram label text on negative month. |

> **Note on China-market convention:** The MarketPulse source code uses green = up / red = down (US convention) which matches NYSE/Nasdaq screens. We intentionally **did not** flip to the Hang Seng red = up / green = down convention even though the UI is in Chinese, because the underlying market data is US and the existing app already uses US convention.

### 1.5 Bloomberg variant palette (variant B only — scoped under `.bb-root`)

| Token | Hex |
|---|---|
| `--bb-bg` | `#050912` |
| `--bb-bg-2` | `#0a1320` |
| `--bb-bg-3` | `#111c2e` |
| `--bb-line` | `#1f2c45` |
| `--bb-ink` | `#e6ecf5` |
| `--bb-ink-dim` | `#93a3bf` |
| `--bb-ink-mute` | `#5b6a85` |
| `--bb-amber` | `#ffb44a` (terminal accent — function codes, command line, primary keys) |
| `--bb-up` | `#2ecc71` |
| `--bb-down` | `#ff5252` |
| `--bb-cyan` | `#5fc7ff` (SMA / RSI line on dark) |

---

## 2 · Typography

### 2.1 Stack

| Token | Family | Loaded via |
|---|---|---|
| `--ns-font-headline` | `"Space Grotesk", "Helvetica Neue", Arial, sans-serif` | Google Fonts (300/400/500/600/700) |
| `--ns-font-body` | `"Inter", "Helvetica Neue", Arial, sans-serif` | Google Fonts (300/400/500/600/700) |
| `--ns-font-mono` | `ui-monospace, "SF Mono", Menlo, Consolas, monospace` | System stack — **do not load JetBrains Mono** despite my earlier description. The mockups use the system mono stack via `ui-monospace`, which renders SF Mono on macOS and Consolas on Windows. Both have tabular numerics by default. |
| `--ns-font-icon` | `"Material Symbols Outlined"` | Google Fonts (variable axes 20–48 / 100–700 / 0–1 / -50–200) |

### 2.2 Usage rules

| Where | Family | Weight | Size | Tracking | Notes |
|---|---|---|---|---|---|
| Page hero h1 | headline | 700 | 48–72 px | `-0.04em` | "Trade Ledger" / "Holdings · Portfolio Overview" / "2026 · 5 月 12 日" |
| Card title (h2) | headline | 700 | 13 px | `-0.01em` | Inside `mp-card__head`. Always has a leading 16 px Material Symbols icon. |
| **Eyebrow** | headline | 700 | 10 px | `0.20em` UPPERCASE | The signature NineScrolls pattern. Slate-600 color by default, primary-blue with `.mp-eyebrow--primary`. Lives directly above an h1/h2/h3. |
| Body | body | 400 | 14 px | 0 | Default in tables, descriptions. |
| Small | body | 400 | 12 px | 0 | Captions, footnotes, status timestamps. |
| **Numeric / OHLC / table data** | mono | 500–700 | 11–14 px | 0 | `font-variant-numeric: tabular-nums` — applied via `.tnum` helper. ALL prices, quantities, percentages. |
| Large price display | mono | 600 | 28–72 px | `-0.02em` to `-0.04em` | The big numbers (current price, total MV, unrealised P&L). |
| Long-form reading column | body | 300/400 | 17 px | 0 | Recap article body. Line-height 1.85 for Chinese readability. |

### 2.3 Inline patterns inside long-form (Recap)

- **bold** = navy emphasis on entities (tickers, scenarios)
- *italic* via `.ai-md em` = a soft chip background (`rgba(77,148,255,0.10)`) wrapping a key statistic
- Inline `<mono>` numbers get a subtle `var(--ns-surface-container-low)` highlight to read as data even when surrounded by Chinese characters

---

## 3 · Spacing

Tailwind v4 4-px base. Used liberally:

| Token | Value |
|---|---|
| `--ns-space-1` | 4 px |
| `--ns-space-2` | 8 px |
| `--ns-space-3` | 12 px |
| `--ns-space-4` | 16 px |
| `--ns-space-5` | 20 px |
| `--ns-space-6` | 24 px |
| `--ns-space-8` | 32 px |
| `--ns-space-10` | 40 px |
| `--ns-space-12` | 48 px |
| `--ns-space-16` | 64 px |

Card internal padding: 16–24 px. Card gap on horizontal rows: 16 px. Page outer padding: 24–48 px (smaller variants use 24, editorial variants use 48). Section vertical gap: 16–24 px.

---

## 4 · Radius (precision-editorial: very low rounding)

| Token | Value | Where |
|---|---|---|
| `--ns-radius-none` | 0 | Bloomberg terminal panels, ticker tape, table cells. |
| **`--ns-radius-sm`** | **2 px** | **Default for everything: buttons, cards, chips, inputs, candle bodies, spark fills. This is the NineScrolls signature.** |
| `--ns-radius-md` | 6 px | Avoid — too soft. |
| `--ns-radius-lg` | 8 px | Avoid. |
| `--ns-radius-xl` | 12 px | Reserved for marketing-site product cards (not used here). |
| `--ns-radius-full` | 9999 px | Status dots only. Never pills for chips — chips are 2 px. |

Hard rule: **no chip, button, or card in this redesign is more rounded than 2 px**. This is what reads as "trading tool" rather than "consumer fintech."

---

## 5 · Shadows (navy-tinted, never gray)

| Token | Value |
|---|---|
| `--ns-shadow-sm` | `0 1px 2px rgba(2, 36, 72, 0.05)` |
| `--ns-shadow-card` | `0 10px 30px rgba(2, 36, 72, 0.04)` — default on `.mp-card` |
| `--ns-shadow-elevated` | `0 10px 30px rgba(2, 36, 72, 0.06)` — Variant C floating glass panels |
| `--ns-shadow-float` | `0 15px 40px rgba(2, 36, 72, 0.10)` |
| `--ns-shadow-lg` | `0 20px 50px rgba(2, 36, 72, 0.12)` |

The navy tint (`rgba(2, 36, 72, …)` not `rgba(0, 0, 0, …)`) is what keeps the cards from looking dusty on the violet-undertone background.

---

## 6 · Section header rules — the "80×4" pattern

Below every page h1 and inside major editorial section breaks:

```html
<h1 class="grotesk">Holdings · Portfolio Overview</h1>
<div class="mp-rule"></div>
```

```css
.mp-rule {
  display: block;
  width: 80px;     /* ← always 80, never wider */
  height: 4px;     /* ← always 4 */
  background: var(--ns-primary);
  margin: 24px 0;
}
```

Inside cards we use a thinner variant: a 32×3 px primary-blue mark — see `app.css` `.mp-card__title` icon block. The `.ai-md h2::before` rule renders a 3×14 primary blue tick before every AI report subheading.

---

## 7 · Motion

| Token | Value |
|---|---|
| `--ns-ease` | `cubic-bezier(0.4, 0, 0.2, 1)` |
| `--ns-ease-out` | `cubic-bezier(0, 0, 0.2, 1)` |
| `--ns-duration-fast` | 200 ms |
| `--ns-duration-base` | 300 ms |
| `--ns-duration-slow` | 500 ms |

Used sparingly — chip hover, segmented control active state, nav link color. **No entrance animations** on data tables, charts, or KPI cards. The pulse animation on "live" indicators (`@keyframes mp-pulse`) is the one exception — a 2 s radiating green ring.

---

## 8 · Component recipes

### Card
```html
<section class="mp-card">
  <div class="mp-card__head">
    <span class="mp-card__title">
      <span class="material-symbols-outlined">icon_name</span>
      Card title
    </span>
    <span>right-side meta</span>
  </div>
  <div class="mp-card__body">…</div>
</section>
```

### KPI cell (used in 5-strip header)
```html
<div class="mp-card" style="padding: 18px 20px;">
  <span class="mp-eyebrow mp-eyebrow--primary">Label</span>
  <div class="grotesk tnum">$12,604</div>   <!-- 30 px / 700 / -0.02em / navy -->
  <div>Hint text</div>                       <!-- 11.5 px / variant ink -->
</div>
```

### Segmented control (period selector)
```html
<div class="mp-seg">
  <button class="is-active">60D</button>
  <button>6M</button>
  <button>YTD</button>
</div>
```
Active = navy fill + white text. Inactive = transparent + slate-600 ink + hover navy.

### Chip (filter / state badge)
```html
<span class="mp-chip">Default · slate</span>
<span class="mp-chip mp-chip--active">Active · navy fill</span>
<span class="mp-chip mp-chip--up">+1.10%</span>      <!-- soft green -->
<span class="mp-chip mp-chip--down">-2.25%</span>    <!-- soft red -->
<span class="mp-chip mp-chip--periwinkle">买入</span> <!-- soft primary -->
```

### Table
```html
<table class="mp-table">…</table>
```
Header: 10 px uppercase eyebrow, slate-500 ink, surface-container-low fill, sticky on scroll, 18 px letter-spacing. Cells: 13 px, tabular nums, 12 px y-padding. Hover: row gets surface-container-low fill.

---

## 9 · Anti-patterns / what NOT to do

- No gradients on backgrounds. The brand reads as flat editorial.
- No emoji as iconography. Material Symbols Outlined only, always 16 or 18 px next to text labels.
- No rounded-2xl cards. Stay at 2 px.
- No purple primary buttons (the violet is surface only).
- No body copy in Space Grotesk. It's headline-only. Body is Inter.
- No `font-feature-settings: "ss01"` or similar opinionated Inter alts unless you've checked the row aligns. The default already enables `tnum` + `cv11` + `ss01` via the body rule.
- No numbers in Inter — every price, percent, or quantity is mono with `tabular-nums`.
- No "MarketPulse" wordmark in lowercase. Always uppercase Space Grotesk Bold, letter-spacing `-0.04em`.
