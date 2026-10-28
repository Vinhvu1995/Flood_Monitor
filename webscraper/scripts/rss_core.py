#!/usr/bin/env python3
"""Shared RSS poller core (uk/us_flood_rss.py): stdlib RSS/Atom + flood regex.

FEEDS lists differ per country; keep this core identical.
"""
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

FLOOD_RE = re.compile(r"flood(?!light)|flash[- ]?flood|deluge|torrential|downpour|"
                      r"submerged|inundat|waterlogged|burst.{0,12}bank|water rescue|"
                      r"swept away|washed away|surface water|in spate|standing water", re.I)
TRACK = re.compile(r"[?&](at_medium|at_campaign|utm_[^=&]*|ref)=[^&]*")


def clean_url(u: str) -> str:
    u = TRACK.sub("", u or "")
    return u.rstrip("?&")


def parse_pub_date(pub: str):
    """RFC 2822 or ISO to naive UTC datetime; None when unparseable."""
    pub = (pub or "").strip()
    if not pub:
        return None
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(pub)
        if dt is not None:
            return dt.replace(tzinfo=None)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(pub.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def fetch_feed(feed: tuple, since, fetch_xml=None) -> tuple[list, str | None]:
    source, region, url = feed
    try:
        if fetch_xml is not None:
            xml = fetch_xml(url)
            xml = xml.decode("utf-8", "replace") if isinstance(xml, bytes) else xml
        else:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                xml = r.read().decode("utf-8", "replace")
    except Exception as e:
        return [], f"{source}: {str(e)[:120]}"
    xml = re.sub(r"<(\w+):(item|entry|title|link|pubDate|published|updated)([\s>])", r"<\2\3", xml)
    xml = re.sub(r"</(\w+):(item|entry|title|link|pubDate|published|updated)>", r"</\2>", xml)
    items = re.findall(r"<item>.*?</item>|<entry>.*?</entry>", xml, re.S)
    hits = []
    for it in items:
        title = (re.search(r"<title>(.*?)</title>", it, re.S) or [None, ""])[1]
        link = (re.search(r"<link[^>]*>(.*?)</link>|<link[^>]*href=\"([^\"]+)\"", it, re.S) or [None, "", ""])[0]
        link = link or ((re.search(r"<link[^>]*href=\"([^\"]+)\"", it) or [None, ""])[1])
        pub = (re.search(r"<pubDate>(.*?)</pubDate>|<published>(.*?)</published>|<updated>(.*?)</updated>", it, re.S) or [None, ""])[1]
        if FLOOD_RE.search(re.sub(r"<[^>]+>", " ", title)):
            dt = parse_pub_date(pub)
            if since is not None and dt is not None and dt < since:
                continue  # outside the window
            hits.append({"source": source, "region": region, "title": re.sub(r"<[^>]+>", " ", title).strip(),
                         "url": clean_url(re.sub(r"<[^>]+>", "", link or "").strip()), "date": (pub or "").strip()})
    return hits, None


def run(feeds: list, since, as_json: bool):
    with ThreadPoolExecutor(max_workers=12) as ex:
        results = list(ex.map(lambda f: fetch_feed(f, since), feeds))
    hits, fails = [], []
    for h, e in results:
        hits += h
        if e:
            fails.append(e)
    seen, out = set(), []
    for h in sorted(hits, key=lambda x: x.get("date", ""), reverse=True):
        key = h["title"].lower().strip()
        if key not in seen:
            seen.add(key)
            out.append(h)
    if as_json:
        import json
        print(json.dumps({"hits": out, "failed_feeds": fails}, indent=2))
    else:
        for h in out:
            print(f"- {h['date']} [{h['source']}] {h['title']}\n  {h['url']}")
        if fails:
            print("FAILED FEEDS:", *fails, sep="\n  ")


def parse_since(argv) -> datetime:
    if "--since" in argv:
        return datetime.fromisoformat(argv[argv.index("--since") + 1])
    days = int(argv[argv.index("--days") + 1]) if "--days" in argv else 3
    if "--all" in argv:
        return datetime.min
    return datetime.now() - timedelta(days=days)
