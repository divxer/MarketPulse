# MarketPulse Design Principles

Stable design guidelines for MarketPulse. These outlast any single feature spec — they're the constitution future specs and plans defer to.

Derived in part from the TraderCore "ruler not repairman" design philosophy (2026-05-12), adapted to a personal portfolio tracker + AI analysis tool.

---

## 1. Measure, don't auto-modify

**Rule:** Any system-generated suggestion that changes user-visible state — trade fields, holdings, settings, AI prompts, signal weights — must surface as an explicit suggestion the user opts into. Never silently apply.

**Why:** Once the system starts mutating data behind the user's back, trust evaporates. The user can no longer answer "is what I see what I actually entered?" — and every bug becomes a forensics exercise.

**Pattern:**

- Surface suggestions as a card with **before → after** delta visible
- Require an explicit "Apply" click
- Provide an undo path that previews "the following fields will revert to ..."
- Log every applied change to an audit table (timestamp + diff)

**Current places we honor this:**

- AI analysis is read-only advice; user types trades manually
- `recompute_ticker` runs only after explicit user action (POST/PUT/DELETE trade), never on schedule
- Trade edits show the existing values pre-filled; user reviews before saving

**Don't do this even if it seems convenient:**

- "Auto-categorize trades by sector"
- "AI noticed your stop-loss is loose, tightened it"
- "Auto-rebalance to target weights"
- "AI re-summarized your notes for brevity"

If a future feature wants any of the above, gate it through a suggestion card.

---

## 2. AI verdicts must be auditable

**Rule:** Every AI-generated analysis (`/stock/{ticker}/analyze`, `/recap`, future "AI scan") must persist its **inputs** alongside its **outputs**, in a way that lets a future reader reconstruct "what did the model see when it said this?"

**Why:** LLMs are non-deterministic, but their inputs are. The day the user looks at a 3-month-old analysis and asks "was Claude right?", we need three things:

1. The verdict (what Claude said)
2. The inputs (price, indicators, news headlines, holdings context at that moment)
3. The realized outcome (what the stock actually did after)

Without (2), we can't tell if Claude was wrong because the model is bad, or because the inputs were noisy, or because the world changed. Without (3), we can't tell if Claude is actually predictive.

**Pattern:**

- One row per analysis: `AiAnalysisInput` (JSON snapshot) + `AiAnalysisOutput` (verdict + reasoning text)
- A nightly cron computes `AiAnalysisOutcome` (forward 7/30/90 day return vs SPY) for each analysis
- UI surfaces a small accountability badge: "Claude historical hit rate: 7d=64% / 30d=58% (N analyses)"

**Don't:**

- Auto-tune prompts based on hit rate (that re-introduces the black box — see Principle #1)
- Display hit rate without sample size — `64% (N=3)` is misleading
- Promise a target hit rate; the user is the judge

---

## 3. Signals must declare their signal-to-noise

**Rule:** Any indicator or signal we draw on a chart (EMA cross, RSI overbought, Bollinger touch) must show its historical predictive power for the specific ticker — or be visually demoted to "decorative."

**Why:** EMA golden cross is gold on slow-trending stocks like KO and noise on speculative names like QUBT. Showing the same marker the same way for both lies about its information content.

**Pattern:**

- Backfill: for each (ticker, signal_type, lookback_years), compute forward-N-day win rate and average return
- Render signals with their stats inline: `🟦 金叉 (胜率 64% · +3.2%/20d)`
- Win rate < 50% → render semi-transparent. The marker still shows (user might still want to see), but visually the chart is saying "I don't trust this one"
- Win rate ≥ 60% → render at full opacity

**Don't:**

- Hide low-signal markers (user loses visibility into what the system "saw")
- Rank or filter signals by hit rate behind the scenes (Principle #1 again — show, don't decide)

---

## 4. Diagnostics over metric soup

**Rule:** When designing a summary view (recap, dashboard, alerts), ask **two or three concrete questions** and answer them with a verdict. Don't dump 20 numbers.

**Why:** A wall of metrics offloads cognition to the user. A diagnostic asks "is X working?" and gives ✓/⚠/✗. The user can drill into the numbers if they want, but the headline is a finding, not a feed.

**Pattern for /recap:**

- **Q1: Are your buy points good?** — avg entry price vs ticker's 30-day moving avg → ✓ if below, ⚠ if at, ✗ if above
- **Q2: Are your sell points good?** — avg exit price vs forward 5-day high → ✓ if captured ≥ 70%
- **Q3: Are you concentrated in winners?** — holdings-weighted return vs SPY same period → ✓ if outperforming

Numbers in tooltips. AI-generated narrative below the three lines, not above.

**Don't:**

- Lead with the numbers, append the verdict
- Have more than 3-4 questions (becomes a feed again)
- Use questions whose answer requires consulting a different page to interpret

---

## 5. Determinism: same inputs → same outputs

**Rule:** Any computation that's not inherently time-sensitive should be reproducible from its inputs. If we can't reproduce a result, we can't debug it.

**Why:** The hardest production bugs are the ones the developer can't reproduce. "It looked fine on my machine" usually means there's hidden state somewhere — TZ, cache, network race, random seed.

**Where this matters in MarketPulse:**

- **Chart data**: `?period=60d` should return the same bars given the same DB state. We already do this via deterministic queries. ✓
- **Indicators**: SMA/EMA/RSI computation must be pure functions of the bar series. No global state, no time-of-call dependency. ✓
- **AI inputs**: see Principle #2 — snapshot inputs so the analysis can at least be re-explained, even if the model output differs on a re-run
- **Backtests / outcome calculation**: if we add a hit-rate computation later, it should be a pure function of (input snapshot, forward bar data) and produce identical output on every re-run

**Don't:**

- Cache derived state in a way that's not content-addressed (cache key must encode all inputs)
- Use `datetime.now()` inside a computation that's supposed to be reproducible — pass `as_of` instead
- Mix data sources non-deterministically (e.g., "use Tencent if reachable else yfinance") — pick one, log the choice

---

## How to use this document

When writing a new spec under `docs/superpowers/specs/`, the **Risk** section should reference these principles by number where relevant. Example: "Risk: low. The 'auto-apply suggested label' UX violates Principle #1 — we instead surface a card with explicit accept."

When a feature wants to violate one of these, the spec must explicitly call out why, what mitigations apply, and link to the discussion that justified the exception. Principles aren't laws — they're the default we deviate from with a paper trail.

When adding a principle here, write it concrete enough to fail a code review against. "Be user-friendly" is not a principle; "every suggestion must show before → after" is.

## Inspiration

- TraderCore's backtesting design (2026-05-12 social post: "做了一把尺子,不是黑箱操作"). The "measure, don't auto-modify" formulation and the diagnostic-over-metrics pattern come directly from there. We adapted them from a quant-platform context to a personal-portfolio context.
