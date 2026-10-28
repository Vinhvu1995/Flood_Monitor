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
