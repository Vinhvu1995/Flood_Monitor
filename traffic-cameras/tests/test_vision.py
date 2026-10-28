"""Offline tests for vision + crosscheck (no network; flags off by default)."""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
os.environ.pop("USE_META_VISION", None)
os.environ.pop("META_API_KEY", None)

from vision import classify_flood, enabled
from rainfall_trigger import classify_downloaded
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/../../tomtom-alerts")
from crosscheck import crosscheck


class TestVision(unittest.TestCase):
    def test_disabled_offline(self):
        self.assertFalse(enabled())

    def test_heuristic_never_raises(self):
        out = classify_flood(b"\xff" * 30000, None)
        self.assertEqual(set(out), {"flooded", "confidence", "evidence", "mask_path"})
        self.assertFalse(out["flooded"])

    def test_trigger_hook_offline(self):
        out = classify_downloaded(b"\xff" * 30000)
        self.assertIn("confidence", out)

    def test_crosscheck_rain_gate(self):
        row = crosscheck({"incident_id": "1", "rainfall_latest_mm": 9.5,
                          "rainfall_24h_mm": 20.0})
        self.assertTrue(row["crosscheck"]["rain_supports"])
        row2 = crosscheck({"incident_id": "2", "rainfall_latest_mm": 1.0,
                           "rainfall_24h_mm": 2.0})
        self.assertFalse(row2["crosscheck"]["rain_supports"])

    def test_crosscheck_shape_unchanged(self):
        row = {"incident_id": "3", "from_road": "A", "rainfall_latest_mm": None,
               "rainfall_24h_mm": None}
        out = crosscheck(row)
        self.assertEqual(out["incident_id"], "3")
        self.assertEqual(out["from_road"], "A")


if __name__ == "__main__":
    unittest.main()
