# Paper Trading Acceptance Checklist

This checklist is required before enabling unattended daily paper ticks after a new deployment.

## Immediate Post-Deploy

- [ ] App container/process is running.
- [ ] Database migration code head is visible: `uv run alembic heads`.
- [ ] Deployed database revision is current: `uv run alembic current`.
- [ ] `/lab/paper-trading` route smoke passes:
  ```bash
  MARKETPULSE_SMOKE_PASSWORD=dev uv run python scripts/smoke_paper_trading_ops.py --base-url http://127.0.0.1:8000
  ```
- [ ] DB health snapshot runs:
  ```bash
  uv run python scripts/check_paper_trading_health.py sqlite:///./data/marketpulse.db --skip-price-smoke
  ```
- [ ] Notification config smoke runs:
  ```bash
  uv run python scripts/smoke_notifications.py
  ```
- [ ] Price provider smoke runs:
  ```bash
  uv run python scripts/check_paper_trading_health.py sqlite:///./data/marketpulse.db
  ```
- [ ] If price smoke reports Attention, classify it as external data/provider availability first, not an automatic deployment rollback.
- [ ] No unexpected `Degraded` state.
- [ ] No unexpected control-plane buttons are visible in `/lab/paper-trading`.

## Optional Notification Send

- [ ] If intentionally testing notification delivery, run:
  ```bash
  uv run python scripts/smoke_notifications.py --send --confirm-send
  ```
- [ ] Received title starts with `SMOKE TEST — Paper Trading Notifications`.

## Next Real Tick Acceptance

These items require the next real paper tick.

- [ ] Current Operational Window advanced or intentionally stayed unchanged.
- [ ] Latest tick status is understandable.
- [ ] 6g notification behavior matches tick outcome.
- [ ] Orders/Fills lifecycle is visible if orders were placed.
- [ ] Positions exit health is visible if positions are open.
- [ ] `PRICE_UNAVAILABLE` and recovery behavior, if present, matches audit rows.
- [ ] No scheduler gap unless an actual missed business day occurred.

## Accept / Reject Decision

- [ ] Accept unattended daily paper ticks.
- [ ] Keep unattended ticks disabled or kill switch ON.
- [ ] Roll back deployment.

## Sign-Off

- Date/time:
- Operator:
- App version / commit:
- DB URL or environment:
- Notes:
