"""
Shared backfill helper for multi-frame sources (sat24, testbed).

Some sources expose not just the current frame but a short rolling window of
recent frames (sat24 lists ~25 at 5-min spacing; testbed up to 15). After the
app is down or a poll is missed, the next fetch can recover every frame in that
window we don't already have — so a gap in the archive shrinks to at most the
length of the source's own window.

Frames already on disk are skipped before any download (existence check on the
timestamped filename), so steady-state polling still costs one image per new
frame, and re-running a fetch is idempotent.
"""

import logging
from datetime import datetime
from pathlib import Path

import httpx

from .events import notify_new_image
from .image_utils import save_image

log = logging.getLogger(__name__)


async def backfill_frames(
    source: dict,
    archive_root: Path,
    frames: list[tuple[datetime, str]],
    client: httpx.AsyncClient,
) -> int:
    """
    Download and archive every frame in `frames` not already on disk.

    frames: (utc_time, url) pairs. The newest by time refreshes latest.jpg; the
            rest are archived without disturbing the current grid view.
    Returns the number of new frames saved; notifies SSE clients once if any.
    """
    if not frames:
        return 0

    source_id = source["id"]
    source_dir = archive_root / source_id
    newest_ts = max(ts for ts, _ in frames)

    saved = 0
    for ts, url in sorted(frames):
        dest = source_dir / f"{ts.strftime('%Y%m%d_%H%M%S')}.jpg"
        if dest.exists():
            continue
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.content
        except Exception as exc:
            log.warning("%s: frame %s fetch failed: %s", source_id, ts, exc)
            continue
        try:
            if save_image(data, source, archive_root,
                          ts=ts, update_latest=(ts == newest_ts)):
                saved += 1
        except Exception as exc:
            log.warning("%s: frame %s processing failed: %s", source_id, ts, exc)

    if saved:
        notify_new_image(source_id)
    return saved
