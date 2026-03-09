#!/usr/bin/env python3
"""Download and resize WoWDB decor thumbnails for visual classification.

Extracts thumbnail URLs from cached WoWDB HTML pages and downloads
them as resized PNGs into a local cache directory.

Usage:
    python download_thumbnails.py [--size 256] [--workers 4]
"""

import argparse
import io
import re
import sys
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parent
CACHE_DIR = SCRIPT_DIR / "data" / "wowdb_cache"
THUMB_DIR = SCRIPT_DIR / "data" / "thumbnails"

OG_IMAGE_RE = re.compile(
    r'<meta\s+property="og:image"\s+content="(https://housing-media\.wowdb\.com/decor/thumb/\d+\.png)"',
)
DECOR_ID_RE = re.compile(r"housing_wowdb_com_decor_(\d+)__")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) HearthAndSeek-Pipeline/1.0"

logger = logging.getLogger(__name__)


def extract_image_urls() -> dict[str, str]:
    """Extract {decorID: imageURL} from cached WoWDB HTML pages."""
    mapping: dict[str, str] = {}
    missing: list[str] = []

    for html_file in sorted(CACHE_DIR.glob("housing_wowdb_com_decor_*__*.html")):
        m = DECOR_ID_RE.search(html_file.name)
        if not m:
            continue
        decor_id = m.group(1)

        content = html_file.read_text(encoding="utf-8", errors="replace")
        img_match = OG_IMAGE_RE.search(content)
        if img_match:
            mapping[decor_id] = img_match.group(1)
        else:
            missing.append(decor_id)

    if missing:
        logger.warning("No thumbnail URL found for %d items: %s", len(missing), missing)

    return mapping


def download_and_resize(decor_id: str, url: str, size: int) -> str | None:
    """Download a thumbnail, resize it, and save to THUMB_DIR.

    Returns None on success, or an error message string.
    """
    out_path = THUMB_DIR / f"{decor_id}.png"
    if out_path.exists():
        return None  # Already downloaded

    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        resp = urlopen(req, timeout=20)
        data = resp.read()
    except (URLError, HTTPError, TimeoutError) as e:
        return f"decorID {decor_id}: download failed - {e}"

    try:
        img = Image.open(io.BytesIO(data))
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        img = img.resize((size, size), Image.LANCZOS)
        img.save(out_path, "PNG", optimize=True)
    except Exception as e:
        return f"decorID {decor_id}: image processing failed - {e}"

    return None


def main():
    parser = argparse.ArgumentParser(description="Download WoWDB decor thumbnails")
    parser.add_argument("--size", type=int, default=256,
                        help="Resize images to NxN pixels (default: 256)")
    parser.add_argument("--workers", type=int, default=4,
                        help="Number of download threads (default: 4)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    logger.info("Extracting image URLs from cached HTML pages...")
    url_map = extract_image_urls()
    logger.info("Found %d thumbnail URLs", len(url_map))

    THUMB_DIR.mkdir(parents=True, exist_ok=True)

    # Check how many already downloaded
    existing = sum(1 for did in url_map if (THUMB_DIR / f"{did}.png").exists())
    to_download = len(url_map) - existing
    if existing:
        logger.info("Already downloaded: %d, remaining: %d", existing, to_download)
    if to_download == 0:
        logger.info("All thumbnails already downloaded!")
        return

    errors: list[str] = []
    done = 0
    start = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for decor_id, url in url_map.items():
            if (THUMB_DIR / f"{decor_id}.png").exists():
                continue
            f = pool.submit(download_and_resize, decor_id, url, args.size)
            futures[f] = decor_id

        for f in as_completed(futures):
            result = f.result()
            done += 1
            if result:
                errors.append(result)
                logger.warning(result)
            if done % 100 == 0 or done == to_download:
                elapsed = time.time() - start
                rate = done / elapsed if elapsed > 0 else 0
                logger.info("Progress: %d/%d (%.1f/s)", done, to_download, rate)

    elapsed = time.time() - start
    logger.info("Done in %.1fs. Downloaded: %d, Errors: %d",
                elapsed, to_download - len(errors), len(errors))

    if errors:
        logger.warning("Failed downloads:")
        for e in errors:
            logger.warning("  %s", e)


if __name__ == "__main__":
    main()
