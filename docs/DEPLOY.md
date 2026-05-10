# Deploying MarketPulse

Two paths documented. **Synology NAS + Tailscale is the recommended one** for personal/self-hosted use — zero hosting cost, data stays on your hardware, secure remote access via Tailscale's mesh.

- [Synology NAS + Portainer + GHCR (one-click GitOps)](#synology-nas--portainer--ghcr-recommended)
- [Synology NAS + manual docker compose](#synology-nas--manual-docker-compose)
- [Fly.io (alternative)](#flyio-alternative)
- [Local development gotcha](#local-development-gotcha)
- [Backup](#backup)

---

## Synology NAS + Portainer + GHCR (recommended)

Fully automated pipeline: `git push` → GitHub Actions builds an x86_64 image → pushes to GHCR → Portainer pulls and redeploys via webhook. Tested on **DS920+** (DSM 7.2+ with Container Manager + Portainer CE).

### One-time setup

#### 1. Push the project to GitHub (private repo recommended)

```sh
gh repo create MarketPulse --private --source=. --remote=origin
git push -u origin main
```

The first push triggers `.github/workflows/build.yml`, which builds `linux/amd64` and pushes to `ghcr.io/<your-username>/marketpulse:latest`. Watch the Actions tab; first build is ~5 minutes.

#### 2. Make the GHCR image accessible

If your repo is private, the GHCR image is also private and Portainer needs credentials.

- Create a GitHub PAT with `read:packages` scope: https://github.com/settings/tokens
- (Alternatively, in your repo's Package settings, you can flip the package visibility to **public** — the image is just compiled bytecode, no secrets, but the repo source stays private.)

#### 3. Install Tailscale on the NAS

DSM → **Package Center** → install **Tailscale** → log in. The NAS appears in your tailnet (e.g. `nas`, `100.x.y.z`).

#### 4. Add a Registry to Portainer (only if GHCR image is private)

Portainer → **Registries** → **Add registry** → Custom registry:
- Name: `ghcr`
- URL: `ghcr.io`
- Authentication: yes
- Username: your GitHub username
- Password: the PAT from step 2

#### 5. Create the Portainer Stack

Portainer → **Stacks** → **Add stack**:
- Name: `marketpulse`
- Build method: **Repository**
- Repository URL: `https://github.com/<your-username>/MarketPulse`
- Reference: `refs/heads/main`
- Compose path: `docker-compose.prod.yml`
- Authentication: tick if private repo (use your PAT)
- **Environment variables** (paste these — values from your local `.env`):
  ```
  APP_PASSWORD_HASH=<bcrypt hash>
  SESSION_SECRET=<openssl rand -hex 32 output>
  ANTHROPIC_API_KEY=sk-ant-...
  ```
  (Optional vars have defaults; only override if needed.)
- **Enable webhook** ✅ — Portainer generates a URL you'll need next
- Deploy

Generate the password hash on any machine with the project checked out:

```sh
uv run python -c 'from marketpulse.auth.password import hash_password; import getpass; print(hash_password(getpass.getpass()))'
```

#### 6. Wire the webhook back to GitHub Actions

Copy the webhook URL from Portainer (Stack details → Webhooks). In your GitHub repo:

**Settings → Secrets and variables → Actions → New repository secret**:
- Name: `PORTAINER_WEBHOOK`
- Value: (the URL Portainer gave you)

Done. From now on:

```sh
# edit code …
git push
```

→ GitHub Actions builds → pushes new image to GHCR → calls Portainer webhook → Portainer pulls and restarts. NAS gets the new version in 3–5 minutes with zero clicks.

### Accessing the app

- Local network: `http://<nas-local-ip>:8000`
- Anywhere via Tailscale: `http://nas:8000` (any device with Tailscale on)

### When things go wrong

- **Actions build fails:** check the Actions tab. Most failures are auth/permissions; verify `packages: write` permission is set on the workflow (it is, in `build.yml`).
- **Portainer can't pull image:** registry credentials wrong. Re-check step 4.
- **Webhook didn't fire:** check `PORTAINER_WEBHOOK` secret. The workflow is fault-tolerant — failed webhook doesn't fail the build.
- **App starts but `/health` 502s:** check Portainer logs for the container; usually a missing env var.

---

## Synology NAS + manual docker compose

Use this if you want NAS-side build (no GitHub Actions / GHCR), or you don't want to push code to GitHub at all. Tested on DS920+.

Tested on **DS920+** (DSM 7.2+, x86_64). Should work on any Synology with Container Manager (DS218+ and newer).

### One-time setup

#### 1. Install Tailscale on the NAS

DSM → **Package Center** → search "Tailscale" → Install → log in with your Tailscale account. After install, the NAS appears in your Tailscale admin console with a name like `nas` and an IP like `100.x.y.z`.

(Install Tailscale on your laptop/phone too if you haven't — `tailscale up` from the [Tailscale download page](https://tailscale.com/download).)

#### 2. Prepare the project on the NAS

SSH into the NAS (`ssh admin@nas-local-ip`) or use File Station. Create a project directory:

```sh
mkdir -p /volume1/docker/marketpulse
cd /volume1/docker/marketpulse
```

Copy the project files there (clone the repo, scp, or File Station upload). You need at minimum:
- `Dockerfile`
- `docker-compose.yml`
- `pyproject.toml`, `uv.lock`, `.python-version`
- `marketpulse/`, `alembic/`, `alembic.ini`, `scripts/`
- `package.json`, `tailwind.config.js` (for the build stage)

#### 3. Create `.env`

```sh
cd /volume1/docker/marketpulse
cat > .env <<EOF
APP_PASSWORD_HASH=<bcrypt hash — see below>
SESSION_SECRET=<32+ random bytes>
ANTHROPIC_API_KEY=sk-ant-...
DATABASE_URL=sqlite:////data/marketpulse.db
WATCHLIST_RECAP_TIME=16:30
LOG_LEVEL=INFO
AI_MODEL=claude-sonnet-4-6
AI_CACHE_TTL_HOURS=24
NEWS_CACHE_TTL_DAYS=7
EOF
chmod 600 .env
```

Generate the password hash on any machine with the project checked out:

```sh
uv run python -c 'from marketpulse.auth.password import hash_password; import getpass; print(hash_password(getpass.getpass()))'
```

Generate a session secret:

```sh
openssl rand -hex 32
```

#### 4. Build & start

**Option A — Synology Container Manager (GUI):**
1. Open Container Manager → **Project** → **Create**
2. Path: `/volume1/docker/marketpulse`
3. Source: "Use existing docker-compose.yml in the project folder"
4. Build → wait for image to build (~3–5 min first time)
5. Start

**Option B — SSH:**
```sh
cd /volume1/docker/marketpulse
sudo docker compose up -d --build
sudo docker compose logs -f marketpulse
```

The container runs `alembic upgrade head` on startup, then `uvicorn` on port 8000.

#### 5. Access

- **From the NAS local network:** `http://nas-local-ip:8000`
- **From anywhere via Tailscale:** `http://<nas-tailscale-name>:8000` (e.g. `http://nas:8000` if you named it `nas`)
- **Pretty URL via MagicDNS:** Tailscale's MagicDNS gives every machine a stable name; no DNS config needed.

### Updating

```sh
cd /volume1/docker/marketpulse
git pull   # or copy new files in
sudo docker compose up -d --build
```

### Logs / debugging

```sh
sudo docker compose logs -f marketpulse
sudo docker compose exec marketpulse sh
```

Or in Container Manager → Project → marketpulse → Logs / Terminal.

### Resource usage on DS920+

- Image size: ~250 MB
- Idle RAM: ~80 MB
- Recap-generating RAM: ~150 MB peak
- Disk: <100 MB for the SQLite DB even with months of recaps

### HTTPS (optional, mostly unnecessary on Tailscale)

Tailscale connections are already encrypted end-to-end. If you also want a `https://marketpulse.your-tailnet.ts.net` URL with a real cert, enable [Tailscale HTTPS](https://tailscale.com/kb/1153/enabling-https/) for the tailnet, then put MarketPulse behind a reverse proxy or use `tailscale serve` from the NAS:

```sh
sudo tailscale serve --bg --https=443 http://localhost:8000
```

---

## Fly.io (alternative)

Use this if you don't have a NAS or want a public-internet deployment without setting up Tailscale.

### One-time setup

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

### Deploy

```
fly deploy
```

Fly removed the always-on free allowance, so a `shared-cpu-1x` 256 MB machine costs ~$2–3/month at idle plus bandwidth. Anthropic API calls are billed separately by Anthropic in both deployments.

### Logs / debugging

```
fly logs
fly ssh console
```

---

## Local development gotcha

If you run `uv run uvicorn ...` from a shell where `ANTHROPIC_API_KEY=` (empty)
is already exported (e.g. inside Claude Code's terminal), pydantic-settings
reads OS env first and the empty value silently overrides `.env`. Workaround:

```
unset ANTHROPIC_API_KEY ANTHROPIC_BASE_URL
uv run uvicorn marketpulse.web.main:app --reload
```

Or launch from a fresh terminal where those vars aren't pre-set.

---

## Backup

### Synology NAS

The SQLite file lives at `/volume1/docker/marketpulse/data/marketpulse.db`. Synology Hyper Backup or a daily snapshot of `/volume1/docker/marketpulse/data` is enough.

Manual dump:
```sh
sudo docker compose exec marketpulse sqlite3 /data/marketpulse.db .dump > backup-$(date +%Y%m%d).sql
```

### Fly.io

```
fly ssh console -C "sqlite3 /data/marketpulse.db .dump" > backup-$(date +%Y%m%d).sql
```
