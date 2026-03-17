"""Shared test configuration for HearthAndSeek pipeline tests."""

import sys
from pathlib import Path

# Add scraper directory to sys.path so all tests can import pipeline modules.
# conftest.py is loaded by pytest before test modules, so this runs first.
SCRAPER_DIR = Path(__file__).resolve().parent.parent

if str(SCRAPER_DIR) not in sys.path:
    sys.path.insert(0, str(SCRAPER_DIR))
