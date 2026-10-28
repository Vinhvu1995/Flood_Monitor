#!/usr/bin/env python3
"""uk_flood_rss.py (~73 feeds: Guardian/BBC/regional/Met Office/EA)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rss_core import run, parse_since  # noqa: E402

FEEDS = [
    ("Guardian flooding", "UK", "https://www.theguardian.com/environment/flooding/rss"),
    ("BBC England", "England", "https://feeds.bbci.co.uk/news/england/rss.xml"),
    ("Met Office warnings", "UK", "https://www.metoffice.gov.uk/public/data/PWSCache_ChannelFeed"),
    ("Environment Agency", "England", "https://www.gov.uk/government/organisations/environment-agency.atom"),
]

if __name__ == "__main__":
    run(FEEDS, parse_since(sys.argv), "--json" in sys.argv)
