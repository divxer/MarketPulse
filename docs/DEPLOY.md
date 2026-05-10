# Deploying MarketPulse to Fly.io

## One-time setup

1. `fly launch --no-deploy` (accept generated `fly.toml` overrides if any).
2. Create the volume:
   ```
   fly volumes create marketpulse_data --region iad --size 1
   ```
3. Set secrets:
   ```
   fly secrets set \
     APP_PASSWORD_HASH="$(uv run python -c 'from marketpulse.auth.password import hash_password; import getpass; print(hash_password(getpass.getpass()))')" \
     SESSION_SECRET="$(openssl rand -hex 32)" \
     ANTHROPIC_API_KEY=sk-ant-...
   ```

## Deploy

```
fly deploy
```

The container runs `alembic upgrade head` on startup, then `uvicorn`.

## Logs / debugging

```
fly logs
fly ssh console
```

## Local development gotcha

If you run `uv run uvicorn ...` from a shell where `ANTHROPIC_API_KEY=` (empty)
is already exported (e.g. inside Claude Code's terminal), pydantic-settings
reads OS env first and the empty value silently overrides `.env`. Workaround:

```
unset ANTHROPIC_API_KEY ANTHROPIC_BASE_URL
uv run uvicorn marketpulse.web.main:app --reload
```

Or launch from a fresh terminal where those vars aren't pre-set.

## Backup

```
fly ssh console -C "sqlite3 /data/marketpulse.db .dump" > backup-$(date +%Y%m%d).sql
```
