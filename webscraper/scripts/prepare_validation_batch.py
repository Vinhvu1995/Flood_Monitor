#!/usr/bin/env python3
"""prepare_validation_batch.py <run_dir> — group records by URL (06 spec)."""
import csv, glob, json, os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from article_signals import date_signal

def load_texts(run_dir):
    texts, snippets = {}, {}
    persisted = os.path.join(run_dir, "Logs", "article_texts.json")
    if os.path.exists(persisted):
        with open(persisted) as f:
            texts = json.load(f) or {}
    for pat in (os.path.join(run_dir, "Logs", "*_extracted_sources.csv"),
                os.path.join(run_dir, "Logs", "*_results.csv")):
        for p in glob.glob(pat):
            with open(p, newline="", encoding="utf-8", errors="replace") as f:
                for row in csv.DictReader(f):
                    u = row.get("url", "")
                    if u and u not in texts:
                        texts[u] = row.get("article_text") or row.get("text", "")
    for p in glob.glob(os.path.join(run_dir, "Logs", "*_content.csv")):
        with open(p, newline="", encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                if row.get("snippet_fallback") in ("1", "True", "true"):
                    snippets[row.get("url", "")] = True
    return texts, snippets

def main(argv):
    run_dir = argv[1]
    summaries = glob.glob(os.path.join(run_dir, "Logs", "*_run_summary.json"))
    if not summaries:
        summaries = glob.glob(os.path.join(run_dir, "Logs", "run_summary.json"))
    with open(sorted(summaries)[-1]) as f:
        summ = json.load(f)
    window = summ.get("window", {})
    country = summ.get("country", "UK")
    texts, snippets = load_texts(run_dir)
    feats = []
    for p in glob.glob(os.path.join(run_dir, "Vectorised_Files", "*.geojson")):
        with open(p) as f:
            feats += json.load(f).get("features", [])
    groups = {}
    for ft in feats:
        pr = ft.get("properties", {})
        u = pr.get("url", "")
        g = groups.setdefault(u, {"url": u, "record_count": 0, "locations": [],
                                  "article_text": texts.get(u, "")})
        g["record_count"] += 1
        g["locations"].append({"location": pr.get("name", ""),
                               "type": pr.get("location_type", ""),
                               "geometry_kind": pr.get("geometry_kind", "point")})
    out_groups = []
    for u, g in sorted(groups.items(), key=lambda kv: -kv[1]["record_count"]):
        sig, seen = date_signal(g["article_text"],
                                __import__("datetime").date.fromisoformat(window["start"]),
                                __import__("datetime").date.fromisoformat(window["end"])) \
            if window.get("start") else ("none", [])
        g.update({"date_signal": sig, "dates_seen": seen,
                  "snippet_fallback": bool(snippets.get(u)),
                  "url_flags": [f for f in ("gov-host" if ".gov." in u else "",
                                            "no-slug" if not re.search(r"/\d|-|_", u) else "")
                                if f]})
        out_groups.append(g)
    print(json.dumps({"summary": {"total_records": len(feats),
                                  "unique_urls": len(groups),
                                  "window": window, "country": country},
                      "groups": out_groups}, indent=2)[:200000])

if __name__ == "__main__":
    main(sys.argv)
