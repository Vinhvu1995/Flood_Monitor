#!/bin/bash
# agent_daily_run.sh <UK|US> [start-date] [duration] — wraps main_country.py.
set -u
CC=${1:?UK|US}; START=${2:-}; DUR=${3:-2}
export USE_MS_GEOCODER=1 PRECISION_JUDGE=1
MODEL=${MODEL:-gpt-5-nano}
START_TS=$(date +%s)
if [ -n "$START" ]; then
  PY_CMD=".venv/bin/python src/main_country.py --country $CC --start-date $START --duration $DUR --fname ${START}_${CC}_manual --model=$MODEL"
else
  PY_CMD=".venv/bin/python src/main_country.py --country $CC --force-trigger --model=$MODEL"
fi
if [ -n "${SCRAPE_CMD_OVERRIDE:-}" ]; then PY_CMD="$SCRAPE_CMD_OVERRIDE"; fi
$PY_CMD; RC=$?
PTR="Logs/last_run_${CC}.json"
SUM=""
if [ -f "$PTR" ] && [ "$PTR" -nt /tmp/agent_run_start_$CC 2>/dev/null ] || [ -f "$PTR" ]; then SUM="$PTR"; fi
if [ -z "$SUM" ]; then SUM=$(ls -t exports/*/Run_*/$CC 2>/dev/null | head -1); fi
echo "RUN_SUMMARY: $SUM"
exit $RC
