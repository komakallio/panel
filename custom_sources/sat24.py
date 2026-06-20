"""
Custom source: sat24.com composite satellite/radar images for Finland.

All four sat24 sources share a single page fetch (cached 90s) so the
scheduler firing them close together only causes one HTTP request.

The Infoplaza/Maptiler border+coast overlay is fetched once and composited
server-side (Pillow alpha_composite) so borders appear in the animation
player too.

Source YAML must include:
    type: custom
    module: sat24
    sat24_layer: <euVisible|euInfra|euMicro|euRadarSat>
"""

import asyncio
import hashlib
import io
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from PIL import Image

from app.events import notify_new_image

log = logging.getLogger(__name__)

SAT24_PAGE  = "https://www.sat24.com/en-gb/country/fi"
RUST_LAYERS = "https://imn-rust-lb.infoplaza.io/v4/nowcast/tiles"

# Center+zoom that matches the sat24 euXxx tile extent (zoom 6, tiles x=[33,40] y=[14,20])
# → west=5.625°E, east=45.0°E, north≈71.1°N, south≈56.5°N
_BORDER_BASE = (
    "https://maptiler.infoplaza.io/api/maps/Border/static/"
    "25.3125,63.8,6/{w}x{h}.png?attribution=false"
)

_page_cache:   tuple[str, float] | None = None
_page_lock   = asyncio.Lock()
_CACHE_TTL   = 90   # seconds — all four sources share one page fetch

_border_cache: bytes | None = None
_border_lock = asyncio.Lock()


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


async def _get_border(width: int, height: int) -> bytes | None:
    """Fetch border+coast PNG once; cached for the process lifetime."""
    global _border_cache
    async with _border_lock:
        if _border_cache is not None:
            return _border_cache
        url = _BORDER_BASE.format(w=width, h=height)
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
            _border_cache = resp.content
            log.info("sat24: border overlay cached (%d bytes)", len(_border_cache))
            return _border_cache
        except Exception as exc:
            log.warning("sat24: border fetch failed: %s", exc)
            return None


def _latest_image_url(html: str, layer_key: str) -> str | None:
    """Return the most recent composite image URL for layer_key, or None."""
    marker = f"radarAvaliableLayers[0]['{layer_key}']"
    idx = html.find(marker)
    if idx == -1:
        log.warning("sat24: layer key %r not found in page", layer_key)
        return None

    chunk = html[idx: idx + 60_000]
    next_layer = chunk.find("radarAvaliableLayers[0][", len(marker))
    if next_layer > 0:
        chunk = chunk[:next_layer]

    urls = re.findall(r'"url":"([^"]+)"', chunk)
    if not urls:
        log.warning("sat24: no urls found for layer %r", layer_key)
        return None

    return RUST_LAYERS + urls[-1]


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

    url = _latest_image_url(html, layer_key)
    if not url:
        return

    source_dir = archive_root / source_id
    source_dir.mkdir(parents=True, exist_ok=True)
    latest = source_dir / "latest.jpg"

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.content
    except Exception as exc:
        log.warning("sat24 %s: image fetch failed: %s", source_id, exc)
        return

    new_hash = hashlib.md5(data).hexdigest()
    if latest.exists() and hashlib.md5(latest.read_bytes()).hexdigest() == new_hash:
        log.debug("sat24 %s: no change", source_id)
        return

    thumb = source.get("thumbnail", {})
    tw, th = thumb.get("width", 640), thumb.get("height", 640)
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        natural_size = img.size  # e.g. (1792, 1536) — fetch border at this resolution

        border_data = await _get_border(*natural_size)
        if border_data:
            border_img = Image.open(io.BytesIO(border_data)).convert("RGBA")
            if border_img.size != natural_size:
                border_img = border_img.resize(natural_size, Image.LANCZOS)
            composited = Image.alpha_composite(img.convert("RGBA"), border_img)
            img = composited.convert("RGB")

        img.thumbnail((tw, th), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
        img_bytes = buf.getvalue()
    except Exception as exc:
        log.warning("sat24 %s: image processing failed: %s", source_id, exc)
        return

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    (source_dir / f"{ts}.jpg").write_bytes(img_bytes)
    latest.write_bytes(img_bytes)
    log.info("sat24 %s: saved frame %s", source_id, ts)

    notify_new_image(source_id)
