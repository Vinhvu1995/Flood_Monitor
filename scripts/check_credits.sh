#!/bin/bash
# check_credits.sh — Baseten auto-check, fails soft to fallback, never prints keys.
# Usage: scripts/check_credits.sh [--dry-run]
# Exit 0 = Baseten path allowed; exit 2 = near cap, use heuristics; exit 1 = check failed (caller decides).
set -u
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
START=$(date -d "-1 day" +%F 2>/dev/null || date -v-1d +%F)
END=$(date +%F 2>/dev/null || date +%F)
if [ -z "${BASETEN_API_KEY:-}" ]; then
  echo '{"baseten":"no-key","action":"fallback-heuristic"}'
  exit 2
fi
RESP=$(curl -s -m 20 -H "Authorization: Bearer $BASETEN_API_KEY" \
  "https://api.baseten.co/v1/billing/usage_summary?start_date=$START&end_date=$END" 2>&1) || {
  echo '{"baseten":"unreachable","action":"caller-decides"}'; exit 1; }
# Redact any accidental key echo, log raw shape without secrets.
CLEAN=$(printf '%s' "$RESP" | sed -E 's/[A-Za-z0-9_\-]{20,}/<redacted>/g' | head -c 1500)
echo "{\"baseten\":\"checked\",\"usage\":$CLEAN,\"action\":\"gate-on-credits_used\"}"
USED=$(printf '%s' "$RESP" | python3 -c "import sys,json;print(json.load(sys.stdin).get('credits_used','?'))" 2>/dev/null || echo "?")
echo "credits_used=$USED (cap \$100; near cap -> fallback-heuristic)" | sed -E 's/[A-Za-z0-9_\-]{20,}/<redacted>/g'
[ "$DRY" = "1" ] && exit 0
python3 - "$USED" <<'EOF' 2>/dev/null || exit 1
import sys
try:
    used = float(sys.argv[1])
except ValueError:
    sys.exit(1)  # unknown shape -> caller decides
sys.exit(2 if used >= 90.0 else 0)
EOF
