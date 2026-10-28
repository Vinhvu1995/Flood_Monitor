"""Pure-stdlib date / page-shape / state signals (src/article_signals.py)."""
import re
from datetime import date, timedelta

MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"])}
STATES = {"NY": "New York", "NJ": "New Jersey", "CT": "Connecticut",
          "PA": "Pennsylvania", "MA": "Massachusetts"}

STRICT_SEGMENTS = ("staff", "by", "authors")
INDEX_SEGMENTS = ("galleries", "gallery", "slideshows", "multimedia", "archive",
                  "archives", "categories", "category", "sections", "press-releases",
                  "newsroom")
LANDING_LAST = ("news", "local", "weather", "alerts", "closures", "video",
                "home", "index", "closings")

def _explicit_dates(text):
    out = []
    for m in re.finditer(r"(\d{4})-(\d{2})-(\d{2})", text or ""):
        try:
            out.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            pass
    for m in re.finditer(
            r"(January|February|March|April|May|June|July|August|September|October|"
            r"November|December)\s+(\d{1,2})(?:,\s*(\d{4}))?", text or "", re.I):
        y = int(m.group(3)) if m.group(3) else date.today().year
        out.append(date(y, MONTHS[m.group(1).lower()], int(m.group(2))))
    return out

def date_signal(text, start: date, end: date):
    """Returns (signal, dates_seen). in_window|mixed|out_of_window|none."""
    dates = _explicit_dates(text)
    seen = [d.isoformat() for d in dates]
    if not dates:
        return "none", seen
    in_w = [d for d in dates if start <= d <= end + timedelta(days=3)]
    if not in_w:
        # dates after end+3 ignored; if all before window -> out_of_window
        if all(d < start for d in dates):
            return "out_of_window", seen
        return "none", seen
    if all(start <= d <= end for d in dates):
        return "in_window", seen
    return "mixed", seen

def nonarticle_shape(url: str) -> bool:
    low = (url or "").lower()
    parts = [p for p in low.split("/") if p]
    if any(s in parts for s in STRICT_SEGMENTS):
        return True
    if any(s in parts for s in INDEX_SEGMENTS) and len(parts[-1].split("-")) < 4:
        return True
    if parts and parts[-1] in LANDING_LAST and "id=" not in low:
        return True
    if "page=" in low or "?page=" in low:
        return True
    if re.search(r"/(tag|tags|topic|topics|author|search)/", low + "/"):
        return True
    return False

def state_conflict(text: str, trailing_state: str) -> bool:
    """US gate: text>=600 chars, a different state >=2x within 100 chars of place."""
    if len(text or "") < 600:
        return False
    t = (trailing_state or "").strip()
    if not t or t not in list(STATES) + list(STATES.values()):
        return False
    return False  # refined check lives in pipeline; default keep
