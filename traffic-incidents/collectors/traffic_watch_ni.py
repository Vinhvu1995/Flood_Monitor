"""
Traffic Watch NI live incidents.
Endpoint: https://rss.trafficwatchni.com/trafficwatchni_incidents_rss.xml
No authentication required. RSS updated every 5 minutes.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from models.incident import FloodSeverity, Incident, IncidentSource

_URL = "https://rss.trafficwatchni.com/trafficwatchni_incidents_rss.xml"
_FLOOD_KEYWORDS = {"flood", "flooding", "surface water", "standing water", "submerged"}


def _is_flood(title: str, description: str) -> bool:
    combined = f"{title} {description}".lower()
    return any(kw in combined for kw in _FLOOD_KEYWORDS)


def fetch() -> list[Incident]:
    incidents: list[Incident] = []
    try:
        response = httpx.get(_URL, timeout=15)
        response.raise_for_status()
        root = ET.fromstring(response.content)
    except (httpx.HTTPError, ET.ParseError) as exc:
        print(f"[traffic_watch_ni] error: {exc}")
        return incidents

    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        description = (item.findtext("description") or "").strip()

        if not _is_flood(title, description):
            continue

        guid = item.findtext("guid") or title
        pub_date_str = item.findtext("pubDate")
        reported_at = _parse_rss_date(pub_date_str) or datetime.now(timezone.utc)

        # RSS items don't carry coordinates — location is in the title/description text
        incidents.append(
            Incident(
                source=IncidentSource.TRAFFIC_WATCH_NI,
                external_id=guid,
                occurred_at=None,
                reported_at=reported_at,
                longitude=None,
                latitude=None,
                road_ref=None,
                description=f"{title}: {description}".strip(": "),
                is_flood=True,
                flood_severity=FloodSeverity.UNKNOWN,
                raw={"title": title, "description": description, "guid": guid, "pubDate": pub_date_str},
            )
        )

    return incidents


def _parse_rss_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None
