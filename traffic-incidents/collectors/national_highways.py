"""
National Highways Road & Lane Closures v2.0 API.
Returns DATEX II v3.4 XML. Filters situations whose cause or comment indicates flooding.

Requires a free API key — register at https://developer.data.nationalhighways.co.uk/
Set NH_API_KEY in your .env file.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx

from config.settings import settings
from models.incident import FloodSeverity, Incident, IncidentSource

_URL = "https://api.data.nationalhighways.co.uk/roads/v2.0/closures"
_FLOOD_KEYWORDS = {"flood", "flooding", "surface water", "standing water", "submerged"}

# DATEX II cause type values that indicate flooding
_FLOOD_CAUSE_TYPES = {"flooding", "heavyRainOrThunderstorm", "surfaceWater"}


def _is_flood(*texts: str) -> bool:
    combined = " ".join(texts).lower()
    return any(kw in combined for kw in _FLOOD_KEYWORDS) or any(ct in combined for ct in _FLOOD_CAUSE_TYPES)


def _text(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None else ""


def fetch() -> list[Incident]:
    incidents: list[Incident] = []
    if not settings.nh_api_key:
        print("[national_highways] NH_API_KEY not set — skipping (register free at https://developer.data.nationalhighways.co.uk/)")
        return incidents

    try:
        response = httpx.get(
            _URL,
            params={"closureType": "unplanned"},
            timeout=15,
            headers={
                "Accept": "application/xml",
                "Ocp-Apim-Subscription-Key": settings.nh_api_key,
            },
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)
    except (httpx.HTTPError, ET.ParseError) as exc:
        print(f"[national_highways] error: {exc}")
        return incidents

    for situation in root.findall("situation"):
        situation_id = _text(situation.find("idG"))

        # There may be multiple situationRecords inside one situation
        for record in situation.findall(".//situationRecord"):
            cause_type = _text(record.find(".//causeType"))
            comment = _text(record.find(".//generalPublicComment/comment"))
            location_desc = _text(record.find(".//locationDescription"))
            road_name = _text(record.find(".//roadName"))

            if not _is_flood(cause_type, comment, location_desc):
                continue

            record_id = _text(record.find("idG")) or situation_id
            created = _text(record.find("situationRecordCreationTime"))
            start = _text(record.find(".//overallStartTime"))

            reported_at = _parse_dt(created) or _parse_dt(start) or datetime.now(timezone.utc)
            occurred_at = _parse_dt(start)

            # Extract first coordinate pair from posList (lat lon lat lon ...)
            lat = lon = None
            pos_list = _text(record.find(".//posList"))
            if pos_list:
                parts = pos_list.split()
                if len(parts) >= 2:
                    try:
                        lat, lon = float(parts[0]), float(parts[1])
                    except ValueError:
                        pass

            incidents.append(
                Incident(
                    source=IncidentSource.NATIONAL_HIGHWAYS,
                    external_id=record_id,
                    occurred_at=occurred_at,
                    reported_at=reported_at,
                    longitude=lon,
                    latitude=lat,
                    road_ref=road_name or None,
                    description=comment or location_desc or cause_type,
                    is_flood=True,
                    flood_severity=FloodSeverity.UNKNOWN,
                    raw={"situation_id": situation_id, "cause_type": cause_type, "comment": comment},
                )
            )

    return incidents


def _parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None
