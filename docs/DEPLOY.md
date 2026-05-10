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

## Backup

```
fly ssh console -C "sqlite3 /data/marketpulse.db .dump" > backup-$(date +%Y%m%d).sql
```
