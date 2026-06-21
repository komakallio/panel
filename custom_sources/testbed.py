"""
Custom source: FMI Helsinki Testbed images (testbed.fmi.fi).

The testbed viewer page embeds the current image as an obfuscated, time-encoded
URL (https://N.img.fmi.fi/php/img.php?A=...) that changes as new frames appear,
so hardcoding it would freeze the image. We scrape the page each fetch and pull
out the latest image URL.

Source YAML must include:
    type: custom
    module: testbed
    page_url: <viewer URL, e.g. https://testbed.fmi.fi/?imgtype=radar&t=5&n=1>
"""

import logging
import re
from pathlib import Path

import httpx

from app.events import notify_new_image
from app.image_utils import save_image

log = logging.getLogger(__name__)

# https://3.img.fmi.fi/php/img.php?A=<encoded>
_IMG_RE = re.compile(r"https://\d+\.img\.fmi\.fi/php/img\.php\?A=[^\s\"'<>]+")


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
            match = _IMG_RE.search(page.text)
            if not match:
                log.warning("testbed %s: no img.php URL found on page", source_id)
                return
            resp = await client.get(match.group(0))
            resp.raise_for_status()
            data = resp.content
    except Exception as exc:
        log.warning("testbed %s: fetch failed: %s", source_id, exc)
        return

    try:
        changed = save_image(data, source, archive_root)
    except Exception as exc:
        log.warning("testbed %s: image processing failed: %s", source_id, exc)
        return

    if changed:
        log.info("testbed %s: saved new frame", source_id)
        notify_new_image(source_id)
    else:
        log.debug("testbed %s: no change", source_id)
