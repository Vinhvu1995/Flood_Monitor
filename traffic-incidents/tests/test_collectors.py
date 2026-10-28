"""Unit tests for flood-filtering logic in each collector."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from collectors import national_highways, tfl, traffic_scotland, traffic_watch_ni
from models.incident import IncidentSource


# ── National Highways ──────────────────────────────────────────────────────────

NH_FLOOD_XML = b"""<?xml version="1.0"?>
<D2Payload>
  <situation>
    <idG>NH-FLOOD-001</idG>
    <situationRecord>
      <idG>NH-FLOOD-001-rec</idG>
      <situationRecordCreationTime>2026-01-08T10:00:00Z</situationRecordCreationTime>
      <cause>
        <causeType>flooding</causeType>
      </cause>
      <generalPublicComment>
        <comment>A303 closed due to flood water on carriageway</comment>
      </generalPublicComment>
      <locationReference>
        <locLinearLocation>
          <gmlLineString>
            <locGmlLineString>
              <posList>51.2 -1.5 51.201 -1.501</posList>
            </locGmlLineString>
          </gmlLineString>
          <supplementaryPositionalDescription>
            <locationDescription>A303 near Andover</locationDescription>
          </supplementaryPositionalDescription>
        </locLinearLocation>
        <locSingleRoadLinearLocation>
          <linearWithinLinearElement>
            <linearElement>
              <locLinearElementByCode>
                <roadName>A303</roadName>
              </locLinearElementByCode>
            </linearElement>
          </linearWithinLinearElement>
        </locSingleRoadLinearLocation>
      </locationReference>
    </situationRecord>
  </situation>
  <situation>
    <idG>NH-ROAD-002</idG>
    <situationRecord>
      <idG>NH-ROAD-002-rec</idG>
      <situationRecordCreationTime>2026-01-08T08:00:00Z</situationRecordCreationTime>
      <cause>
        <causeType>roadOrCarriagewayOrLaneManagement</causeType>
      </cause>
      <generalPublicComment>
        <comment>Lane closure for planned maintenance</comment>
      </generalPublicComment>
      <locationReference>
        <locSingleRoadLinearLocation>
          <linearWithinLinearElement>
            <linearElement>
              <locLinearElementByCode>
                <roadName>M25</roadName>
              </locLinearElementByCode>
            </linearElement>
          </linearWithinLinearElement>
        </locSingleRoadLinearLocation>
      </locationReference>
    </situationRecord>
  </situation>
</D2Payload>"""


def _mock_response(payload):
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.json.return_value = payload
    return mock


def _mock_xml_response(content: bytes):
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.content = content
    return mock


def test_national_highways_filters_flood_incidents():
    with patch("collectors.national_highways.httpx.get") as mock_get, \
         patch("collectors.national_highways.settings") as mock_settings:
        mock_settings.nh_api_key = "test-key"
        mock_get.return_value = _mock_xml_response(NH_FLOOD_XML)
        incidents = national_highways.fetch()

    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.source == IncidentSource.NATIONAL_HIGHWAYS
    assert inc.is_flood is True
    assert inc.road_ref == "A303"
    assert inc.latitude == pytest.approx(51.2)
    assert inc.longitude == pytest.approx(-1.5)


def test_national_highways_returns_empty_on_http_error():
    with patch("collectors.national_highways.httpx.get") as mock_get, \
         patch("collectors.national_highways.settings") as mock_settings:
        import httpx
        mock_settings.nh_api_key = "test-key"
        mock_get.side_effect = httpx.HTTPError("connection refused")
        incidents = national_highways.fetch()
    assert incidents == []


def test_national_highways_skipped_without_api_key():
    with patch("collectors.national_highways.settings") as mock_settings:
        mock_settings.nh_api_key = ""
        assert national_highways.fetch() == []


# ── TfL ────────────────────────────────────────────────────────────────────────

TFL_FLOOD_DISRUPTION = {
    "id": "TFL-001",
    "category": "FloodAlert",
    "comments": "Road flooded near Waterloo Bridge",
    "street": "Waterloo Bridge",
    "startDateTime": "2026-01-08T12:00:00Z",
    "geography": {"lat": 51.507, "lon": -0.113},
}
TFL_NONFLOOD_DISRUPTION = {
    "id": "TFL-002",
    "category": "RoadClosure",
    "comments": "Planned closure for event",
    "street": "Oxford Street",
    "startDateTime": "2026-01-08T09:00:00Z",
}


def test_tfl_filters_flood_disruptions():
    with patch("collectors.tfl.httpx.get") as mock_get:
        mock_get.return_value = _mock_response([TFL_FLOOD_DISRUPTION, TFL_NONFLOOD_DISRUPTION])
        incidents = tfl.fetch()

    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.external_id == "TFL-001"
    assert inc.source == IncidentSource.TFL
    assert inc.is_flood is True
    assert inc.latitude == pytest.approx(51.507)


def test_tfl_returns_empty_on_http_error():
    with patch("collectors.tfl.httpx.get") as mock_get:
        import httpx
        mock_get.side_effect = httpx.HTTPError("timeout")
        incidents = tfl.fetch()
    assert incidents == []


# ── Traffic Scotland ───────────────────────────────────────────────────────────

TS_FLOOD_INCIDENT = {
    "incident_id": "c12345",
    "incident_type_name": "Flooding",
    "incident_sub_type_name": "Surface Water",
    "description": "A9 closed due to surface water flooding",
    "road_name": "A9",
    "lat": "56.33",
    "lng": "-3.64",
    "start_time": "1779793880",
    "last_modified": "1779794000",
}
TS_NONFLOOD_INCIDENT = {
    "incident_id": "c99999",
    "incident_type_name": "Accident",
    "incident_sub_type_name": "Accident",
    "description": "Multi-vehicle collision on M8",
    "road_name": "M8",
    "lat": "55.87",
    "lng": "-4.27",
    "start_time": "1779793000",
    "last_modified": "1779793500",
}


def test_traffic_scotland_filters_flood_incidents():
    with patch("collectors.traffic_scotland.httpx.get") as mock_get:
        mock_get.return_value = _mock_response({"results": [TS_FLOOD_INCIDENT, TS_NONFLOOD_INCIDENT]})
        incidents = traffic_scotland.fetch()

    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.external_id == "c12345"
    assert inc.source == IncidentSource.TRAFFIC_SCOTLAND
    assert inc.is_flood is True
    assert inc.road_ref == "A9"
    assert inc.latitude == pytest.approx(56.33)


def test_traffic_scotland_keyword_in_description():
    incident = {**TS_NONFLOOD_INCIDENT, "incident_id": "c77777", "description": "Road closed due to standing water"}
    with patch("collectors.traffic_scotland.httpx.get") as mock_get:
        mock_get.return_value = _mock_response({"results": [incident]})
        incidents = traffic_scotland.fetch()
    assert len(incidents) == 1
    assert incidents[0].external_id == "c77777"


def test_traffic_scotland_returns_empty_on_http_error():
    with patch("collectors.traffic_scotland.httpx.get") as mock_get:
        import httpx
        mock_get.side_effect = httpx.HTTPError("timeout")
        incidents = traffic_scotland.fetch()
    assert incidents == []


# ── Traffic Watch NI ──────────────────────────────────────────────────────────

NI_FLOOD_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Trafficwatch NI Incidents</title>
    <item>
      <title>A2 Flooding near Bangor</title>
      <description>Road closed due to flood water on carriageway</description>
      <guid>https://www.trafficwatchni.com/ni-001</guid>
      <pubDate>Wed, 08 Jan 2026 10:00:00 +0000</pubDate>
    </item>
    <item>
      <title>M1 Accident near Lisburn</title>
      <description>Lane closure following collision</description>
      <guid>https://www.trafficwatchni.com/ni-002</guid>
      <pubDate>Wed, 08 Jan 2026 09:30:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""


def _mock_rss_response(content: bytes):
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.content = content
    return mock


def test_traffic_watch_ni_filters_flood_incidents():
    with patch("collectors.traffic_watch_ni.httpx.get") as mock_get:
        mock_get.return_value = _mock_rss_response(NI_FLOOD_RSS)
        incidents = traffic_watch_ni.fetch()

    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.source == IncidentSource.TRAFFIC_WATCH_NI
    assert inc.is_flood is True
    assert "Bangor" in inc.description


def test_traffic_watch_ni_returns_empty_on_http_error():
    with patch("collectors.traffic_watch_ni.httpx.get") as mock_get:
        import httpx
        mock_get.side_effect = httpx.HTTPError("timeout")
        incidents = traffic_watch_ni.fetch()
    assert incidents == []
