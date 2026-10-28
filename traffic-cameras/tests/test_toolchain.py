"""Tests for the discovery/matching toolchain (stdlib-level, no network)."""

import csv
import json

import Download_images as dl
import match_cameras as mc
import xml_to_geojson as x2g


def test_xml_to_geojson(tmp_path):
    xml = tmp_path / "camera_locations.xml"
    xml.write_text(
        '<markers>'
        '<marker id="1" text="M25 J10" color="green" location="51.2,-0.5"/>'
        '<marker id="2" text="bad" color="red" location="nowhere"/>'
        "</markers>"
    )
    fc = x2g.convert(xml)
    assert len(fc["features"]) == 1
    f = fc["features"][0]
    assert f["geometry"]["coordinates"] == [-0.5, 51.2]
    assert f["properties"]["text"] == "M25 J10"


def _feat(text):
    return {"geometry": {"type": "Point", "coordinates": [0, 51]},
            "text": text, "properties": {"text": text}}


def test_match_two_pass_token_filter():
    markers = [_feat("M25 Junction 10 Wisley"), _feat("M25 Junction 10 Wisley")]
    metas = [{"id": "42", "desc": "M25 J10 Wisley traffic camera view",
              "url": "http://img/42.jpg", "view_url": "http://view/42.html"}]
    merged, unused, dups = mc.match(markers, metas)
    by_status = {f["properties"]["match_status"] for f in merged}
    # pass 1 consumes the only camera; pass 2 reuses it as a duplicate.
    assert by_status == {"matched", "duplicate"}
    assert len(dups) == 1
    assert unused == []
    matched = next(f for f in merged
                   if f["properties"]["match_status"] == "matched")
    assert matched["properties"]["matched_camera_id"] == "42"
    assert matched["properties"]["image_url"] == "http://img/42.jpg"


def test_match_unmatched_and_unused():
    markers = [_feat("Zebra Crossing Nowhere")]
    metas = [{"id": "7", "desc": "M1 J1 camera", "url": "", "view_url": ""}]
    merged, unused, _ = mc.match(markers, metas)
    assert merged[0]["properties"]["match_status"] == "unmatched"
    assert [m["id"] for m in unused] == ["7"]


def test_road_from_description():
    assert dl.road_from_description("M25 J10 Wisley") == "M25"
    assert dl.road_from_description("A1(M) J3 camera") == "A1(M)"
    assert dl.road_from_description("some barn lane") == "Unknown"
