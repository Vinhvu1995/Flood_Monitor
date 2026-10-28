"""
TfL Unified API — Road Disruptions.
Returns disruptions whose category or description indicates flooding.
Docs: https://api.tfl.gov.uk/
Register for a free app key at: https://api-portal.tfl.gov.uk/
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from config.settings import settings
from models.incident import FloodSeverity, Incident, IncidentSource

_BASE = "https://api.tfl.gov.uk"
_FLOOD_KEYWORDS = {"flood", "flooding", "surface water", "standing water", "submerged"}


def _is_flood(category: str, comments: str) -> bool:
    combined = f"{category} {comments}".lower()
    return any(kw in combined for kw in _FLOOD_KEYWORDS)


def fetch() -> list[Incident]:
    """Fetch active road disruptions in London and return those related to flooding."""
    incidents: list[Incident] = []
    params: dict = {}
    if settings.tfl_app_key:
        params["app_key"] = settings.tfl_app_key

    try:
        response = httpx.get(
            f"{_BASE}/Road/all/Disruption",
            params=params,
            timeout=15,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as exc:
        print(f"[tfl] HTTP error: {exc}")
        return incidents

    for item in data:
        category = item.get("category", "") or ""
        comments = item.get("comments", "") or item.get("description", "") or ""
        if not _is_flood(category, comments):
            continue

        lat = item.get("geography", {}).get("lat") if item.get("geography") else None
        lon = item.get("geography", {}).get("lon") if item.get("geography") else None
        # Fallback to corridor coordinates if present
        if lat is None and item.get("corridor"):
            corridor = item["corridor"]
            lat = corridor.get("lat")
            lon = corridor.get("lon")

        reported_at = _parse_dt(item.get("startDateTime")) or datetime.now(timezone.utc)
        occurred_at = _parse_dt(item.get("startDateTime"))

        incidents.append(
            Incident(
                source=IncidentSource.TFL,
                external_id=str(item.get("id", "")),
                occurred_at=occurred_at,
                reported_at=reported_at,
                longitude=float(lon) if lon is not None else None,
                latitude=float(lat) if lat is not None else None,
                road_ref=item.get("point") or item.get("street"),
                description=comments or category,
                is_flood=True,
                flood_severity=FloodSeverity.UNKNOWN,
                raw=item,
            )
        )

    return incidents


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None
