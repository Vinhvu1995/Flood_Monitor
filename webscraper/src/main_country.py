"""THE pipeline (faithful scaffold of src/main_country.py).

Stages: window -> discovery (cse/gdelt/gnews_rss) -> scraping (EXCLUDED_DOMAINS) ->
LSR parse -> LLM extraction (gpt-5-nano, NO temperature) -> text gates ->
local geocoding tiers -> geometry gates -> enrichment -> S3 deferral -> run_summary.
All I/O is plain CSV/JSON; heavy deps (openai, geopandas) injectable/fallback.
"""
import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from country_config import get_country  # noqa: E402
from article_signals import date_signal, nonarticle_shape, state_conflict  # noqa: E402
from lsr_parser import parse_lsr, is_lsr_page, fetch_iem_lsr_records  # noqa: E402
from geocoding import geoencode_local  # noqa: E402

EXCLUDED_DOMAINS = ("facebook.com", "twitter.com", "x.com", "instagram.com",
                    "tiktok.com", "wikipedia.org", "metoffice.gov.uk", "weather.gov",
                    "wunderground.com", "mesonet.agron.iastate.edu", "waterdata.usgs.gov",
                    "ready.noaa.gov", "rightmove.co.uk", "zoopla.co.uk", "ypod.cymru")
EXCLUDED_PATH_HINTS = ("/flood-zone", "/flood-risk", "/flood-information", "/flood-map",
                       "/floodmap", "/civicalerts", "/consultation", "/engagement",
                       "/newsflash", "/preparedness", "/be-prepared", "/weather/alerts/")
SERVICE_PATHS = ("/roadworks", "/road-closure", "/service-update", "/service-status",
                 "/disruption", "/travel-updates", "/travel-alerts", "/timetable")
FOREIGN_TLD = {"UK": ("ie", "au", "ca", "nz", "us", "mil", "gov")}
FLOOD_NEAR_RE = re.compile(r"flood|flooded|flooding|submerged|inundat|burst|deluge|"
                           r"washed away|underwater|evacuat|swollen|torrent|waterlogged|"
                           r"downpour|overflow|rescued from", re.I)
HIST_RE_CACHE: dict[str, re.Pattern] = {}


def parse_args(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--country", required=True, help="UK|US|IT")
    p.add_argument("--force-trigger", action="store_true")
    p.add_argument("--threshold", type=float, default=5.0)
    p.add_argument("--model", default="gpt-5-nano")
    p.add_argument("--search-depth", type=int, default=40)
    p.add_argument("--start-date", default=None)
    p.add_argument("--duration", type=int, default=2)
    p.add_argument("--fname", default=None)
    return p.parse_args(argv)


def compute_window(cfg, args) -> tuple[str, str, str]:
    if args.start_date:
        s = args.start_date.replace("-", "")
        start = date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        end = start + timedelta(days=args.duration)
        return start.isoformat(), end.isoformat(), (args.fname or f"{s}_{cfg.code}_manual")
    end = date.today()
    return (end - timedelta(days=2)).isoformat(), end.isoformat(), f"auto_{cfg.code}"


def discovery_queries(cfg) -> list[str]:
    qs = list(cfg.discovery_queries)
    for seed in cfg.seeds:
        for t in cfg.query_templates:
            qs.append(t.format(loc=seed))
    seen, out = set(), []
    for q in qs:
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out


def skipped_scrape(url: str, tld_block) -> str | None:
    low = (url or "").lower()
    host = re.sub(r"^https?://", "", low).split("/")[0]
    if host.startswith("https://www.bbc.com/weather") or url.startswith("https://www.bbc.com/weather"):
        return "bbc_weather"
    if any(host == d or host.endswith("." + d) for d in EXCLUDED_DOMAINS):
        return "excluded_domain"
    if "fixmystreet" in host or "engagementhq" in host or "engage.environment-agency" in host:
        return "engagement"
    if tld_block and host.split(".")[-1] in tld_block:
        return "foreign_tld"
    path = "/" + "/".join(low.split("/")[3:]) if "/" in low else ""
    if any(h in path for h in EXCLUDED_PATH_HINTS):
        return "info_page"
    if any(h in path for h in SERVICE_PATHS):
        return "service_page"
    return None


def fetch_text(url: str, timeout=20) -> tuple[str, dict]:
    """Returns (text, meta). Meta: main_images, link_ratio heuristic."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except Exception as e:
        return "", {"error": str(e)[:200]}
    title = " ".join(re.findall(r"<title>(.*?)</title>", raw, re.S | re.I))[:300]
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.S | re.I)
    links = "".join(re.findall(r"<a[^>]*>(.*?)</a>", body, flags=re.S | re.I))
    text = re.sub(r"<[^>]+>", " ", body)
    text = re.sub(r"\s+", " ", title + " " + text).strip()
    ratio = len(links) / max(len(text), 1)
    images = parse_article_images(raw)
    return text, {"main_images": images, "link_ratio": ratio}


def parse_article_images(raw: str, limit: int = 3) -> list:
    """og:image + <img> tags with caption/alt/figure context (testable, no I/O)."""
    raw = raw or ""
    imgs = re.findall(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', raw, re.I)
    images = [{"url": u, "context": ""} for u in imgs[:limit]]
    seen = {u for u in imgs[:limit]}
    for m in re.finditer(r"<img\b[^>]*>", raw, re.I):
        if len(images) >= limit:
            break
        tag = m.group(0)
        src = re.search(r'''(?:data-src|src)=["']([^"']+)''', tag, re.I)
        if not src or not src.group(1).startswith("http"):
            continue
        if src.group(1) in seen:
            continue
        seen.add(src.group(1))
        alt = re.search(r'''alt=["']([^"']*)''', tag, re.I)
        title = re.search(r'''title=["']([^"']*)''', tag, re.I)
        ctx = " ".join(x for x in [
            alt.group(1) if alt else "", title.group(1) if title else ""] if x).strip()
        # enclosing <figure> text (captions live here, not in attributes)
        start = max(0, m.start() - 5000)
        fig_open = raw.rfind("<figure", start, m.start())
        if fig_open != -1:
            fig_close = raw.find("</figure>", m.end(), m.end() + 5000)
            fig = raw[fig_open: fig_close + 9 if fig_close != -1 else m.end()]
            fig_txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fig)).strip()
            ctx = (ctx + " " + fig_txt).strip()[:500]
        images.append({"url": src.group(1), "context": ctx})
    return images


def road_near_image(road: str, images) -> dict | None:
    """First image whose caption/alt/figure context names the road (else None)."""
    road = (road or "").split(",")[0].strip().lower()
    if not road:
        return None
    for im in images or []:
        if not isinstance(im, dict):
            continue
        if road and road in (im.get("context") or "").lower():
            return im
    return None


def download_image(url: str, timeout=15, max_bytes=10_000_000) -> bytes | None:
    """Download one article image. None on any failure (never raises)."""
    if not (url or "").startswith("http"):
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = (r.headers.get("Content-Type", "") or "").lower()
            if "image" not in ctype:
                return None
            data = r.read(max_bytes + 1)
            return data if data and len(data) <= max_bytes else None
    except Exception:
        return None


NON_PLACE = re.compile(
    r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|"
    r"January|February|March|April|May|June|July|August|September|October|"
    r"November|December|Storm|Flood|Flooding|Floods|After|Before|During|"
    r"United|South|North|East|West|New|Heavy|Severe|Flash|By)$", re.I)

# Site-chrome words: nav/footer/newsletter/promo text scraped with the article.
# A candidate made ONLY of these (or ≤2 chars) is contamination, not a place.
CHROME_WORDS = {
    "public", "newsletter", "newsletters", "jobs", "book", "books", "cars",
    "homes", "home", "local", "skip", "close", "next", "previous", "print",
    "full", "news", "star", "press", "guardian", "echo", "live", "traffic",
    "politics", "sport", "puzzles", "reader", "rewards", "archive",
    "newspaper", "newspapers", "notices", "notice", "reporter", "young",
    "pub", "federation", "signin", "sign", "login", "subscribe",
    "subscription", "menu", "search", "share", "follow",
    "advertisement", "cookie", "cookies", "privacy", "terms", "contact",
    # paper-name / section / nav words (never standalone places)
    "national", "telegraph", "mail", "express", "mirror", "metro", "record",
    "herald", "post", "gazette", "chronicle", "argus", "advertiser", "times",
    "sun", "courier", "journal", "observer", "standard", "independent",
    "evening", "morning", "weekly", "daily", "sunday",
    "features", "districts", "district", "dock", "shop", "contact", "more",
    "foundation", "gannett", "crime", "business", "farming", "housing",
    "education", "health", "travel", "weather", "emergency", "section",
    "the", "a", "an", "this", "that", "these", "those", "its", "their",
    "our", "your",
    # footer/nav/boilerplate words
    "galleries", "gallery", "picture", "pictures", "image", "images",
    "comments", "comment", "latest", "american", "journalists", "journalist",
    "independence", "economy", "fact", "check", "seven", "days", "day",
    "human", "hume", "hum",
}

def _word_is_chrome(w: str) -> bool:
    low = w.lower()
    if low in CHROME_WORDS:
        return True
    # truncated fragments from the 3-word window ("hom", "loc")
    return len(low) >= 3 and any(c.startswith(low) for c in CHROME_WORDS)

def is_chrome(name: str) -> bool:
    words = re.findall(r"[A-Za-z]+", name or "")
    if not words:
        return True
    if len("".join(words)) <= 2:
        return True
    return all(_word_is_chrome(w) for w in words)

def strip_chrome(name: str) -> str:
    """Remove chrome words from a compound candidate; "" when nothing remains."""
    kept = [w for w in (name or "").split() if not _word_is_chrome(w)]
    return " ".join(kept)

def heuristic_candidates(text: str, start: str, limit: int = 6) -> list[dict]:
    """Offline fallback: capitalized phrases within 120 chars of a flood
    keyword become candidate locations (deduped, capped). Storm event names
    and site chrome are filtered; chrome words are stripped from compounds."""
    text = text or ""
    storm_names = set()
    for sm in re.finditer(r"(?i)\bstorm\s+([A-Z][a-z]+(?:\s+and\s+[A-Z][a-z]+)?)", text):
        for w in re.findall(r"[A-Z][a-z]+", sm.group(1)):
            storm_names.add(w.lower())
    seen, out = set(), []
    for m in FLOOD_NEAR_RE.finditer(text):
        window = text[max(0, m.start() - 120): m.end() + 120]
        for ph in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b", window):
            name = ph.group(1)
            words = name.split()
            if any(NON_PLACE.match(w) for w in words):
                continue
            # author bylines ("By Chloe Boden"): skip what "By" introduces
            before = window[max(0, ph.start() - 5): ph.start()]
            if re.search(r"(?i)\bby\s+$", before):
                continue
            if any(w.lower() in storm_names for w in words):
                continue  # storm event name, not a place
            name = strip_chrome(name)
            if not name or is_chrome(name):
                continue
            key = name.lower()
            if key not in seen:
                seen.add(key)
                out.append({"name": name, "date": start, "context": window.strip()[:200]})
                if len(out) >= limit:
                    return out
    return out


def llm_params(model: str) -> dict:
    """gpt-5-nano: response_format + reasoning_effort, NO temperature (fails every request)."""
    if model.startswith(("o1", "o3", "o4")):
        return {"reasoning_effort": "medium"}
    if model.startswith("gpt-5"):
        if "nano" in model:
            return {"response_format": {"type": "json_object"}, "reasoning_effort": "medium"}
        return {"temperature": 0, "response_format": {"type": "json_object"},
                "reasoning_effort": "medium"}
    return {"temperature": 0, "response_format": {"type": "json_object"}}


def llm_extract(text: str, country: str, start: str, end: str, model="gpt-5-nano",
                client=None) -> dict:
    if client is None:
        try:
            from meta_llm import extract as _meta_extract
            hit = _meta_extract(text, country, start, end)
            if hit is not None:
                return hit
        except Exception:
            pass
    params = llm_params(model)
    prompt = (f"Decide if the article reports current flooding in {country} between "
              f"{start} and {end}. Exclusions: plumbing, forecasts, prevention, historical, "
              f"outside dates, warnings, outside country, advisories, service notices. "
              f"Extract explicitly named places at most specific granularity. "
              f"Output {{\"flood_event\": bool, \"locations\": [{{\"name\",\"date\",\"context\"}}]}}.\n{text[:6000]}")
    if client is None:
        # offline fallback: proper-noun candidates near flood keywords
        locs = heuristic_candidates(text, start) if FLOOD_NEAR_RE.search(text or "") else []
        return {"flood_event": bool(locs), "locations": locs}
    return client.extract(prompt, params)


def proximity_gate(location: str, text: str, window_chars: int = 120) -> bool:
    first = (location or "").split(",")[0].strip()
    if not first:
        return False
    for m in re.finditer(re.escape(first), text or "", re.I):
        near = (text or "")[max(0, m.start() - window_chars): m.end() + window_chars]
        if FLOOD_NEAR_RE.search(near):
            return True
    return False


def bad_source(url: str, country: str) -> str | None:
    low = (url or "").lower()
    if "tiktok" in low:
        return "tiktok"
    if any(d in low for d in ("maryevans.com", "yelp.com", "extraspace.com")):
        return "non_news"
    if re.search(r"/(tag|tags|topic|topics|author|search)/", low + "/"):
        return "index_segment"
    if nonarticle_shape(url) and "mesonet.agron" not in low and "nws.noaa" not in low:
        return "nonarticle"
    if country == "US" and re.search(r"\.gov\.uk|\.gov\.wales", low):
        return "uk_gov_in_us"
    if country != "US" and ("weather.gov" in low or "noaa.gov" in low):
        return "us_weather_abroad"
    if country in ("UK", "GB") and "centralbucksnews.com" in low:
        return "cross_country"
    return None


def run_pipeline(args, hooks=None) -> dict:
    """hooks: {discover(fn), fetch(fn), llm(fn)} for offline tests."""
    hooks = hooks or {}
    cfg = get_country(args.country)
    start, end, fname = compute_window(cfg, args)
    stamp = datetime.now().strftime("%Y%m%d%H")
    root = os.path.join("exports", start, f"Run_{stamp}", cfg.country_name)
    os.makedirs(os.path.join(root, "Logs"), exist_ok=True)
    os.makedirs(os.path.join(root, "Vectorised_Files"), exist_ok=True)

    summary = {"ran": True, "status": "in_progress", "country": cfg.code,
               "window": {"start": start, "end": end}, "model": args.model,
               "counts": {}, "gates": {}, "batch_warnings": []}

    def set_stage(stage):
        summary["stage"] = stage
        with open(os.path.join(root, "Logs", "run_summary.json"), "w") as f:
            json.dump(summary, f, indent=2)

    set_stage("scrape_and_geocode")
    # Step 1 discovery (production default: keyless RSS + GDELT + GNews;
    # CSE only when GOOGLE_API_KEY/SEARCH_ENGINE_ID resolve — never hardcoded)
    from sources import dedup_urls, build_sources
    _srcs = build_sources(cfg)
    def _default_discover(qs):
        found = []
        for name in ("rss", "gdelt", "gnews_rss"):
            try:
                found += _srcs[name].search(qs, start, end) or []
            except Exception as e:
                summary.setdefault("batch_warnings", []).append(f"{name}: {str(e)[:150]}")
        return found
    discover = hooks.get("discover") or _default_discover
    urls = discover(discovery_queries(cfg))
    urls = dedup_urls(urls)
    # Step 2 scrape
    fetch = hooks.get("fetch") or (lambda u: ("", {"error": "no-fetcher"}))
    rows, snippet_fallback, lsr_points = [], 0, []
    images_by_url: dict = {}
    llm_fn = hooks.get("llm")
    for it in urls:
        u = it.get("url", "")
        reason = skipped_scrape(u, FOREIGN_TLD.get(cfg.code, ()))
        if reason:
            continue
        text, meta = fetch_text(u) if fetch == "http" else fetch(u)
        if isinstance(meta, dict):
            images_by_url[u] = meta.get("main_images", []) or []
        if len(text) < 200:
            text = it.get("snippet", "") or text
            snippet_fallback += 1 if text else 0
        if not text:
            continue
        if is_lsr_page(u, text):
            for rec in parse_lsr(text):
                lsr_points.append({"name": rec.place_override or rec.county,
                                   "lat": rec.lat, "lon": rec.lon})
            continue
        sig, seen = date_signal(text, date.fromisoformat(start), date.fromisoformat(end))
        if sig == "out_of_window":
            summary["gates"]["date_guard_articles"] = summary["gates"].get("date_guard_articles", 0) + 1
            continue
        res = llm_fn(text) if llm_fn else llm_extract(text, cfg.country_name, start, end, args.model)
        if res.get("meta_model"):
            try:
                from meta_cost import record as _cost_record
                entry = _cost_record(res["meta_model"], res.get("meta_tokens"),
                                     run_id=fname)
                summary.setdefault("meta_cost_usd", 0.0)
                summary["meta_cost_usd"] = round(summary["meta_cost_usd"] + entry["cost"], 4)
                summary["meta_model"] = res["meta_model"]
            except Exception:
                pass
        for loc in (res.get("locations") or []):
            name = loc.get("name", "")
            if not proximity_gate(name, text):
                summary["gates"]["hallucination_locs"] = summary["gates"].get("hallucination_locs", 0) + 1
                continue
            if cfg.code == "US" and state_conflict(text, name.split(",")[-1].strip()):
                summary["gates"]["state_text_gate"] = summary["gates"].get("state_text_gate", 0) + 1
                continue
            rows.append({"url": u, "name": name, "date": loc.get("date", ""),
                         "context": loc.get("context", "")[:500]})
    # Step 5 geocode (local tiers)
    geo = geoencode_local([{"name": r["name"]} for r in rows],
                          target_epsg=cfg.target_epsg, region=cfg.code)
    kept = []
    for r, p in zip(rows, geo["points"]):
        if bad_source(r["url"], cfg.code):
            summary["gates"]["source_gate"] = summary["gates"].get("source_gate", 0) + 1
            continue
        kept.append({**r, **p})
    kept += [{"url": "", "name": p.get("name", ""), "confidence": 1.0,
              "location_type": "road", "geometry_kind": "point",
              "lat": p.get("lat"), "lon": p.get("lon")} for p in lsr_points]
    # Step 5a news-image SAM: road rows only, only when the road is named
    # near the picture (image caption/alt/figure context), capped per run.
    sam_cap = int(os.environ.get("SAM_IMAGE_CAP", 10))
    sam_checked = sam_flooded = 0
    for r in kept:
        r["sam_checked"] = False
        if r.get("location_type") != "road" or sam_checked >= sam_cap:
            continue
        road = (r.get("name", "") or "").split(",")[0].strip().lower()
        if not road:
            continue
        near = road_near_image(road, images_by_url.get(r.get("url", ""), []))
        if not near:
            continue
        try:
            from meta_cost import meta_allowed
            if not meta_allowed():
                break
        except Exception:
            pass
        img_bytes = download_image(near.get("url", ""))
        if not img_bytes:
            continue
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            "..", "..", "traffic-cameras"))
            from vision import sam_flood_check
        except Exception:
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", road)[:60] or "road"
        sidecar = os.path.join(root, "Logs", "sam_masks", f"{slug}-{sam_checked}")
        os.makedirs(os.path.dirname(sidecar), exist_ok=True)
        verdict = sam_flood_check(img_bytes, sidecar)
        r["sam_checked"] = bool(verdict.get("sam_ran"))
        r["sam_flooded"] = verdict.get("flooded")
        r["sam_water_fraction"] = verdict.get("water_fraction")
        r["sam_mask"] = verdict.get("mask_path")
        sam_checked += 1 if verdict.get("sam_ran") else 0
        sam_flooded += 1 if verdict.get("flooded") else 0
    summary["sam_images"] = {"checked": sam_checked, "flooded": sam_flooded, "cap": sam_cap}
    # Step 5b safety write + Step 9 write
    with open(os.path.join(root, "Vectorised_Files", f"points_{start.replace('-', '')}.geojson"), "w") as f:
        json.dump({"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": None, "properties": k} for k in kept]}, f)
    # IEM poll (recorded, never raised)
    try:
        iem = fetch_iem_lsr_records(list(cfg.nws_lsr_states), start, end) if cfg.nws_lsr_states else {"checked": False}
    except Exception as e:
        iem = {"checked": True, "failed": str(e)[:200]}
    summary.update({"status": "completed",
                    "counts": {"urls_discovered": len(urls), "articles_to_llm": len(rows),
                               "locations_extracted": len(rows),
                               "geometries": {"points": len(kept), "total": len(kept)}},
                    "s3_uri": f"s3://BUCKET/PREFIX/{cfg.country_name}/{start}/Run_{stamp}/",
                    "s3_vectors_deferred": True,
                    "snippet_fallback": {"count": snippet_fallback},
                    "lsr_parsed": {"iem": iem}})
    set_stage("write_vectors")
    return {"summary": summary, "root": root, "records": kept}


def main(argv=None):
    args = parse_args(argv)
    out = run_pipeline(args)
    print("RUN_SUMMARY: " + os.path.join(out["root"], "Logs", "run_summary.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
