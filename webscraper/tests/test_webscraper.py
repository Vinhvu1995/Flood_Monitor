"""Offline tests: python3 -m unittest discover -s tests (no network)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from country_config import get_country  # noqa: E402
from article_signals import date_signal, nonarticle_shape, state_conflict  # noqa: E402
from lsr_parser import parse_lsr, is_lsr_page  # noqa: E402
from sources import resolve_gnews_url, dedup_urls, redact  # noqa: E402
from geocoding import infer_location_type, geoencode_local  # noqa: E402
from notifications import build_card  # noqa: E402
from validation_receipt import write_receipt, check_receipt  # noqa: E402
from main_country import (skipped_scrape, llm_params, proximity_gate,  # noqa: E402
                          bad_source, run_pipeline, parse_args)
from datetime import date  # noqa: E402


class TestScaffold(unittest.TestCase):
    def test_country_registry(self):
        for cc in ("UK", "US", "IT"):
            cfg = get_country(cc)
            self.assertTrue(cfg.query_templates and cfg.discovery_queries)
        self.assertRaises(ValueError, get_country, "XX")

    def test_gnews_unescape_strips_backslash(self):
        link = "https://news.google.com/rss/articles/CBMiXEFVX3lx"
        out = resolve_gnews_url(link, fetch=lambda u, **k: b"")
        self.assertEqual(out, link)  # no tokens -> fallback
        page = 'data-n-a-sg="SG" data-n-a-ts="TS"'
        hit = resolve_gnews_url(
            link,
            fetch=lambda u, **k: page if "articles" in u else 'x garturlres y "https://pub.example/a\\\\" z')
        self.assertEqual(hit, "https://pub.example/a")

    def test_redact(self):
        self.assertNotIn("SECRET", redact("https://x/?key=SECRET&q=1"))

    def test_dedup(self):
        items = [{"url": "a"}, {"url": "a"}, {"url": "b"}]
        self.assertEqual(len(dedup_urls(items)), 2)

    def test_skipped_scrape(self):
        self.assertEqual(skipped_scrape("https://facebook.com/x", ()), "excluded_domain")
        self.assertEqual(skipped_scrape("https://www.bbc.com/weather/uk", ()), "bbc_weather")
        self.assertIsNone(skipped_scrape("https://www.bbc.co.uk/news/uk-123", ()))

    def test_llm_no_temperature_nano(self):
        p = llm_params("gpt-5-nano")
        self.assertNotIn("temperature", p)
        self.assertIn("reasoning_effort", p)
        self.assertIn("temperature", llm_params("gpt-4.1"))

    def test_date_signal(self):
        s, _ = date_signal("flood on 2026-10-20", date(2026, 10, 19), date(2026, 10, 21))
        self.assertEqual(s, "in_window")
        s, _ = date_signal("flood on 2026-01-01", date(2026, 10, 19), date(2026, 10, 21))
        self.assertEqual(s, "out_of_window")

    def test_proximity(self):
        self.assertTrue(proximity_gate("York", "flooding in York devastated homes"))
        self.assertFalse(proximity_gate("York", "York is nice. " + "x" * 150 + " Unrelated flood in Leeds"))

    def test_nonarticle(self):
        self.assertTrue(nonarticle_shape("https://x.com/tag/flood"))
        self.assertTrue(nonarticle_shape("https://x.com/news?page=2"))

    def test_lsr(self):
        txt = "900 AM FLOOD ...TIME... LAT 40.7 LON -74.0 REMARKS: street flooding on Route 17"
        recs = parse_lsr(txt)
        self.assertTrue(recs and recs[0].place_override)
        self.assertTrue(is_lsr_page("https://partnerservices.nws.noaa.gov/products/cache/LSR", txt + " Local Storm Report ..TIME..."))

    def test_geocode_tiers(self):
        self.assertEqual(infer_location_type("A21 Robertsbridge"), "road")
        self.assertEqual(infer_location_type("River Ouse"), "river")
        g = geoencode_local([{"name": "York, Yorkshire"}], region="UK")
        self.assertEqual(len(g["points"]), 1)

    def test_cards(self):
        c = build_card("validated", locations=[f"L{i}" for i in range(10)])
        self.assertIn("+2 more", c["facts"][-1]["value"])

    def test_receipt_roundtrip(self):
        import tempfile
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "Vectorised_Files"))
        open(os.path.join(d, "Vectorised_Files", "a.geojson"), "w").write("{}")
        write_receipt(d, "v.json", [{"url": "u", "location": "l", "keep": True}],
                      1, 1, 0, 0, 1)
        ok, _ = check_receipt(d)
        self.assertTrue(ok)

    def test_pipeline_offline(self):
        args = parse_args(["--country", "UK", "--start-date", "2026-10-20", "--duration", "2", "--fname", "t"])
        def discover(qs):
            return [{"url": "https://example.com/flood-york", "snippet": "flooding in York",
                     "source": "cse"}]
        out = run_pipeline(args, hooks={
            "discover": discover,
            "fetch": lambda u: ("flooding in York devastated homes in York on 2026-10-20", {}),
            "llm": lambda t: {"flood_event": True, "locations": [{"name": "York", "date": "2026-10-20", "context": "flood"}]}})
        self.assertEqual(out["summary"]["status"], "completed")
        self.assertTrue(out["summary"]["s3_vectors_deferred"])


if __name__ == "__main__":
    unittest.main()
