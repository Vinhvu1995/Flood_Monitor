#!/bin/bash
# Install the fixed traffic-incident collector cron entry.
# FIX: the old entry used `cd /home/user/traffic-incident-live-data`, a
# directory that does not exist — the code lives at
# /nfs/develop_vinh/traffic-incident-live-data, so every run failed.
# Run this ON Paris as user vinh. Never print secrets: it touches no env values.
set -euo pipefail

LIVE_DIR=/nfs/develop_vinh/traffic-incident-live-data
UV=/home/vinh/.local/bin/uv
ENTRY="*/2 * * * * cd ${LIVE_DIR} && ${UV} run python run.py >> cron.log 2>&1"

if [ ! -d "$LIVE_DIR" ]; then
  echo "ERROR: $LIVE_DIR not found (NFS not mounted?). Aborting." >&2
  exit 1
fi

tmp=$(mktemp)
crontab -l 2>/dev/null | grep -v 'traffic-incident-live-data' > "$tmp" || true
echo "$ENTRY" >> "$tmp"
crontab "$tmp"
rm -f "$tmp"
echo "Installed: $ENTRY"
