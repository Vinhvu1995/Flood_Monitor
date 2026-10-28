"""Tests for rainfall_trigger pure helpers (no network/S3/NFS needed)."""

import hashlib
import io
import json
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

import rainfall_trigger as rt


def utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


# --- ASC naming: rainfall1 = 02:00 UTC of the folder date -------------------

assert_cases = [
    # (now, folder, N)
    (utc(2026, 10, 27, 2, 5), "20261027", 1),
    (utc(2026, 10, 27, 3, 0), "20261027", 2),
    (utc(2026, 10, 27, 23, 59), "20261027", 22),
    (utc(2026, 10, 28, 0, 30), "20261027", 23),
    (utc(2026, 10, 28, 1, 59), "20261027", 24),
]


@pytest.mark.parametrize("when,folder,n", assert_cases)
def test_asc_naming(when, folder, n):
    p = rt.asc_path_for_hour(when, rainfall_dir="/rain")
    assert p == Path(f"/rain/{folder}/rainfall{n}.asc")


def test_next_hour_lookup_uses_next_hour_file():
    # 05:40 run looks up the 06:00 file in the same folder.
    now = utc(2026, 10, 27, 5, 40)
    nxt = (now.replace(minute=0, second=0, microsecond=0)
           + rt.timedelta(hours=1))
    assert rt.asc_path_for_hour(nxt, "/r").name == "rainfall5.asc"


# --- read_asc_value -----------------------------------------------------------

ASC = """ncols 3
nrows 2
xllcorner 0
yllcorner 0
cellsize 1
NODATA_value -9999
1 2 3
4 -9999 6
"""


def test_read_asc_value(tmp_path):
    f = tmp_path / "rainfall1.asc"
    f.write_text(ASC)
    assert rt.read_asc_value(f, 0.5, 1.5) == 1.0  # top-left
    assert rt.read_asc_value(f, 2.5, 0.5) == 6.0  # bottom-right
    assert rt.read_asc_value(f, 1.5, 0.5) is None  # nodata
    assert rt.read_asc_value(f, 99, 99) is None  # out of bounds
    assert rt.read_asc_value(tmp_path / "missing.asc", 0.5, 0.5) is None


# --- minute gate ---------------------------------------------------------------

def _args(**kw):
    base = dict(dry_run=False, force=False)
    base.update(kw)
    return Namespace(**base)


def test_minute_gate():
    assert rt.lookup_allowed(utc(2026, 1, 1, 5, 10), _args())
    assert not rt.lookup_allowed(utc(2026, 1, 1, 5, 11), _args())
    assert rt.lookup_allowed(utc(2026, 1, 1, 5, 11), _args(force=True))
    assert rt.lookup_allowed(utc(2026, 1, 1, 5, 59), _args(dry_run=True))


def test_decide_triggered_respects_gate(tmp_path):
    cams = [{"id": "1", "e": 0.5, "n": 1.5}]
    args = _args(threshold=8.0, test_rainfall=None, test_asc=None,
                 rainfall_dir=str(tmp_path))
    trig, asc = rt.decide_triggered(cams, args, utc(2026, 1, 1, 5, 30))
    assert trig == [] and asc is None  # gate closed, no lookup
    trig, _ = rt.decide_triggered(
        cams, _args(threshold=8.0, test_rainfall=9.0), utc(2026, 1, 1, 5, 30))
    assert len(trig) == 1
    trig, _ = rt.decide_triggered(
        cams, _args(threshold=8.0, test_rainfall=7.9), utc(2026, 1, 1, 5, 30))
    assert trig == []


# --- placeholder ----------------------------------------------------------------

def test_placeholder_detection():
    blob = b"x" * rt.PLACEHOLDER_SIZE
    # exact placeholder: size + digest
    digest = hashlib.sha256(blob).hexdigest()
    assert rt.is_placeholder(blob) == (digest == rt.PLACEHOLDER_SHA256)
    assert not rt.is_placeholder(b"x" * 100)
    assert not rt.is_placeholder(b"y" * rt.PLACEHOLDER_SIZE)  # size ok, hash differs


# --- brightness -------------------------------------------------------------------

def solid_jpeg(grey: int) -> bytes:
    img = Image.new("L", (32, 32), grey)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_brightness_std_dark_frame_discarded():
    assert rt.brightness_std(solid_jpeg(30)) < rt.BRIGHTNESS_STD_MIN


def test_unreadable_bytes_not_faulty():
    # original _is_faulty_image returns False on exception -> image is saved
    assert rt._is_faulty_image(b"not an image") is False
    assert rt.brightness_std(b"not an image") is None


def test_brightness_std_textured_frame_kept():
    import numpy as np

    arr = (np.random.default_rng(0).integers(0, 255, (64, 64))).astype("uint8")
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="JPEG")
    assert rt.brightness_std(buf.getvalue()) >= rt.BRIGHTNESS_STD_MIN


# --- state path: NFS primary, /tmp fallback -----------------------------------------

def test_state_path_explicit_and_env(tmp_path, monkeypatch):
    assert rt.resolve_state_path(str(tmp_path / "s.json")) == tmp_path / "s.json"
    monkeypatch.setenv("TRAFFIC_CAM_STATE_FILE", str(tmp_path / "e.json"))
    assert rt.resolve_state_path() == tmp_path / "e.json"


def test_state_save_load_roundtrip(tmp_path):
    p = tmp_path / "sub" / "state.json"
    rt.save_state(p, {"windows": {"7": {"window_end": "x"}}})
    assert json.loads(p.read_text())["windows"]["7"]["window_end"] == "x"
    assert rt.load_state(p)["windows"]["7"]["window_end"] == "x"
    assert rt.load_state(tmp_path / "nope.json") == {"windows": {}}
