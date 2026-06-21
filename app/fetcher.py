import logging
from datetime import datetime
from email.utils import formatdate
from pathlib import Path

import httpx

from .events import notify_new_image
from .image_utils import save_image

log = logging.getLogger(__name__)


async def fetch_http_image(source: dict, archive_root: Path) -> None:
    latest = archive_root / source["id"] / "latest.jpg"

    # HTTP conditional request — skip download entirely if server says unchanged
    headers = {}
    if latest.exists():
        headers["If-Modified-Since"] = formatdate(latest.stat().st_mtime, usegmt=True)

    try:
        verify = source.get("verify_ssl", True)
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, verify=verify) as client:
            resp = await client.get(source["url"], headers=headers)
        if resp.status_code == 304:
            log.debug("%s: 304 not modified", source["id"])
            return
        resp.raise_for_status()
        data = resp.content
    except Exception as exc:
        log.warning("fetch %s failed: %s", source["id"], exc)
        return

    try:
        changed = save_image(data, source, archive_root)
    except Exception as exc:
        log.warning("image processing %s failed: %s", source["id"], exc)
        return

    if changed:
        log.info("%s: saved new frame", source["id"])
        notify_new_image(source["id"])
    else:
        log.debug("%s: no change (hash)", source["id"])


def cleanup_old_files(source: dict, archive_root: Path) -> None:
    source_dir = archive_root / source["id"]
    if not source_dir.exists():
        return

    retention_secs = source.get("retention", 48) * 3600
    cutoff = datetime.now().timestamp() - retention_secs
    removed = 0
    for f in source_dir.glob("2*.jpg"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            removed += 1
    if removed:
        log.info("%s: removed %d old frames", source["id"], removed)
