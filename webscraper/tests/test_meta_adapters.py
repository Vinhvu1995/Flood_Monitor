"""Offline tests for Meta adapters (no network; flags off by default)."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.pop("USE_META_LLM", None)
os.environ.pop("META_API_KEY", None)

from meta_llm import extract as meta_extract, enabled as meta_enabled
from validation_agent import adjudicate, rule_verdict
from main_country import llm_extract


class TestMetaAdapters(unittest.TestCase):
    def test_meta_disabled_without_flag(self):
        self.assertFalse(meta_enabled())
        self.assertIsNone(meta_extract("flooding in York", "UK", "2026-10-20", "2026-10-22"))

    def test_llm_extract_falls_back_offline(self):
        out = llm_extract("flooding in York devastated homes", "United Kingdom",
                          "2026-10-20", "2026-10-22")
        self.assertIn("flood_event", out)
        self.assertTrue(out["flood_event"])
        self.assertIn("York", [loc["name"] for loc in out["locations"]])

    def test_heuristic_skips_event_names(self):
        from main_country import heuristic_candidates
        locs = heuristic_candidates("Storm Boelo flooding leaves Dumbarton submerged",
                                    "2026-10-27")
        names = [loc["name"] for loc in locs]
        self.assertIn("Dumbarton", names)
        self.assertFalse(any("Boelo" in n for n in names))

    def test_heuristic_skips_chrome(self):
        from main_country import heuristic_candidates, is_chrome, strip_chrome
        self.assertTrue(is_chrome("Cars Homes Local"))
        self.assertTrue(is_chrome("Ip"))
        self.assertTrue(is_chrome("Cars Hom"))
        self.assertTrue(is_chrome("Newsletters Jobs Book"))
        self.assertFalse(is_chrome("Dorset"))
        self.assertFalse(is_chrome("Dumbarton"))
        self.assertEqual(strip_chrome("Dorset Echo Public"), "Dorset")
        self.assertEqual(strip_chrome("The National Full"), "")
        locs = heuristic_candidates("flooding in Dorset. Cars Homes Local Skip Close",
                                    "2026-10-24")
        names = [loc["name"] for loc in locs]
        self.assertIn("Dorset", names)
        self.assertFalse(any(is_chrome(n) for n in names))

    def test_heuristic_skips_conjoined_storm_names(self):
        from main_country import heuristic_candidates
        locs = heuristic_candidates("Flood alerts lifted for Dorset after Storm Austen and Boelo",
                                    "2026-10-24")
        names = [loc["name"] for loc in locs]
        self.assertIn("Dorset", names)
        self.assertFalse(any(n in ("Austen", "Boelo") for n in names))

    def test_rule_verdict_drops_chrome(self):
        from validation_agent import rule_verdict
        keep, reason = rule_verdict("http://x", {"location": "Cars Homes Local"},
                                    "flooding Cars Homes Local", "in_window")
        self.assertFalse(keep)
        self.assertIn("contamination", reason)

    def test_rule_verdict_out_of_window(self):
        keep, reason = rule_verdict("http://x", {"location": "York"},
                                    "flooding in York", "out_of_window")
        self.assertFalse(keep)

    def test_rule_verdict_keeps_reported_place(self):
        keep, _ = rule_verdict("http://x", {"location": "York"},
                              "severe flooding in York on Tuesday " * 30, "in_window")
        self.assertTrue(keep)

    def test_adjudicate_covers_every_pair(self):
        groups = [{"url": "http://a", "article_text": "flooding in York " * 40,
                   "date_signal": "in_window",
                   "locations": [{"location": "York"}, {"location": "Leeds"}]}]
        v = adjudicate(groups)
        self.assertEqual(len(v), 2)
        self.assertTrue(all(set(x) >= {"url", "location", "keep", "reason"} for x in v))


if __name__ == "__main__":
    unittest.main()
