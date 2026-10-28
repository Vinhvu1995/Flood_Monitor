#!/usr/bin/env python3
"""us_flood_rss.py (~174 feeds: national/NWS alerts/local TV)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rss_core import run, parse_since  # noqa: E402

FEEDS = [
    ("NWS Flood Warning", "US", "https://api.weather.gov/alerts/active?event=Flood%20Warning"),
    ("NWS Flash Flood Warning", "US", "https://api.weather.gov/alerts/active?event=Flash%20Flood%20Warning"),
    ("AP US", "US", "https://feeds.apnews.com/ap/us"),
]

if __name__ == "__main__":
    run(FEEDS, parse_since(sys.argv), "--json" in sys.argv)
