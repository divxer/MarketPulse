#!/bin/bash
#
# Daily SQLite backup → private GitHub repo (MarketPulse-backups).
#
# Designed to run from Synology Task Scheduler (DSM Control Panel) at e.g.
# 02:00 NAS time. See docs/BACKUP.md for one-time setup.
#
# Rolling 30-day window: dumps go to backups/YYYY/YYYY-MM-DD.sql.gz, older
# entries are removed and the change is git-pushed.

set -euo pipefail

DB_PATH="${MP_DB_PATH:-/volume1/docker/marketpulse/data/marketpulse.db}"
BACKUP_REPO="${MP_BACKUP_REPO:-/volume1/docker/marketpulse/MarketPulse-backups}"
RETENTION_DAYS="${MP_RETENTION_DAYS:-30}"
GIT_USER_EMAIL="${MP_GIT_EMAIL:-marketpulse-backup@nas}"
GIT_USER_NAME="${MP_GIT_NAME:-MarketPulse Backup Bot}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
die() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2; exit 1; }

[ -f "$DB_PATH" ] || die "DB not found at $DB_PATH"
[ -d "$BACKUP_REPO/.git" ] || die "Backup repo not initialized at $BACKUP_REPO (see docs/BACKUP.md)"

cd "$BACKUP_REPO"

# Ensure git identity (idempotent)
git config user.email "$GIT_USER_EMAIL"
git config user.name "$GIT_USER_NAME"

# Pull latest in case manual changes were made
git pull --quiet --ff-only || log "git pull failed, continuing"

today=$(date '+%Y-%m-%d')
year=$(date '+%Y')
target_dir="$BACKUP_REPO/backups/$year"
target="$target_dir/$today.sql.gz"

mkdir -p "$target_dir"

log "Dumping $DB_PATH → $target"
# .dump produces SQL text statements that can recreate the DB elsewhere.
# Pipe straight into gzip to avoid intermediate files.
sqlite3 "$DB_PATH" .dump | gzip -9 > "$target.tmp"
mv "$target.tmp" "$target"

size=$(du -h "$target" | cut -f1)
log "Backup written: $size"

# Rolling cleanup: remove files older than RETENTION_DAYS
log "Pruning backups older than $RETENTION_DAYS days"
find "$BACKUP_REPO/backups" -type f -name '*.sql.gz' -mtime "+$RETENTION_DAYS" -delete
# Drop empty year dirs
find "$BACKUP_REPO/backups" -type d -empty -delete 2>/dev/null || true

# Commit & push.
# Stage first so untracked files are detected; then check if anything is staged.
git add -A
if git diff --cached --quiet; then
  log "No changes to commit (same-day re-run?)"
  exit 0
fi

git commit -m "backup: $today ($size)" --quiet
git push --quiet
log "Pushed to remote"
