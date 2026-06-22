"""
Custom source: sat24.com composite satellite/radar images for Finland.

All four sat24 sources share a single page fetch (cached 90s) so the
scheduler firing them close together only causes one HTTP request.

The page lists ~25 recent frames per layer (5-min spacing, ~2 h). Every fetch
backfills any of those we're missing, so a gap up to ~2 h is recovered on the
next successful poll; in steady state only the newest frame is downloaded.

Border/coast overlays are rendered browser-side (see sources.yaml border_url).

Source YAML must include:
    type: custom
    module: sat24
    sat24_layer: <euVisible|euInfra|euMicro|euRadarSat>
"""

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.backfill import backfill_frames

log = logging.getLogger(__name__)

SAT24_PAGE  = "https://www.sat24.com/en-gb/country/fi"
RUST_LAYERS = "https://imn-rust-lb.infoplaza.io/v4/nowcast/tiles"

_page_cache: tuple[str, float] | None = None
_page_lock  = asyncio.Lock()
_CACHE_TTL  = 90  # seconds — all four sources share one page fetch

# The composite path carries the frame's UTC time: /.../202606221210/6/14/33/...
_TS_RE = re.compile(r"/(\d{12})/")


async def _get_page() -> str:
    global _page_cache
    async with _page_lock:
        if _page_cache and (time.monotonic() - _page_cache[1]) < _CACHE_TTL:
            return _page_cache[0]
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(SAT24_PAGE)
            resp.raise_for_status()
        _page_cache = (resp.text, time.monotonic())
        log.debug("sat24: page fetched and cached")
        return _page_cache[0]


def _frame_urls(html: str, layer_key: str) -> list[tuple[datetime, str]]:
    """All (utc_time, image_url) frames for layer_key, oldest first."""
    marker = f"radarAvaliableLayers[0]['{layer_key}']"
    idx = html.find(marker)
    if idx == -1:
        log.warning("sat24: layer key %r not found in page", layer_key)
        return []

    chunk = html[idx: idx + 60_000]
    next_layer = chunk.find("radarAvaliableLayers[0][", len(marker))
    if next_layer > 0:
        chunk = chunk[:next_layer]

    frames: list[tuple[datetime, str]] = []
    for path in re.findall(r'"url":"([^"]+)"', chunk):
        m = _TS_RE.search(path)
        if not m:
            continue
        ts = datetime.strptime(m.group(1), "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
        frames.append((ts, RUST_LAYERS + path))

    if not frames:
        log.warning("sat24: no timestamped urls found for layer %r", layer_key)
    return sorted(frames)


async def fetch(source: dict, archive_root: Path) -> None:
    source_id = source["id"]
    layer_key = source.get("sat24_layer")
    if not layer_key:
        log.error("sat24: source %s missing sat24_layer", source_id)
        return

    try:
        html = await _get_page()
    except Exception as exc:
        log.warning("sat24: page fetch failed: %s", exc)
        return

    frames = _frame_urls(html, layer_key)
    if not frames:
        return

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        saved = await backfill_frames(source, archive_root, frames, client)

    if saved:
        log.info("sat24 %s: archived %d new frame(s)", source_id, saved)
    else:
        log.debug("sat24 %s: no new frames", source_id)
