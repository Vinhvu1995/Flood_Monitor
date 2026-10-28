# traffic-cameras

Faithful scaffold of the two un-git'd Paris directories (see
`../handover/09_TRAFFIC_CAMERAS.md`):

- `rainfall_trigger.py` — live cron job (was
  `/nfs/develop_vinh/Traffic_Image_Downloader/rainfall_trigger.py`).
- `find_rainy_asc.py`, `Scan_cameras.py`, `Download_images.py`,
  `camera_metadata.py`, `xml_to_geojson.py`, `match_cameras.py` — the
  `Traffic_Flood_Detector/` discovery toolchain (Jan 2026, one-off).

## Live job

```bash
*/15 * * * * TMPDIR=/nfs/develop_vinh/tmp .venv/bin/python rainfall_trigger.py --threshold=8 >> log/rainfall_trigger.log 2>&1
```

`--threshold` default 8.0 mm (code value; SETUP.md said 10). Window 6 h,
download interval 15 min, 50 download threads, S3 `previsico-modelling-team`
eu-west-3 prefix `Traffic_Cameras/Flooded_images`.

## State file (moved off root disk)

Was `/tmp/floodwatch_state.json` (root disk). Now
`resolve_state_path()` prefers, in order: `--state-file`,
`$TRAFFIC_CAM_STATE_FILE` / `$FLOODWATCH_STATE_FILE`, then the NFS default
`/nfs/develop_vinh/Traffic_Image_Downloader/state/floodwatch_state.json`
(when writable), else `/tmp` fallback. Reads migrate from the legacy
`/tmp` file when the primary is missing.

## Notable deviations from the original

- `datetime.now(timezone.utc)` instead of deprecated `datetime.utcnow()`.
- `camera_metadata.py` grayscales with PIL/numpy (no OpenCV in the
  declared deps); Tesseract `--psm 6` unchanged.
- Original filename `xml_to_geojson_converter.py` shortened to
  `xml_to_geojson.py`.

## Tests

```bash
python -m pytest tests/ -q
```
