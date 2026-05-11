# Backup & Restore

Daily SQLite dump → private GitHub repo (`divxer/MarketPulse-backups`), rolling 30-day window. Backup runs from Synology Task Scheduler on the NAS using `scripts/backup.sh`.

## One-time setup (on the NAS)

### 1. Generate an SSH deploy key

```sh
ssh-keygen -t ed25519 -f /volume1/homes/divxer/.ssh/mp_backup_key -C "marketpulse-backup@nas" -N ""
cat /volume1/homes/divxer/.ssh/mp_backup_key.pub
```

Copy the public key.

### 2. Add the key as a deploy key (write access) on the backups repo

On any machine with `gh` authenticated as `divxer`:

```sh
gh repo deploy-key add /volume1/homes/divxer/.ssh/mp_backup_key.pub \
  --repo divxer/MarketPulse-backups --title "nas-backup" --allow-write
```

Or via the web UI: https://github.com/divxer/MarketPulse-backups/settings/keys → **Add deploy key** → paste the public key → ✅ Allow write access.

### 3. Configure SSH to use this key for github.com pushes from the backup repo

```sh
mkdir -p /volume1/homes/divxer/.ssh
cat >> /volume1/homes/divxer/.ssh/config <<'EOF'

Host github-mp-backup
  HostName github.com
  User git
  IdentityFile /volume1/homes/divxer/.ssh/mp_backup_key
  IdentitiesOnly yes
EOF
chmod 600 /volume1/homes/divxer/.ssh/config
```

### 4. Clone the backup repo to a known path

```sh
cd /volume1/docker/marketpulse
git clone git@github-mp-backup:divxer/MarketPulse-backups.git
cd MarketPulse-backups
git remote set-url origin git@github-mp-backup:divxer/MarketPulse-backups.git
```

### 5. Test the backup script manually

```sh
bash /volume1/docker/marketpulse/MarketPulse/scripts/backup.sh
```

(Assumes the main MarketPulse repo is cloned at `/volume1/docker/marketpulse/MarketPulse` for the script. If you've placed it elsewhere, copy `scripts/backup.sh` to anywhere convenient.)

You should see a new commit at `https://github.com/divxer/MarketPulse-backups` containing `backups/YYYY/YYYY-MM-DD.sql.gz`.

### 6. Schedule it daily in DSM

DSM **Control Panel → Task Scheduler → Create → Scheduled Task → User-defined script**:

- **General**:
  - Task: `MarketPulse Backup`
  - User: `divxer` (the user whose SSH key was generated)
  - Enabled: ✅
- **Schedule**:
  - Date: Daily
  - First run time: `02:00` (NAS local time)
- **Task Settings → User-defined script**:
  ```sh
  bash /volume1/docker/marketpulse/MarketPulse/scripts/backup.sh >> /volume1/docker/marketpulse/backup.log 2>&1
  ```
- **Notification**: optional; send email on failure

Save. The first scheduled run will fire the next 02:00.

## Verifying

After the first scheduled (or manual) run:

- Check the log: `tail -50 /volume1/docker/marketpulse/backup.log`
- Look at https://github.com/divxer/MarketPulse-backups — should have a new commit and a file at `backups/<year>/<date>.sql.gz`.

## Restore from a backup

If the NAS dies or the SQLite file is corrupted:

```sh
# 1. On a clean machine (or a fresh NAS), clone the backup repo
git clone git@github-mp-backup:divxer/MarketPulse-backups.git /tmp/mp-restore
cd /tmp/mp-restore

# 2. Pick the date you want (latest by default)
ls backups/*/

# 3. Decompress + import into a fresh SQLite file
gunzip -c backups/2026/2026-05-09.sql.gz | sqlite3 /tmp/marketpulse.db

# 4. Verify integrity
sqlite3 /tmp/marketpulse.db "SELECT COUNT(*) FROM watchlist_items;"

# 5. Drop into place
sudo docker compose stop marketpulse
sudo cp /tmp/marketpulse.db /volume1/docker/marketpulse/data/marketpulse.db
sudo chown 1001:1001 /volume1/docker/marketpulse/data/marketpulse.db
sudo docker compose start marketpulse
```

## What's NOT backed up

- The `.env` file (contains the bcrypt password hash, session secret, Anthropic key — these are configuration, not application data)
- Container images (they're rebuilt automatically by GitHub Actions)
- Logs

So the full restore recipe is: re-clone source repo → rebuild stack via Portainer → restore SQLite from this backup → set `.env` from your password manager. Everything else regenerates.

## Configuration knobs

`scripts/backup.sh` reads these env vars (all optional, with sensible defaults):

| Variable | Default | What it does |
|---|---|---|
| `MP_DB_PATH` | `/volume1/docker/marketpulse/data/marketpulse.db` | Source SQLite file |
| `MP_BACKUP_REPO` | `/volume1/docker/marketpulse/MarketPulse-backups` | Local clone of backup repo |
| `MP_RETENTION_DAYS` | `30` | Keep daily backups for this many days |
| `MP_GIT_EMAIL` | `marketpulse-backup@nas` | Commit author email |
| `MP_GIT_NAME` | `MarketPulse Backup Bot` | Commit author name |
