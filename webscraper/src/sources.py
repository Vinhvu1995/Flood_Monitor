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
                "startdatetime": start.replace("-", ""), "enddatetime": end.replace("-", "")})
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


UK_RSS_FEEDS = [
    ('Guardian (flooding topic)', 'UK/national', 'https://www.theguardian.com/environment/flooding/rss'),
    ('Guardian UK', 'UK/national', 'https://www.theguardian.com/uk/rss'),
    ('BBC UK', 'UK/national', 'https://feeds.bbci.co.uk/news/rss.xml'),
    ('Independent UK', 'UK/national', 'https://www.independent.co.uk/news/uk/rss'),
    ('Sky News UK', 'UK/national', 'https://feeds.skynews.com/feeds/rss/uk.xml'),
    ('Mirror', 'UK/national', 'https://www.mirror.co.uk/news/?service=rss'),
    ('Daily Mail', 'UK/national', 'https://www.dailymail.co.uk/news/index.rss'),
    ('Express UK', 'UK/national', 'https://www.express.co.uk/posts/rss/1/uk'),
    ('Metro', 'UK/national', 'https://metro.co.uk/news/feed/'),
    ('i (inews)', 'UK/national', 'https://inews.co.uk/feed'),
    ('Evening Standard', 'London', 'https://www.standard.co.uk/rss'),
    ('BBC Scotland', 'Scotland', 'https://feeds.bbci.co.uk/news/scotland/rss.xml'),
    ('BBC Edinburgh/East/Fife', 'Scotland/Edinburgh', 'https://feeds.bbci.co.uk/news/scotland/edinburgh_east_and_fife/rss.xml'),
    ('BBC Glasgow & West', 'Scotland/Glasgow', 'https://feeds.bbci.co.uk/news/scotland/glasgow_and_west/rss.xml'),
    ('Edinburgh Live', 'Scotland/Edinburgh', 'https://www.edinburghlive.co.uk/news/edinburgh-news/?service=rss'),
    ('The Herald', 'Scotland', 'https://www.heraldscotland.com/news/rss/'),
    ('The National', 'Scotland', 'https://www.thenational.scot/news/rss/'),
    ('Edinburgh Evening News', 'Scotland/Edinburgh', 'https://www.edinburghnews.scotsman.com/rss'),
    ('Daily Record', 'Scotland', 'https://www.dailyrecord.co.uk/news/?service=rss'),
    ('Glasgow Live', 'Scotland/Glasgow', 'https://glasgowlive.co.uk/news/?service=rss'),
    ('Manchester Evening News', 'NW England', 'https://www.manchestereveningnews.co.uk/news/?service=rss'),
    ('Liverpool Echo', 'NW England', 'https://www.liverpoolecho.co.uk/news/?service=rss'),
    ('Lancashire Telegraph', 'NW England', 'https://www.lancashiretelegraph.co.uk/news/rss/'),
    ('News & Star (Cumbria/Carlisle)', 'Cumbria', 'https://www.newsandstar.co.uk/news/rss/'),
    ('Yorkshire Post', 'Yorkshire', 'https://www.yorkshirepost.co.uk/rss'),
    ('Yorkshire Evening Post', 'Yorkshire/Leeds', 'https://www.yorkshireeveningpost.co.uk/rss'),
    ('York Press', 'Yorkshire/York', 'https://www.yorkpress.co.uk/news/rss/'),
    ('ChronicleLive (Newcastle)', 'NE England', 'https://www.chroniclelive.co.uk/news/?service=rss'),
    ('WalesOnline', 'Wales', 'https://www.walesonline.co.uk/news/?service=rss'),
    ('BBC Wales', 'Wales', 'https://feeds.bbci.co.uk/news/wales/rss.xml'),
    ('Cornwall Live', 'SW England', 'https://www.cornwalllive.com/news/?service=rss'),
    ('Devon Live', 'SW England', 'https://www.devonlive.com/news/?service=rss'),
    ('Somerset Live', 'SW England', 'https://www.somersetlive.co.uk/news/?service=rss'),
    ('Gloucestershire Live', 'SW England/Severn', 'https://www.gloucestershirelive.co.uk/news/?service=rss'),
    ('Shropshire Star', 'Midlands/Severn', 'https://www.shropshirestar.com/news/rss/'),
    ('Express & Star (Wolverhampton)', 'Midlands', 'https://www.expressandstar.com/news/rss/'),
    ('Birmingham Mail', 'Midlands', 'https://www.birminghammail.co.uk/news/?service=rss'),
    ('Nottingham Post', 'Midlands/Trent', 'https://www.nottinghampost.com/news/?service=rss'),
    ('BBC England', 'England', 'https://feeds.bbci.co.uk/news/england/rss.xml'),
    ('BBC News NI', 'Northern Ireland', 'https://feeds.bbci.co.uk/news/northern_ireland/rss.xml'),
    ('Belfast Live', 'Northern Ireland', 'https://www.belfastlive.co.uk/news/?service=rss'),
    ('Belfast Telegraph', 'Northern Ireland', 'https://www.belfasttelegraph.co.uk/rss/'),
    ('Hull Daily Mail', 'Yorkshire/Humber', 'https://www.hulldailymail.co.uk/news/?service=rss'),
    ('Grimsby Telegraph', 'Lincolnshire/Humber', 'https://www.grimsbytelegraph.co.uk/news/?service=rss'),
    ('Lincolnshire Live', 'Lincolnshire', 'https://www.lincolnshirelive.co.uk/news/?service=rss'),
    ('Leicester Mercury', 'East Midlands', 'https://www.leicestermercury.co.uk/news/?service=rss'),
    ('Derbyshire Live', 'East Midlands/Trent', 'https://www.derbytelegraph.co.uk/news/?service=rss'),
    ('Stoke Sentinel', 'Staffordshire', 'https://www.stokesentinel.co.uk/news/?service=rss'),
    ('Coventry Live', 'West Midlands', 'https://www.coventrytelegraph.net/news/?service=rss'),
    ('Kent Live', 'Kent', 'https://www.kentlive.news/news/?service=rss'),
    ('Essex Live', 'Essex', 'https://www.essexlive.news/news/?service=rss'),
    ('Cambridge News', 'Cambridgeshire/Fens', 'https://www.cambridge-news.co.uk/news/?service=rss'),
    ('Peterborough Telegraph', 'Cambridgeshire/Nene', 'https://www.peterboroughtoday.co.uk/rss'),
    ('Northants Telegraph', 'Northamptonshire/Nene', 'https://www.northantstelegraph.co.uk/rss'),
    ('Eastern Daily Press', 'Norfolk', 'https://www.edp24.co.uk/news/rss/'),
    ('Ipswich Star', 'Suffolk', 'https://www.ipswichstar.co.uk/news/rss/'),
    ('Southern Daily Echo', 'Hampshire', 'https://www.dailyecho.co.uk/news/rss/'),
    ('Basingstoke Gazette', 'Hampshire', 'https://www.basingstokegazette.co.uk/news/rss/'),
    ('The Argus', 'Sussex', 'https://www.theargus.co.uk/news/rss/'),
    ('Reading Chronicle', 'Berkshire/Thames Valley', 'https://www.readingchronicle.co.uk/news/rss/'),
    ('Oxford Mail', 'Oxfordshire/Thames', 'https://www.oxfordmail.co.uk/news/rss/'),
    ('Swindon Advertiser', 'Wiltshire', 'https://www.swindonadvertiser.co.uk/news/rss/'),
    ('Wiltshire Times', 'Wiltshire', 'https://www.wiltshiretimes.co.uk/news/rss/'),
    ('Bournemouth Echo', 'Dorset', 'https://www.bournemouthecho.co.uk/news/rss/'),
    ('Dorset Echo', 'Dorset', 'https://www.dorsetecho.co.uk/news/rss/'),
    ('Bristol Live', 'Bristol/SW', 'https://www.bristolpost.co.uk/news/?service=rss'),
    ('Plymouth Live', 'Devon/SW', 'https://www.plymouthherald.co.uk/news/?service=rss'),
    ('Worcester News', 'Worcestershire/Severn', 'https://www.worcesternews.co.uk/news/rss/'),
    ('Hereford Times', 'Herefordshire/Wye-Severn', 'https://www.herefordtimes.com/news/rss/'),
    ('North Wales Live', 'North Wales', 'https://www.dailypost.co.uk/news/?service=rss'),
    ('Aberdeen Live', 'Scotland/NE', 'https://www.aberdeenlive.news/news/?service=rss'),
    ('Met Office Warnings', 'UK/weather-gov', 'https://www.metoffice.gov.uk/public/data/PWSCache/WarningsRSS/Region/UK'),
    ('Environment Agency', 'UK/flood-gov', 'https://www.gov.uk/government/organisations/environment-agency.atom'),
]

US_RSS_FEEDS = [
    ('Reuters Top', 'US/national', 'https://feeds.feedburner.com/reuters/topNews'),
    ('Reuters US Domestic', 'US/national', 'https://feeds.feedburner.com/reuters/domesticNews'),
    ('Reuters Environment', 'US/climate', 'https://feeds.feedburner.com/reuters/environment'),
    ('NBC News Top', 'US/national', 'https://feeds.nbcnews.com/nbcnews/public/news'),
    ('ABC News US', 'US/national', 'https://abcnews.go.com/abcnews/usheadlines'),
    ('CBS News US', 'US/national', 'https://www.cbsnews.com/latest/rss/us'),
    ('CBS News Weather', 'US/weather', 'https://www.cbsnews.com/latest/rss/weather'),
    ('NPR News', 'US/national', 'https://feeds.npr.org/1001/rss.xml'),
    ('NPR Environment', 'US/climate', 'https://feeds.npr.org/1025/rss.xml'),
    ('Axios', 'US/national', 'https://api.axios.com/feed/'),
    ('Guardian US', 'US/national', 'https://www.theguardian.com/us-news/rss'),
    ('Guardian Environment', 'US/climate', 'https://www.theguardian.com/environment/rss'),
    ('Washington Post National', 'US/national', 'https://feeds.washingtonpost.com/rss/national'),
    ('NYT US', 'US/national', 'https://rss.nytimes.com/services/xml/rss/nyt/US.xml'),
    ('NYT Weather', 'US/weather', 'https://rss.nytimes.com/services/xml/rss/nyt/Weather.xml'),
    ('NYT Climate', 'US/climate', 'https://rss.nytimes.com/services/xml/rss/nyt/Climate.xml'),
    ('The Hill', 'US/national', 'https://thehill.com/homenews/feed/'),
    ('PBS NewsHour Nation', 'US/national', 'https://www.pbs.org/newshour/feeds/rss/nation'),
    ('NWS Flood Warnings', 'US/flood-gov', 'https://api.weather.gov/alerts/active.atom?event=Flood%20Warning'),
    ('NWS Flash Flood Warnings', 'US/flood-gov', 'https://api.weather.gov/alerts/active.atom?event=Flash%20Flood%20Warning'),
    ('NHC Atlantic', 'US/tropical-gov', 'https://www.nhc.noaa.gov/index-at.xml'),
    ('NOAA SPC', 'US/severe-gov', 'https://www.spc.noaa.gov/products/spcrss.xml'),
    ('Inside Climate News', 'US/climate', 'https://insideclimatenews.org/feed/'),
    ('Grist', 'US/climate', 'https://grist.org/feed/'),
    ('NJ.com', 'US/NJ', 'https://www.nj.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('Syracuse.com', 'US/NY', 'https://www.syracuse.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('PennLive', 'US/PA', 'https://www.pennlive.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('MassLive', 'US/MA', 'https://www.masslive.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('Philadelphia Inquirer', 'US/PA', 'https://www.inquirer.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('Pittsburgh Post-Gazette', 'US/PA', 'https://www.post-gazette.com/rss/'),
    ('Baltimore Banner', 'US/MD', 'https://www.thebaltimorebanner.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('PIX11', 'US/NY-NYC', 'https://pix11.com/feed/'),
    ('abc7NY', 'US/NY-NYC', 'https://abc7ny.com/feed/'),
    ('NBC New York', 'US/NY-NYC', 'https://www.nbcnewyork.com/?rss=y'),
    ('Fox 5 NY', 'US/NY-NYC', 'https://www.fox5ny.com/rss/category/news'),
    ('WNYT Albany', 'US/NY', 'https://www.wnyt.com/feed/'),
    ('LocalSYR', 'US/NY', 'https://www.localsyr.com/feed/'),
    ('RochesterFirst', 'US/NY', 'https://www.rochesterfirst.com/feed/'),
    ('WFSB Hartford', 'US/CT', 'https://www.wfsb.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('WTNH News8', 'US/CT', 'https://www.wtnh.com/feed/'),
    ('NBC Connecticut', 'US/CT', 'https://www.nbcconnecticut.com/?rss=y'),
    ('6abc Philadelphia', 'US/PA', 'https://6abc.com/feed/'),
    ('NBC10 Philadelphia', 'US/PA', 'https://www.nbcphiladelphia.com/?rss=y'),
    ('Fox 29 Philadelphia', 'US/PA', 'https://www.fox29.com/rss/category/news'),
    ('WHYY', 'US/PA', 'https://whyy.org/feed/'),
    ('NBC10 Boston', 'US/MA', 'https://www.nbcboston.com/?rss=y'),
    ('WCAX Burlington', 'US/VT', 'https://www.wcax.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('VTDigger', 'US/VT', 'https://vtdigger.org/feed/'),
    ('NHPR', 'US/NH', 'https://www.nhpr.org/rss.xml'),
    ('WAVY Hampton Roads', 'US/VA', 'https://www.wavy.com/feed/'),
    ('WRIC Richmond', 'US/VA', 'https://www.wric.com/feed/'),
    ('NBC12 Richmond', 'US/VA', 'https://www.nbc12.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('WSLS Roanoke', 'US/VA', 'https://www.wsls.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('WDBJ7 Roanoke', 'US/VA', 'https://www.wdbj7.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('WHSV Harrisonburg', 'US/VA', 'https://www.whsv.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('Cardinal News', 'US/VA', 'https://cardinalnews.org/feed/'),
    ('WRAL Raleigh', 'US/NC', 'https://www.wral.com/news/rss/142/'),
    ('WBTV Charlotte', 'US/NC', 'https://www.wbtv.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('WECT Wilmington', 'US/NC', 'https://www.wect.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('WPRI Providence', 'US/RI', 'https://www.wpri.com/feed/'),
    ('Concord Monitor', 'US/NH', 'https://www.concordmonitor.com/feed'),
    ('Spotlight Delaware', 'US/DE', 'https://spotlightdelaware.org/feed/'),
    ('Cleveland.com', 'US/OH', 'https://www.cleveland.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('WV MetroNews', 'US/WV', 'https://wvmetronews.com/feed/'),
    ('Arkansas Democrat-Gazette', 'US/AR', 'https://www.arkansasonline.com/rss/headlines/'),
    ('Chicago Sun-Times', 'US/IL', 'https://chicago.suntimes.com/rss/index.xml'),
    ('WCMH NBC4 Columbus', 'US/OH', 'https://www.nbc4i.com/feed/'),
    ('WISH-TV Indianapolis', 'US/IN', 'https://www.wishtv.com/feed/'),
    ('Fox59 Indianapolis', 'US/IN', 'https://fox59.com/feed/'),
    ('WANE Fort Wayne', 'US/IN', 'https://www.wane.com/feed/'),
    ('WEHT Evansville', 'US/IN', 'https://www.tristatehomepage.com/feed/'),
    ('WKRN Nashville', 'US/TN', 'https://www.wkrn.com/feed/'),
    ('WATE Knoxville', 'US/TN', 'https://www.wate.com/feed/'),
    ('WREG Memphis', 'US/TN', 'https://wreg.com/feed/'),
    ('FOX2 St. Louis', 'US/MO', 'https://fox2now.com/feed/'),
    ('KOLR Springfield', 'US/MO', 'https://www.ozarksfirst.com/feed/'),
    ('WHO13 Des Moines', 'US/IA', 'https://who13.com/feed/'),
    ('WGN Chicago', 'US/IL', 'https://wgntv.com/feed/'),
    ('WCIA Champaign', 'US/IL', 'https://www.wcia.com/feed/'),
    ('KARK Little Rock', 'US/AR', 'https://www.kark.com/feed/'),
    ('KNWA Fayetteville', 'US/AR', 'https://www.nwahomepage.com/feed/'),
    ('WSAZ Huntington-Charleston', 'US/WV', 'https://www.wsaz.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('WAVE Louisville', 'US/KY', 'https://www.wave3.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('WKYT Lexington', 'US/KY', 'https://www.wkyt.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('WSMV Nashville', 'US/TN', 'https://www.wsmv.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('WVLT Knoxville', 'US/TN', 'https://www.wvlt.tv/arc/outboundfeeds/rss/?outputType=xml'),
    ('WMC Memphis', 'US/TN', 'https://www.actionnews5.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('KMOV St. Louis', 'US/MO', 'https://www.firstalert4.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('KCTV Kansas City', 'US/MO', 'https://www.kctv5.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('KY3 Springfield', 'US/MO', 'https://www.ky3.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('KCRG Cedar Rapids', 'US/IA', 'https://www.kcrg.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('KAIT Jonesboro', 'US/AR', 'https://www.kait8.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('WHIO Dayton', 'US/OH', 'https://www.whio.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('WKRC Cincinnati', 'US/OH', 'https://local12.com/news/local.rss'),
    ('WCHS Charleston WV', 'US/WV', 'https://wchstv.com/news/local.rss'),
    ('KATV Little Rock', 'US/AR', 'https://katv.com/news/local.rss'),
    ('WCPO Cincinnati', 'US/OH', 'https://www.wcpo.com/index.rss'),
    ('WLEX Lexington', 'US/KY', 'https://www.lex18.com/index.rss'),
    ('WRTV Indianapolis', 'US/IN', 'https://www.wrtv.com/index.rss'),
    ('St. Louis Post-Dispatch', 'US/MO', 'https://www.stltoday.com/search/?f=rss&t=article&c=news/local&l=50&s=start_time&sd=desc'),
    ('LA Times Local', 'US/CA', 'https://www.latimes.com/local/rss2.0.xml'),
    ('LA Times California', 'US/CA', 'https://www.latimes.com/california/rss2.0.xml'),
    ('SFGate Bay Area', 'US/CA', 'https://www.sfgate.com/bayarea/feed/Bay-Area-News-429.php'),
    ('ABC7 LA', 'US/CA', 'https://abc7.com/feed/'),
    ('KTLA', 'US/CA', 'https://ktla.com/feed/'),
    ('FOX40 Sacramento', 'US/CA', 'https://fox40.com/feed/'),
    ('Seattle Times', 'US/WA', 'https://www.seattletimes.com/feed/'),
    ('Spokesman-Review', 'US/WA', 'https://www.spokesman.com/feeds/stories/'),
    ('The Oregonian', 'US/OR', 'https://www.oregonlive.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('KOIN Portland', 'US/OR', 'https://www.koin.com/feed/'),
    ('KGUN Tucson', 'US/AZ', 'https://www.kgun9.com/index.rss'),
    ('Las Vegas Review-Journal', 'US/NV', 'https://www.reviewjournal.com/feed/'),
    ('KLAS Las Vegas', 'US/NV', 'https://www.8newsnow.com/feed/'),
    ('KRQE Albuquerque', 'US/NM', 'https://www.krqe.com/feed/'),
    ('KOB Albuquerque', 'US/NM', 'https://www.kob.com/feed/'),
    ('Santa Fe New Mexican', 'US/NM', 'https://www.santafenewmexican.com/search/?f=rss&t=article&c=news&l=50&s=start_time&sd=desc'),
    ('Colorado Sun', 'US/CO', 'https://coloradosun.com/feed/'),
    ('KDVR Denver', 'US/CO', 'https://kdvr.com/feed/'),
    ('KREX Grand Junction', 'US/CO', 'https://www.westernslopenow.com/feed/'),
    ('Salt Lake Tribune', 'US/UT', 'https://www.sltrib.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('Deseret News', 'US/UT', 'https://www.deseret.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('FOX13 Salt Lake', 'US/UT', 'https://www.fox13now.com/index.rss'),
    ('KSL Salt Lake', 'US/UT', 'https://www.ksl.com/rss/news/'),
    ('KTVQ Billings', 'US/MT', 'https://www.ktvq.com/index.rss'),
    ('KPAX Missoula', 'US/MT', 'https://www.kpax.com/index.rss'),
    ('KIVI Boise', 'US/ID', 'https://www.kivitv.com/index.rss'),
    ('Oil City News', 'US/WY', 'https://oilcity.news/feed/'),
    ('County 17', 'US/WY', 'https://county17.com/feed/'),
    ('WyoFile', 'US/WY', 'https://wyofile.com/feed/'),
    ('KLKN Lincoln', 'US/NE', 'https://www.klkntv.com/feed/'),
    ('KSN Wichita', 'US/KS', 'https://www.ksn.com/feed/'),
    ('KFOR Oklahoma City', 'US/OK', 'https://kfor.com/feed/'),
    ('KSAT San Antonio', 'US/TX', 'https://www.ksat.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('KSAT Weather', 'US/TX', 'https://www.ksat.com/arc/outboundfeeds/rss/category/weather/?outputType=xml'),
    ('KXAN Austin', 'US/TX', 'https://www.kxan.com/feed/'),
    ('KTAB Abilene', 'US/TX', 'https://www.bigcountryhomepage.com/feed/'),
    ('Texas Tribune', 'US/TX', 'https://www.texastribune.org/feeds/main/'),
    ('KPRC Houston', 'US/TX', 'https://www.click2houston.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('abc13 Houston', 'US/TX', 'https://abc13.com/feed/'),
    ('Houston Public Media', 'US/TX', 'https://www.houstonpublicmedia.org/feed/'),
    ('KRIS Corpus Christi', 'US/TX', 'https://www.kristv.com/index.rss'),
    ('WAFB Baton Rouge', 'US/LA', 'https://www.wafb.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('WVUE Fox8 New Orleans', 'US/LA', 'https://www.fox8live.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('KLFY Lafayette', 'US/LA', 'https://www.klfy.com/feed/'),
    ('KATC Lafayette', 'US/LA', 'https://www.katc.com/index.rss'),
    ('KSLA Shreveport', 'US/LA', 'https://www.ksla.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('KPLC Lake Charles', 'US/LA', 'https://www.kplctv.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('WLBT Jackson', 'US/MS', 'https://www.wlbt.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('WJTV Jackson', 'US/MS', 'https://www.wjtv.com/feed/'),
    ('WLOX Biloxi-Gulfport', 'US/MS', 'https://www.wlox.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('Mississippi Today', 'US/MS', 'https://mississippitoday.org/feed/'),
    ('AL.com', 'US/AL', 'https://www.al.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('WBRC Birmingham', 'US/AL', 'https://www.wbrc.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('WSFA Montgomery', 'US/AL', 'https://www.wsfa.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('WKRG Mobile', 'US/AL', 'https://www.wkrg.com/feed/'),
    ('WALA Fox10 Mobile', 'US/AL', 'https://www.fox10tv.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('Tampa Bay Times', 'US/FL', 'https://www.tampabay.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('WFLA Tampa', 'US/FL', 'https://www.wfla.com/feed/'),
    ('WKMG Orlando', 'US/FL', 'https://www.clickorlando.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('WPTV West Palm Beach', 'US/FL', 'https://www.wptv.com/index.rss'),
    ('WSVN Miami', 'US/FL', 'https://wsvn.com/feed/'),
    ('WJXT Jacksonville', 'US/FL', 'https://www.news4jax.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('WCTV Tallahassee', 'US/FL', 'https://www.wctv.tv/arc/outboundfeeds/rss/?outputType=xml'),
    ('WMBB Panama City', 'US/FL', 'https://www.mypanhandle.com/feed/'),
    ('FOX5 Atlanta', 'US/GA', 'https://www.fox5atlanta.com/rss/category/news'),
    ('WSAV Savannah', 'US/GA', 'https://www.wsav.com/feed/'),
    ('WTOC Savannah', 'US/GA', 'https://www.wtoc.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('WRDW Augusta', 'US/GA', 'https://www.wrdw.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('WALB Albany GA', 'US/GA', 'https://www.walb.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('Live5News Charleston', 'US/SC', 'https://www.live5news.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('WCBD Charleston', 'US/SC', 'https://www.counton2.com/feed/'),
    ('WIS Columbia', 'US/SC', 'https://www.wistv.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('WSPA Greenville', 'US/SC', 'https://www.wspa.com/feed/'),
    ('WMBF Myrtle Beach', 'US/SC', 'https://www.wmbfnews.com/arc/outboundfeeds/rss/?outputType=xml'),
]

COUNTRY_RSS_FEEDS = {"UK": UK_RSS_FEEDS, "US": US_RSS_FEEDS, "IT": []}


class RSSSource:
    """Keyless first-class discovery: poller feed lists, flood-regex titles."""

    name = "rss"

    def __init__(self, feeds=None, fetch_xml=None):
        self.feeds = feeds or []
        self._fetch_xml = fetch_xml

    def search(self, queries, start: str, end: str) -> list[dict]:
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "..", "scripts"))
        from rss_core import fetch_feed  # noqa
        from datetime import datetime
        try:
            since = datetime.fromisoformat(start)
        except ValueError:
            since = None
        out: list[dict] = []
        for feed in self.feeds:
            try:
                hits, _ = fetch_feed(feed, since, fetch_xml=self._fetch_xml)
                for h in hits:
                    out.append({"url": h["url"], "snippet": h["title"],
                                "source": "rss", "title": h["title"]})
            except Exception as e:  # per-feed failure isolated
                out.append({"error": redact(str(e))})
        return [o for o in out if "url" in o]


def build_sources(cfg, fetch=None) -> dict:
    lang, country, ce = cfg.rss_lang
    feeds = list(COUNTRY_RSS_FEEDS.get(cfg.code, []))
    if cfg.code == "IT":
        feeds = [("BBC Europe", "Europe", "https://feeds.bbci.co.uk/news/world/europe/rss.xml")]
    return {
        "cse": CSESource(fetch=fetch),
        "gdelt": GDELTSource(fetch=fetch),
        "gnews_rss": GoogleNewsRSSSource(lang=lang, country=country, ce=ce),
        "rss": RSSSource(feeds=feeds, fetch_xml=fetch),
    }
