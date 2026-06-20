import hashlib
import io
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
from PIL import Image

log = logging.getLogger(__name__)


async def fetch_http_image(source: dict, archive_root: Path) -> None:
    source_dir = archive_root / source["id"]
    source_dir.mkdir(parents=True, exist_ok=True)
    latest = source_dir / "latest.jpg"

    try:
        verify = source.get("verify_ssl", True)
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, verify=verify) as client:
            resp = await client.get(source["url"])
            resp.raise_for_status()
        data = resp.content
    except Exception as exc:
        log.warning("fetch %s failed: %s", source["id"], exc)
        return

    new_hash = hashlib.md5(data).hexdigest()
    if latest.exists() and hashlib.md5(latest.read_bytes()).hexdigest() == new_hash:
        log.debug("%s: no change", source["id"])
        return

    thumb_cfg = source.get("thumbnail", {})
    w = thumb_cfg.get("width", 640)
    h = thumb_cfg.get("height", 480)

    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((w, h), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
        img_bytes = buf.getvalue()
    except Exception as exc:
        log.warning("image processing %s failed: %s", source["id"], exc)
        return

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    (source_dir / f"{ts}.jpg").write_bytes(img_bytes)
    latest.write_bytes(img_bytes)
    log.info("%s: saved frame %s", source["id"], ts)


def cleanup_old_files(source: dict, archive_root: Path) -> None:
    source_dir = archive_root / source["id"]
    if not source_dir.exists():
        return

    retention_secs = source.get("retention", 48) * 3600
    cutoff = datetime.now(timezone.utc).timestamp() - retention_secs
    removed = 0
    for f in source_dir.glob("2*.jpg"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            removed += 1
    if removed:
        log.info("%s: removed %d old frames", source["id"], removed)
