# Paper Trading Runbook

## Daily 30-Second Check

1. Open `/lab/paper-trading`.
2. Confirm `System Status`.
3. Confirm `Generated at` is current enough for the deployment context.
4. Check `Critical Events`.
5. Check `Positions`, especially `Exit Health`.

## Status Meanings

Healthy means current telemetry loaded and no active operational attention state is visible.

Attention means the system is visible, but an operator should inspect a current condition such as `PRICE_UNAVAILABLE`, scheduler gap, kill switch ON, or reprocessed tick.

Degraded means part of the dashboard or health query could not load. Treat it as a telemetry quality problem first, not proof that the trading engine is broken.

Failed is a script outcome, not a paper engine state. It means the check itself could not run.

## After-Deploy Smoke

Run:

```bash
MARKETPULSE_SMOKE_PASSWORD=dev uv run python scripts/smoke_paper_trading_ops.py --base-url http://127.0.0.1:8000
uv run python scripts/check_paper_trading_health.py sqlite:///./marketpulse.db --skip-price-smoke
uv run python scripts/smoke_notifications.py
```

Only run notification send smoke when intentionally testing the configured channel:

```bash
uv run python scripts/smoke_notifications.py --send --confirm-send
```

## PRICE_UNAVAILABLE

`PRICE_UNAVAILABLE_1` means the first exit-price lookup failed. Watch the next tick.

`PRICE_UNAVAILABLE_2` means the second consecutive lookup failed. Check provider health and ticker data availability.

`STUCK_3_PLUS` means the position has failed at least three exit attempts. Capture the dashboard, audit rows, ticker, position id, and provider smoke result before changing anything.

6h does not auto-close or repair stuck positions.

## Scheduler Gap

If a scheduler gap appears, capture:

- latest successful tick date;
- resume date;
- missed business days;
- deployment time;
- scheduler/container logs.

Do not retroactively replay missed allocation days unless a later spec explicitly allows it.

## Kill Switch ON

If kill switch is ON:

1. Confirm reason shown in dashboard or health output.
2. Confirm whether it is env override or audit flip.
3. Do not toggle it from 6f/6h tooling.
4. Follow the deployment/control-plane procedure for the environment.

## Notification Failure

Run config-only smoke first:

```bash
uv run python scripts/smoke_notifications.py
```

If config looks right, intentionally send:

```bash
uv run python scripts/smoke_notifications.py --send --confirm-send
```

The smoke title must begin with `SMOKE TEST — Paper Trading Notifications`.

## Price Provider Failure

Run health with price smoke enabled:

```bash
uv run python scripts/check_paper_trading_health.py sqlite:///./marketpulse.db
```

The price smoke checks the most recent completed NY trading day close for SPY by default. A failure is Attention, not automatic proof of engine corruption.

## Rollback / Mitigation

Safe mitigations:

- keep kill switch ON through existing deployment controls;
- pause unattended scheduler outside 6h if the deployment platform supports it;
- roll back the application image;
- capture DB/audit evidence before manual intervention.

6h never retries ticks, closes positions, edits audit rows, or repairs scheduler gaps.

## Evidence to Capture

- `/lab/paper-trading` screenshot;
- `check_paper_trading_health.py` output;
- relevant `paper_audit_event` rows;
- container/deployment logs;
- notification smoke output if notification delivery is suspect;
- price smoke output if exit pricing is suspect.

## Developer Appendix

Primary code entry points:

- `marketpulse.trading.query_models.load_paper_trading_dashboard`
- `marketpulse.trading.forward_engine.ForwardExecutionEngine`
- `marketpulse.trading.repository.Repository`
- `marketpulse.observability.paper_tick_notifier`
- `marketpulse.web.routes.lab`
