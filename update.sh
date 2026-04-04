#!/usr/bin/env bash
# GrowBox OTA update — pulls latest main from GitHub and restarts if changed.
# Safe to run at any time; exits 0 with no action if already up to date.

set -e

APP_DIR=/opt/growbox
LOG_PREFIX="[growbox-ota $(date '+%Y-%m-%d %H:%M:%S')]"

cd "$APP_DIR"

# Fetch without merging
git fetch origin main --quiet 2>&1 || {
    echo "$LOG_PREFIX ERROR: git fetch failed (no internet?)"
    exit 1
}

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
    echo "$LOG_PREFIX Already up to date ($(git rev-parse --short HEAD))"
    exit 0
fi

echo "$LOG_PREFIX Update available: ${LOCAL:0:7} → ${REMOTE:0:7}"

# Pull — config.json is in .gitignore so it won't be touched
git pull origin main --quiet

# Re-install deps only if requirements changed
if git diff --name-only "$LOCAL" "$REMOTE" | grep -q 'requirements.txt'; then
    echo "$LOG_PREFIX requirements.txt changed — reinstalling dependencies"
    venv/bin/pip install -r requirements.txt --quiet
fi

# Restart service
systemctl restart growbox
echo "$LOG_PREFIX Service restarted. Now at $(git rev-parse --short HEAD)"
