#!/bin/bash
# Pull important S3 data into Napoleon (bounded, recent-only).
# Bucket: previsico-modelling-team. No writes to S3, downloads only.
# Usage: bash scripts/pull_s3.sh [outdir] [--latest-only]
set -u
OUT="${1:-_napoleon_data/s3}"
mkdir -p "$OUT"
echo "==> S3 prefixes (recent only — full syncs are very large)"
for prefix in "TomTomAlerts" "Traffic_Cameras/Flooded_images" "Traffic_Incidents/Flood_Incidents" "Daily_Flood_records"; do
  echo "== $prefix"
  aws s3 ls "s3://previsico-modelling-team/$prefix/" --recursive --region eu-west-3 2>&1 | tail -5 | tee "$OUT/ls_$(echo "$prefix" | tr '/' '_').txt" || echo "aws s3 ls failed for $prefix (need AWS creds + network)" | tee "$OUT/ls_$(echo "$prefix" | tr '/' '_').txt"
done
echo "==> to download latest TomTom + incidents (uncomment/run manually):"
echo 'aws s3 sync s3://previsico-modelling-team/TomTomAlerts '"$OUT"'/TomTomAlerts --region eu-west-3 --exclude "*" --include "*$(date +%Y-%m-%d)*"'
echo 'aws s3 sync s3://previsico-modelling-team/Traffic_Incidents '"$OUT"'/Traffic_Incidents --region eu-west-3 --exclude "*" --include "*$(date +%Y-%m-%d)*"'
echo "done -> $OUT"
