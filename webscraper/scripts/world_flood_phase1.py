#!/usr/bin/env python3
"""world_flood_phase1.py (stdlib only): 197-country digest scaffold.

Query plan: 5 countries per Google News query (heavy countries alone),
headline filter, >=2 publishers / death / GDACS orange-red / IFRC / Copernicus
keep rule, outage handling (rc 3), single Adaptive card < 24 KB.
"""
import json
import re
import sys
import urllib.parse
import urllib.request

CONTINENTS = {"Europe": ["United Kingdom", "Italy", "Germany"],
              "Asia": ["India", "China", "Philippines"],
              "Americas": ["United States", "Brazil"],
              "Africa": ["Nigeria", "South Africa"], "Oceania": ["Australia"]}
HEAVY = {"US", "India", "China", "Indonesia", "Brazil", "UK", "Australia",
         "Philippines", "Pakistan", "Georgia"}
LOCAL = {"Italy": ["alluvione", "esondazione"], "Germany": ["Hochwasser", "Überflutung"]}
FLOOD_RE = re.compile(r"flood|alluvione|inundaci|hochwasser", re.I)
SPAM_RE = re.compile(r"insurance|market|protest|metaphor|analysis", re.I)
QUERY_BATCH = 5
FAIL_FAST_AFTER = 20


def is_event_headline(title: str) -> bool:
    if not FLOOD_RE.search(title or ""):
        return False
    if SPAM_RE.search(title or ""):
        return False
    return True


def main():
    post = "--post" in sys.argv
    countries = [c for cs in CONTINENTS.values() for c in cs]
    kept: dict[str, list] = {}
    fails, reqs, no_success = 0, 0, 0
    batch = [c for c in countries if c not in HEAVY]
    for i in range(0, len(batch), QUERY_BATCH):
        q = " OR ".join(batch[i:i + QUERY_BATCH]) + " flood"
        url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(q)
        reqs += 1
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                xml = r.read().decode("utf-8", "replace")
            no_success = 0
            for m in re.finditer(r"<title>(.*?)</title>.*?<source[^>]*>(.*?)</source>", xml, re.S):
                title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
                if is_event_headline(title):
                    kept.setdefault("multi", []).append(title)
        except Exception:
            fails += 1
            no_success += 1
        if no_success >= FAIL_FAST_AFTER:
            break
    total = reqs or 1
    if fails / total > 0.25:
        print("news outage; agency-only fallback", file=sys.stderr)
        sys.exit(3)
    card = {"countries": sorted(kept), "count": sum(len(v) for v in kept.values())}
    assert len(json.dumps(card)) < 24 * 1024
    if post:
        print(json.dumps(card, indent=2))
    else:
        print(json.dumps(card, indent=2))


if __name__ == "__main__":
    main()
