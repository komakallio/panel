import logging
from datetime import datetime
from email.utils import formatdate
from pathlib import Path

import httpx

from .events import notify_new_image
from .image_utils import save_image

log = logging.getLogger(__name__)


async def fetch_http_image(source: dict, archive_root: Path) -> None:
    source_dir = archive_root / source["id"]
    latest = source_dir / "latest.jpg"
    etag_file = source_dir / "latest.etag"      # last seen ETag (strong validator)
    lm_file = source_dir / "latest.lastmod"     # last seen Last-Modified

    # Conditional headers for the GET fallback (servers that honour 304).
    cond = {}
    if etag_file.exists():
        cond["If-None-Match"] = etag_file.read_text()
    if latest.exists():
        cond["If-Modified-Since"] = formatdate(latest.stat().st_mtime, usegmt=True)

    verify = source.get("verify_ssl", True)
    etag = last_mod = None
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, verify=verify) as client:
            # Cheap HEAD probe first: many servers ignore conditional GETs and
            # re-send the whole image, so compare the strongest validator (ETag,
            # else Last-Modified) from a HEAD and skip the download if unchanged.
            try:
                head = await client.head(source["url"])
                if head.status_code < 400:
                    etag = head.headers.get("ETag")
                    last_mod = head.headers.get("Last-Modified")
            except Exception:
                pass
            if etag and etag_file.exists() and etag_file.read_text() == etag:
                log.debug("%s: unchanged (ETag)", source["id"])
                return
            if not etag and last_mod and lm_file.exists() and lm_file.read_text() == last_mod:
                log.debug("%s: unchanged (Last-Modified)", source["id"])
                return

            resp = await client.get(source["url"], headers=cond)
            if resp.status_code == 304:
                log.debug("%s: 304 not modified", source["id"])
                return
            resp.raise_for_status()
            data = resp.content
            etag = resp.headers.get("ETag", etag)
            last_mod = resp.headers.get("Last-Modified", last_mod)
    except Exception as exc:
        log.warning("fetch %s failed: %s", source["id"], exc)
        return

    try:
        changed = save_image(data, source, archive_root)
    except Exception as exc:
        log.warning("image processing %s failed: %s", source["id"], exc)
        return

    # Cache validators (save_image created source_dir) so the next HEAD probe /
    # conditional GET can skip re-downloading identical content.
    if etag:
        etag_file.write_text(etag)
    if last_mod:
        lm_file.write_text(last_mod)

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
