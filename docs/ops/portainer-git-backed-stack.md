# Convert the MarketPulse Portainer stack to Git-backed (GitOps)

**Goal:** Make the GitHub repo the single source of truth for the prod stack, so
adding a compose env var (e.g. `SECTOR_CACHE_PATH`) just needs a `git push` — no
more hand-patching the live stack. Also fixes "webhook doesn't re-pull the image".

**Why this is needed:** Stack 118 is currently a Portainer **web-editor** stack.
Portainer stores its own copy of the compose (in its DB, written to
`/data/compose/118/vN/` on each deploy), maintained *separately* from
`docker-compose.cn.yml` in the repo. The two drift — `AI_EVAL_*` (earlier) and
`SECTOR_CACHE_PATH` (2026-05-30) both had to be hand-added to the live stack.

**Data safety:** `/data` is a **host bind mount** (`/volume1/docker/marketpulse/data:/data`).
The DB (`marketpulse.db`), `sector_cache.json`, and `backups/` live on the host
filesystem. Deleting + recreating the stack only recreates *containers* — the
bind-mounted data is untouched. Safe. (Still take the snapshot in Step 1.)

**Downtime:** ~2–5 min while the stack is recreated (marketpulse + ib-gateway).
Do this in a quiet window, NOT right after an incident.

---

## Step 0 — Prereqs
- Portainer: https://nas.ninescrolls.us:9441
- Repo: `https://github.com/divxer/MarketPulse.git`, branch `main`, compose `docker-compose.cn.yml`

## Step 1 — Back up first (belt-and-suspenders)
SSH to the NAS and snapshot the DB + current stack config:
```bash
ts=$(date +%Y%m%d-%H%M)
sudo cp /volume1/docker/marketpulse/data/marketpulse.db /volume1/docker/marketpulse/data/marketpulse.db.pre-gitops-$ts
sudo cp /volume1/@docker/volumes/portainer_data/_data/compose/118/v14/stack.env /tmp/stack.env.backup-$ts
sudo cp /volume1/@docker/volumes/portainer_data/_data/compose/118/v14/docker-compose.yml /tmp/stack-compose.backup-$ts
echo "backed up with suffix $ts"
```
Keep `/tmp/stack.env.backup-$ts` open — you'll paste its contents into Portainer in Step 4.

## Step 2 — Create a GitHub access token (PAT)
GitHub → Settings → Developer settings → **Personal access tokens → Fine-grained tokens** → Generate:
- **Resource owner:** divxer
- **Repository access:** Only select repositories → `divxer/MarketPulse`
- **Permissions:** Repository permissions → **Contents: Read-only** (nothing else needed)
- Generate, copy the `github_pat_…` value.

## Step 3 — Note the current env, then remove the old stack
1. In Portainer → **Stacks → marketpulse** → scroll to **Environment variables**. Confirm they match `/tmp/stack.env.backup-$ts` (they should). These are the 22 keys you must carry over:
   `APP_PASSWORD_HASH, SESSION_SECRET, ANTHROPIC_API_KEY, NOTIFIER_KIND, NOTIFIER_SERVERCHAN_KEY, HTTPS_PROXY, HTTP_PROXY, NO_PROXY, AI_MODEL_ANALYZE, AI_MODEL_ROUTER, AI_EVAL_ENABLED, AI_EVAL_MAX_CALLS_PER_DAY, IBKR_USERNAME, IBKR_PASSWORD, IBKR_ACCOUNT_ID, IBKR_TRADING_MODE, IBKR_READ_ONLY_API, IB_GATEWAY_VNC_BIND, VNC_SERVER_PASSWORD, EXISTING_SESSION_DETECTED_ACTION, IBKR_FLEX_TOKEN, IBKR_FLEX_QUERY_ID`
   > These are secrets/overrides with NO default in compose. If any is missing the app won't start (e.g. `ANTHROPIC_API_KEY`, `APP_PASSWORD_HASH`, `SESSION_SECRET`).
2. **Delete** the `marketpulse` stack. When prompted whether to also remove volumes — **do NOT remove volumes** (and it doesn't matter here since `/data` is a host bind mount, but leave the option unchecked to be safe). Containers stop; the host `/volume1/docker/marketpulse/data` is untouched.

## Step 4 — Recreate as a Git stack
Portainer → **Stacks → + Add stack**:
- **Name:** `marketpulse` (keep the same name so the container name / bind mounts match)
- **Build method:** **Repository**
- **Repository URL:** `https://github.com/divxer/MarketPulse.git`
- **Repository reference:** `refs/heads/main`
- **Compose path:** `docker-compose.cn.yml`
- **Authentication:** ON → Username: `divxer`, Password/Token: the `github_pat_…` from Step 2
- **Environment variables:** click **Advanced mode** and paste the full contents of `/tmp/stack.env.backup-$ts` (all `KEY=VALUE` lines). This reproduces every secret/override.
- **GitOps updates:** enable **Automatic updates**:
  - **Polling** (interval e.g. `5m`) OR **Webhook** (copy the webhook URL into the `PORTAINER_WEBHOOK` GitHub Actions secret so CI pings it on merge).
  - Enable **Re-pull image** / **Force redeployment** so a new `:latest` is actually pulled on update. ← this also fixes the "webhook doesn't deploy" problem.
- **Deploy the stack.**

Portainer clones the repo, reads `docker-compose.cn.yml`, substitutes the env vars, re-attaches the `/volume1/docker/marketpulse/data` bind mount, and starts the containers.

## Step 5 — Verify
```bash
sudo /usr/local/bin/docker ps --filter name=marketpulse --format '{{.Names}} {{.Status}}'
sudo /usr/local/bin/docker exec marketpulse printenv SECTOR_CACHE_PATH   # -> /data/sector_cache.json
sudo /usr/local/bin/docker exec marketpulse python -c "import json;print(len(json.load(open('/data/sector_cache.json'))),'sectors cached')"
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8088/           # -> 303
```
Open `/watchlist` — sectors intact, DB intact (it's the same host dir).

## After this
- `git push` to `main` → CI builds + pushes image → Portainer (polling or webhook) pulls the new compose **and** image, redeploys. Zero manual steps.
- Compose env changes (new `${VAR}`) flow automatically; only brand-new **secrets** need adding once in the Portainer stack env.
- Note: the manual-CLI `docker compose up` recreate-race we hit does NOT affect Portainer's own deploys (its v13/v14 redeploys were clean).

## Rollback
If anything's wrong: delete the git stack, re-add a web-editor stack, paste
`/tmp/stack-compose.backup-$ts` as the compose + `/tmp/stack.env.backup-$ts` as
env, deploy. Data unaffected throughout (host bind mount).
