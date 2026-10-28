"""Offline tests for keyless RSS discovery (no network; injected XML)."""
import os, sys, unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from rss_core import fetch_feed, parse_pub_date
from sources import RSSSource, build_sources
from country_config import get_country

XML = """<rss><channel>
<item><title>Severe flooding in York</title><link>https://ex.com/york-flood</link>
<pubDate>Sat, 25 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>Old flood story</title><link>https://ex.com/old</link>
<pubDate>Mon, 01 Sep 2026 10:00:00 GMT</pubDate></item>
<item><title>Football results</title><link>https://ex.com/sport</link>
<pubDate>Sat, 25 Oct 2026 11:00:00 GMT</pubDate></item>
</channel></rss>"""


class TestRSS(unittest.TestCase):
    def test_parse_pub_date(self):
        self.assertEqual(parse_pub_date("Sat, 25 Oct 2026 10:00:00 GMT"),
                         datetime(2026, 10, 25, 10, 0))
        self.assertIsNone(parse_pub_date("not a date"))
        self.assertIsNone(parse_pub_date(""))

    def test_fetch_feed_filters_date_and_topic(self):
        hits, fail = fetch_feed(("T", "UK", "https://ex.com/rss"),
                                datetime(2026, 10, 24),
                                fetch_xml=lambda u: XML)
        self.assertIsNone(fail)
        self.assertEqual([h["url"] for h in hits], ["https://ex.com/york-flood"])
        self.assertIn("York", hits[0]["title"])

    def test_fetch_feed_failure_isolated(self):
        def boom(u):
            raise ConnectionError("down")
        hits, fail = fetch_feed(("T", "UK", "https://ex.com/rss"), None, fetch_xml=boom)
        self.assertEqual(hits, [])
        self.assertIn("down", fail)

    def test_rss_source_offline(self):
        src = RSSSource(feeds=[("T", "UK", "https://ex.com/rss")],
                        fetch_xml=lambda u: XML)
        out = src.search([], "2026-10-24", "2026-10-26")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["source"], "rss")
        self.assertIn("York", out[0]["snippet"])  # title doubles as snippet

    def test_build_sources_has_rss(self):
        srcs = build_sources(get_country("UK"))
        self.assertIn("rss", srcs)
        self.assertTrue(srcs["rss"].feeds)
        self.assertTrue(all(u.startswith("http") for _, _, u in srcs["rss"].feeds))
        self.assertNotIn("metoffice.gov.uk/public/data/PWSCache_ChannelFeed",
                         [u for _, _, u in srcs["rss"].feeds])

    def test_no_hardcoded_cse_key(self):
        import sources as _s, inspect
        self.assertNotIn("AIza", inspect.getsource(_s))


if __name__ == "__main__":
    unittest.main()
