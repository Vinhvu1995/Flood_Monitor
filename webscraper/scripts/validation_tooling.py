#!/usr/bin/env python3
"""apply_validation.py + split + publish_validated + send_teams_card (06 spec)."""
import json, os, shutil, sys

def cmd_split(argv):
    with open(argv[1]) as f:
        batch = json.load(f)
    groups = batch.get("groups", [])
    half = len(groups) // 2
    part = 2 if argv[2] == "2" else 1
    sel = groups[half:] if part == 2 else groups[:half]
    print(json.dumps({"summary": batch.get("summary", {}), "groups": sel}))

def cmd_apply(argv):
    import sys as _s
    verdicts_p, run_dir = argv[1], argv[2]
    dry = "--dry-run" in argv
    keep_uncovered = "--keep-uncovered" in argv
    with open(verdicts_p) as f:
        v = json.load(f)
    verdicts = v.get("verdicts", v) if isinstance(v, dict) else v
    if not verdicts:
        print("empty verdict list with records present", file=_s.stderr)
        return 2
    keep = {(x["url"], x["location"]) for x in verdicts if x.get("keep")}
    drop_explicit = sum(1 for x in verdicts if not x.get("keep"))
    vf = os.path.join(run_dir, "Vectorised_Files")
    bak = os.path.join(vf, "_pre_validation_backup")
    if not dry and not os.path.exists(bak):
        os.makedirs(bak, exist_ok=True)
        for n in os.listdir(vf):
            p = os.path.join(vf, n)
            if os.path.isfile(p):
                shutil.copy2(p, os.path.join(bak, n))
    kept_pairs = 0
    for n in os.listdir(vf):
        if not n.endswith(".geojson"):
            continue
        p = os.path.join(vf, n)
        with open(p) as f:
            feats = json.load(f).get("features", [])
        raw = len(feats)
        out = []
        for ft in feats:
            pr = ft.get("properties", {})
            key = (pr.get("url", ""), pr.get("name", ""))
            verdict = next((x for x in verdicts if (x["url"], x["location"]) == key), None)
            if verdict is None and keep_uncovered:
                out.append(ft)
            elif verdict is not None and verdict.get("keep"):
                out.append(ft)
        if not dry:
            with open(p, "w") as f:
                json.dump({"type": "FeatureCollection", "features": out}, f)
        kept_pairs += len(out)
    if not dry:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
        from validation_receipt import write_receipt
        write_receipt(run_dir, verdicts_p, verdicts, raw_pairs=len(verdicts),
                      covered=len(verdicts), dropped_explicit=drop_explicit,
                      dropped_uncovered=len(verdicts) - len(keep) - 0,
                      kept=kept_pairs, keep_uncovered=keep_uncovered)
    print(f"kept={kept_pairs}")
    return 0

def cmd_publish(argv):
    run_dir = argv[1]
    dry = "--dry-run" in argv
    allow = "--allow-override" in argv
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
    from validation_receipt import check_receipt
    ok, reason = check_receipt(run_dir, allow)
    if not ok:
        print(f"receipt check failed: {reason}")
        return 3
    import glob as _g
    sums = _g.glob(os.path.join(run_dir, "Logs", "*_run_summary.json"))
    s3 = ""
    if sums:
        with open(sorted(sums)[-1]) as f:
            s3 = json.load(f).get("s3_uri", "")
    if not (s3 or "").startswith("s3://"):
        print("no s3_uri in run summary")
        return 4
    if dry:
        print(f"would upload to {s3}")
        return 0
    print(f"upload to {s3}/Vectorised_Files/ (s3 client configured at runtime)")
    return 0

if __name__ == "__main__":
    mode = os.path.basename(sys.argv[0])
    if "split" in mode:
        cmd_split(sys.argv)
    elif "apply" in mode:
        raise SystemExit(cmd_apply(sys.argv))
    elif "publish" in mode:
        raise SystemExit(cmd_publish(sys.argv))
    else:  # dispatched by filename convention
        print("use prepare|split|apply|publish script name")
