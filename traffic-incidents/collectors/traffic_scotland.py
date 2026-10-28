"""
Traffic Scotland live incidents.
Endpoint: https://www.traffic.gov.scot/tsis/incidents
No authentication required. Returns JSON with lat/lng and incident type.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from models.incident import FloodSeverity, Incident, IncidentSource

_URL = "https://www.traffic.gov.scot/tsis/incidents"
_FLOOD_KEYWORDS = {"flood", "flooding", "surface water", "standing water", "submerged"}


def _is_flood(incident_type: str, description: str) -> bool:
    combined = f"{incident_type} {description}".lower()
    return any(kw in combined for kw in _FLOOD_KEYWORDS)


def fetch() -> list[Incident]:
    incidents: list[Incident] = []
    try:
        response = httpx.get(_URL, timeout=15, headers={"Accept": "application/json"})
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as exc:
        print(f"[traffic_scotland] HTTP error: {exc}")
        return incidents

    for item in data.get("results", []):
        incident_type = item.get("incident_type_name", "") or ""
        incident_subtype = item.get("incident_sub_type_name", "") or ""
        description = item.get("description", "") or ""

        if not _is_flood(f"{incident_type} {incident_subtype}", description):
            continue

        lat = item.get("lat")
        lon = item.get("lng")
        reported_at = _from_timestamp(item.get("last_modified") or item.get("start_time")) or datetime.now(timezone.utc)
        occurred_at = _from_timestamp(item.get("start_time"))

        incidents.append(
            Incident(
                source=IncidentSource.TRAFFIC_SCOTLAND,
                external_id=str(item.get("incident_id", item.get("sid", ""))),
                occurred_at=occurred_at,
                reported_at=reported_at,
                longitude=float(lon) if lon is not None else None,
                latitude=float(lat) if lat is not None else None,
                road_ref=item.get("road_name"),
                description=description or incident_type,
                is_flood=True,
                flood_severity=FloodSeverity.UNKNOWN,
                raw=item,
            )
        )

    return incidents


def _from_timestamp(value) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (ValueError, OSError):
        return None
