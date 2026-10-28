#!/bin/bash
# daily_monitoring_agent.sh — Phase 1 (detect) -> per-country scrape -> Phase 2 (validate).
set -u
export TMPDIR="${TMPDIR:-/nfs/develop_webscraper/tmp}"
test -f .disable_daily_monitoring && exit 0
command -v claude >/dev/null || { echo "claude CLI missing"; exit 1; }
rm -f exports/pending_scrape_UK.json exports/pending_scrape_US.json
run_claude_phase() {  # $1=prompt $2=logtag
  out=$(claude -p "$1" --model claude-sonnet-5 --output-format json 2>phase_err.log); rc=$?
  echo "$out" > "cron_logs/daily_monitoring_agent_runs_$(date +%s)_$2.jsonl"
  is_err=$(echo "$out" | python3 -c "import sys,json;print(json.load(sys.stdin).get('is_error',False))" 2>/dev/null || echo False)
  if [ "$rc" != "0" ] || [ "$is_err" = "True" ]; then
    echo "[monitor] phase $2 failed: $(cat phase_err.log | head -c 1000)" >> cron_logs/monitor.log
  fi
  echo "$out"
}
run_claude_phase "$PHASE1_PROMPT" phase1
for CC in UK US; do
  test -f "exports/pending_scrape_${CC}.json" || continue
  S=$(python3 -c "import json;print(json.load(open('exports/pending_scrape_${CC}.json'))['start_date'])")
  D=$(python3 -c "import json;print(json.load(open('exports/pending_scrape_${CC}.json'))['duration'])")
  SUM=$(./scripts/agent_daily_run.sh "$CC" "$S" "$D") || echo "[monitor] $CC scrape failed: $SUM" >> cron_logs/monitor.log
  echo "$SUM"
  run_claude_phase "Phase2 validate $SUM" phase2
  PY=$(echo "$SUM" | sed -n 's/.*RUN_SUMMARY: //p' | head -1)
  ./scripts/validation_tooling.py publish "$PY" || echo "[monitor] publish failed $CC: $PY" >> cron_logs/monitor.log
done
echo END
