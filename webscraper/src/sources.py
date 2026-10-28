"""Discovery sources: cse, gdelt, gnews_rss + Google-News URL resolver.

Mirrors src/sources.py. Network calls are isolated in fetch helpers so the
pipeline and tests can inject fakes.
"""
import html
import json
import re
import time
import urllib.parse
import urllib.request

GNEWS_ARTICLE_RE = re.compile(r"news\.google\.com/rss/articles/")


def redact(url_or_err: str) -> str:
    return re.sub(r"((?:key|api_key|token|sig)=)[^&\s]+", r"\1REDACTED", url_or_err or "")


def dedup_urls(items: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for it in items:
        u = (it.get("url") or "").strip()
        if u and u not in seen:
            seen[u] = it
    return list(seen.values())


class CSESource:
    name = "cse"

    def __init__(self, api_key="", engine_id="", search_depth=40, sleep_s=1.0, fetch=None):
        self.api_key = api_key
        self.engine_id = engine_id
        self.search_depth = search_depth
        self.sleep_s = sleep_s
        self._fetch = fetch or self._http

    def _http(self, url):
        with urllib.request.urlopen(url, timeout=20) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def search(self, query: str, start: str, end: str) -> list[dict]:
        out: list[dict] = []
        for offset in range(1, self.search_depth + 1, 10):
            params = urllib.parse.urlencode({
                "q": query, "key": self.api_key, "cx": self.engine_id,
                "sort": f"date:r:{start}:{end}", "start": offset, "num": 10})
            try:
                data = self._fetch(f"https://www.googleapis.com/customsearch/v1?{params}")
            except Exception as e:  # per-source failure isolated
                out.append({"error": redact(str(e))})
                break
            items = data.get("items", [])
            for it in items:
                out.append({"url": it.get("link", ""), "snippet": it.get("snippet", ""),
                            "source": "cse", "title": it.get("title", "")})
            time.sleep(self.sleep_s)
            if len(items) < 10:
                break
        return [o for o in out if "url" in o]


class GDELTSource:
    name = "gdelt"

    def __init__(self, max_queries=50, maxrecords=75, fetch=None):
        self.max_queries = max_queries
        self.maxrecords = maxrecords
        self._fetch = fetch or self._http

    def _http(self, url):
        with urllib.request.urlopen(url, timeout=20) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def search(self, queries: list[str], start: str, end: str) -> list[dict]:
        out, fails = [], 0
        for q in queries[: self.max_queries]:
            params = urllib.parse.urlencode({
                "query": q, "mode": "ArtList", "format": "json",
                "maxrecords": self.maxrecords,
                "startdatetime": start.replace("-", ""), "enddatetime": end.replace("-")})
            url = f"https://api.gdeltproject.org/api/v2/doc/doc?{params}"
            try:
                data = self._fetch(url)
                fails = 0
                for a in (data.get("articles") or []):
                    out.append({"url": a.get("url", ""), "snippet": a.get("title", ""),
                                "source": "gdelt", "title": a.get("title", "")})
            except Exception as e:
                out.append({"error": redact(str(e))})
                fails += 1
                if fails >= 5:  # give up after 5 consecutive failures (usually 429)
                    break
            time.sleep(5.0)
        return [o for o in out if "url" in o]


class GoogleNewsRSSSource:
    name = "gnews_rss"

    def __init__(self, lang="en-GB", country="GB", ce="GB:en", max_queries=150,
                 fetch_xml=None, resolve=True):
        self.lang, self.country, self.ce = lang, country, ce
        self.max_queries = max_queries
        self._fetch_xml = fetch_xml or self._http
        self.resolve = resolve

    def _http(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", "replace")

    def rss_url(self, query: str, start: str, end: str) -> str:
        q = f"{query} after:{start} before:{end}"
        return ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q)
                + f"&hl={self.lang}&gl={self.country}&ceid={self.ce}")

    def search(self, queries: list[str], start: str, end: str) -> list[dict]:
        out: list[dict] = []
        for q in queries[: self.max_queries]:
            try:
                xml = self._fetch_xml(self.rss_url(q, start, end))
            except Exception as e:
                out.append({"error": redact(str(e))})
                continue
            for m in re.finditer(r"<item>.*?</item>", xml, re.S):
                link = re.search(r"<link>(.*?)</link>", m.group(0), re.S)
                title = re.search(r"<title>(.*?)</title>", m.group(0), re.S)
                if link:
                    out.append({"url": html.unescape(link.group(1).strip()),
                                "title": html.unescape(title.group(1).strip()) if title else "",
                                "snippet": "", "source": "gnews_rss"})
            time.sleep(0.6)
        if self.resolve:
            out = resolve_gnews_urls(out)
        return [o for o in out if "url" in o]


def resolve_gnews_url(link: str, fetch=None) -> str:
    """Resolve a news.google.com/rss/articles/<id> link to the publisher URL.

    Mirrors _resolve_gnews_urls: GET article page with SOCS consent cookie,
    read data-n-a-sg / data-n-a-ts, POST garturlreq to batchexecute, extract
    URL after 'garturlres', strip trailing backslash, JSON-unescape.
    """
    if not GNEWS_ARTICLE_RE.search(link or ""):
        return link
    try:
        fetch = fetch or (lambda u, **k: b"")
        raw = fetch(link)
        page = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        sg = re.search(r'data-n-a-sg="([^"]+)"', page)
        ts = re.search(r'data-n-a-ts="([^"]+)"', page)
        if not (sg and ts):
            return link
        resolved = fetch("batchexecute", sg=sg.group(1), ts=ts.group(1))
        text = resolved.decode("utf-8", "replace") if isinstance(resolved, bytes) else resolved
        m = re.search(r"garturlres(.*)", text)
        if not m:
            return link
        um = re.search(r'"(https?://[^"]+)"', m.group(1))
        if not um:
            return link
        # bug fix 2026-10-05: strip trailing backslash then JSON-unescape
        return json.loads(f'"{um.group(1).rstrip(chr(92))}"')
    except Exception:
        return link


def resolve_gnews_urls(items: list[dict], fetch=None) -> list[dict]:
    from concurrent.futures import ThreadPoolExecutor
    g = [it for it in items if GNEWS_ARTICLE_RE.search(it.get("url", ""))]
    other = [it for it in items if not GNEWS_ARTICLE_RE.search(it.get("url", ""))]
    if not g:
        return items
    with ThreadPoolExecutor(max_workers=8) as ex:
        urls = list(ex.map(lambda it: resolve_gnews_url(it["url"], fetch), g))
    for it, u in zip(g, urls):
        it["url"] = u
    print(f"[gnews_rss] resolved {sum(1 for u in urls if not GNEWS_ARTICLE_RE.search(u))}/{len(g)}")
    return other + g


def build_sources(cfg, fetch=None) -> dict:
    lang, country, ce = cfg.rss_lang
    return {
        "cse": CSESource(fetch=fetch),
        "gdelt": GDELTSource(fetch=fetch),
        "gnews_rss": GoogleNewsRSSSource(lang=lang, country=country, ce=ce),
    }
