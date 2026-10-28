#!/usr/bin/env python3
"""us_flood_rss.py (~174 feeds: national/NWS alerts/local TV)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from rss_core import run, parse_since  # noqa: E402
from sources import US_RSS_FEEDS as FEEDS  # noqa: E402

if __name__ == "__main__":
    run(FEEDS, parse_since(sys.argv), "--json" in sys.argv)
