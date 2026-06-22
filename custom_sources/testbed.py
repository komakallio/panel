"""
Custom source: FMI Helsinki Testbed images (testbed.fmi.fi).

The viewer page embeds its animation as two parallel JS arrays — anim_timestamps
(UTC, YYYYMMDDHHMM) and anim_images_* (the obfuscated, time-encoded img.php
URLs). We parse both and archive any frame we're missing, so a gap up to the
page's window (n frames × 5 min) is recovered on the next poll. The img.php URLs
can't be constructed by hand (their A= key is opaque), so scraping the page is
the only way to reach them.

Source YAML must include:
    type: custom
    module: testbed
    page_url: <viewer URL, e.g. https://testbed.fmi.fi/?imgtype=radar&t=5&n=15>
              n sets how many recent frames the page lists — use the max, 15.
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.backfill import backfill_frames

log = logging.getLogger(__name__)

_TS_ARR_RE  = re.compile(r"anim_timestamps\s*=\s*new Array\(([^)]*)\)")
_IMG_ARR_RE = re.compile(r"anim_images\w*\s*=\s*new Array\(([^)]*)\)")
_QUOTED_RE  = re.compile(r'"([^"]*)"')


def _parse_frames(html: str) -> list[tuple[datetime, str]]:
    """(utc_time, img_url) for every frame in the page's animation arrays."""
    tm = _TS_ARR_RE.search(html)
    im = _IMG_ARR_RE.search(html)
    if not tm or not im:
        return []

    stamps = _QUOTED_RE.findall(tm.group(1))
    urls = _QUOTED_RE.findall(im.group(1))
    frames: list[tuple[datetime, str]] = []
    for stamp, url in zip(stamps, urls):
        if len(stamp) == 12 and stamp.isdigit():
            ts = datetime.strptime(stamp, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
            frames.append((ts, url))
    return sorted(frames)


async def fetch(source: dict, archive_root: Path) -> None:
    source_id = source["id"]
    page_url = source.get("page_url")
    if not page_url:
        log.error("testbed: source %s missing page_url", source_id)
        return

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            page = await client.get(page_url)
            page.raise_for_status()
            frames = _parse_frames(page.text)
            if not frames:
                log.warning("testbed %s: no frames parsed from page", source_id)
                return
            saved = await backfill_frames(source, archive_root, frames, client)
    except Exception as exc:
        log.warning("testbed %s: fetch failed: %s", source_id, exc)
        return

    if saved:
        log.info("testbed %s: archived %d new frame(s)", source_id, saved)
    else:
        log.debug("testbed %s: no new frames", source_id)
