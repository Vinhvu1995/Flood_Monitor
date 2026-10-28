#!/bin/bash
# Pull important Paris data into Napoleon (this machine).
# Read-only on Paris. Excludes secrets (.env, tmp/private), bulk data, caches.
# Usage: bash scripts/pull_paris.sh [outdir]
set -u
OUT="${1:-_napoleon_data/paris}"
mkdir -p "$OUT"
# Pipe commands into a remote bash through both hops (quoting-safe).
on_paris() { printf '%s' "$1" | timeout 120 tailscale ssh vinh-previsico 'ssh -o BatchMode=yes -o ConnectTimeout=15 paris bash -s'; }
echo "==> Paris identity + layout"
on_paris 'hostname; ls -la /nfs/develop_vinh /nfs/develop_webscraper' > "$OUT/paris_layout.txt" 2>&1 || echo "Paris unreachable — run this from a host with tailscale access" | tee "$OUT/paris_layout.txt"
echo "==> tar of small code/state (no .env, data, exports, caches, logs)"
on_paris 'cd /nfs/develop_vinh; find tomtom-alerts traffic-incident-live-data Traffic_Flood_Detector Traffic_Image_Downloader -path "*/.git" -prune -o -path "*/.venv" -prune -o -path "*__pycache__*" -prune -o -path "*rainfall_downloads*" -prune -o -path "*uk_incident_runs*" -prune -o -type f \( -name "*.py" -o -name "*.sh" -o -name "*.md" -o -name "*.toml" -o -name ".env.example" -o -path "*/state/*.json" \) -print | tar czf - -T -' > "$OUT/develop_vinh_code.tgz" 2>"$OUT/develop_vinh_code.err" || true
echo "==> targeted important data (small, bounded)"
on_paris 'cd /nfs/develop_vinh; tar czf - Traffic_Image_Downloader/Camera_locations/Traffic_Cameras_Road_Flood_Risk.geojson tomtom-alerts/uk_strips_tomtom.geojson tomtom-alerts/uk_boundary.geojson tomtom-alerts/state/storm_sessions.json tomtom-alerts/state/alerted_incidents.json tomtom-alerts/state/credit_health_status.json 2>/dev/null' > "$OUT/key_geo_state.tgz" 2>"$OUT/key_geo_state.err" || true
echo "==> latest TomTom run only (bounded to newest date dir)"
on_paris 'd=$(ls -1 /nfs/develop_vinh/tomtom-alerts/uk_incident_runs 2>/dev/null | sort | tail -1); [ -n "$d" ] && tar -C /nfs/develop_vinh/tomtom-alerts/uk_incident_runs -czf - "$d" || echo "no runs"' > "$OUT/tomtom_latest_run.tgz" 2>"$OUT/tomtom_latest_run.err" || true
echo "==> latest rainfall ASC folder listing (no bulk copy)"
on_paris 'd=$(ls -1 /nfs/develop_pff/processing/UK/national/Rainfall/Hourly 2>/dev/null | sort | tail -3); for x in $d; do echo "== $x"; ls /nfs/develop_pff/processing/UK/national/Rainfall/Hourly/$x | head -30; done' > "$OUT/rainfall_listing.txt" 2>&1 || true
echo "done -> $OUT (never copies .env, tmp/private, bulk exports/downloads)"
