"""
Shared image save logic for all source types.

- Timestamped files are always full-size (no resizing).
- latest.jpg is an ROI crop if the source has geo_bounds and settings has roi,
  otherwise a scaled thumbnail.
- latest.md5 is a sidecar storing the MD5 of the last raw download so the hash
  check stays correct when latest.jpg is a crop (its hash differs from the raw).
"""

import hashlib
import io
import math
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image


# ── Geo helpers ─────────────────────────────────────────────────────────────

def merc_y(lat_deg: float) -> float:
    """Web Mercator y for a latitude (Gudermannian inverse)."""
    return math.log(math.tan(math.pi / 4 + math.radians(lat_deg) / 2))


def geo_to_pixel(lat: float, lon: float, geo_bounds: dict,
                 img_w: int, img_h: int) -> tuple[int, int]:
    """Lat/lon → pixel (x, y) in a Mercator-projected image with known tile bounds."""
    n, s = geo_bounds["north"], geo_bounds["south"]
    w, e = geo_bounds["west"],  geo_bounds["east"]
    x = round((lon - w) / (e - w) * img_w)
    y = round((merc_y(n) - merc_y(lat)) / (merc_y(n) - merc_y(s)) * img_h)
    return x, y


def crop_box(cx: int, cy: int, cw: int, ch: int,
             img_w: int, img_h: int) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) crop centered on (cx,cy), clamped to image."""
    left = max(0, min(cx - cw // 2, img_w - cw))
    top  = max(0, min(cy - ch // 2, img_h - ch))
    return left, top, left + cw, top + ch


# ── Save ─────────────────────────────────────────────────────────────────────

def save_image(
    data: bytes,
    source: dict,
    archive_root: Path,
    ts: datetime | None = None,
    update_latest: bool = True,
) -> bool:
    """
    Persist a freshly downloaded image.

    Returns True if a new timestamped frame was written.

    ts:            frame time (UTC). Defaults to now, for live single-frame
                   sources. Backfill callers pass each frame's real capture time
                   so the archive filename reflects when the frame was taken.
    update_latest: also refresh latest.jpg (grid thumbnail) and latest.md5.
                   Backfill loops set this only for the newest frame, so an older
                   recovered frame never overwrites the current view.

    Dedup:
      - ts given (backfill): by archive filename, so re-running a fetch after a
        gap re-downloads nothing it already has.
      - ts None  (live):     by latest.md5, so an unchanged download isn't
        re-encoded and re-archived.

    Archive layout:
        {source_id}/YYYYMMDD_HHMMSS.jpg   — full-size, every new frame
        {source_id}/latest.jpg            — ROI crop or thumbnail for grid view
        {source_id}/latest.md5            — MD5 of the latest raw download
        {source_id}/full_size.txt         — "{width} {height}" of archived frames
    """
    source_dir = archive_root / source["id"]
    source_dir.mkdir(parents=True, exist_ok=True)

    stamp = (ts or datetime.now(timezone.utc)).strftime("%Y%m%d_%H%M%S")
    dest = source_dir / f"{stamp}.jpg"
    md5_file = source_dir / "latest.md5"
    new_hash = hashlib.md5(data).hexdigest()

    if ts is None:
        # Live source: skip identical consecutive downloads.
        if md5_file.exists() and md5_file.read_text().strip() == new_hash:
            return False
    elif dest.exists():
        # Backfill: already have this exact frame.
        return False

    img = Image.open(io.BytesIO(data)).convert("RGB")
    img_w, img_h = img.size

    # ── Full-size timestamped archive ─────────────────────────────────────
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    dest.write_bytes(buf.getvalue())
    (source_dir / "full_size.txt").write_text(f"{img_w} {img_h}")

    if not update_latest:
        return True

    # ── latest.jpg: ROI crop or scaled thumbnail ──────────────────────────
    roi = source.get("_roi")
    geo_bounds = source.get("geo_bounds")

    if roi and geo_bounds:
        cx, cy = geo_to_pixel(roi["lat"], roi["lon"], geo_bounds, img_w, img_h)
        cw, ch = roi["thumb_w"], roi["thumb_h"]
        box = crop_box(cx, cy, cw, ch, img_w, img_h)
        display = img.crop(box)
    else:
        thumb_cfg = source.get("thumbnail", {})
        tw = thumb_cfg.get("width", 640)
        th = thumb_cfg.get("height", 480)
        display = img.copy()
        display.thumbnail((tw, th), Image.LANCZOS)

    buf = io.BytesIO()
    display.save(buf, format="JPEG", quality=88)
    (source_dir / "latest.jpg").write_bytes(buf.getvalue())
    md5_file.write_text(new_hash)

    return True
