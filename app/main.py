import json
import logging
import math
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import load_settings, load_sources
from .events import event_stream
from .image_utils import crop_box, geo_to_pixel, merc_y
from .overlay import render_overlays
from .scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

settings = load_settings()
sources = load_sources()
archive_path = Path(settings["archive_dir"])
archive_path.mkdir(parents=True, exist_ok=True)

templates = Jinja2Templates(directory="app/templates")
templates.env.filters["tojson"] = json.dumps


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="KK Dashboard", lifespan=lifespan)

# Fallback static serving when nginx is not in front (local dev)
app.mount("/static", StaticFiles(directory=str(archive_path)), name="static")


def _thumb_border_url(source: dict, roi_crop: dict | None) -> str | None:
    """Maptiler border URL sized/centred to match the ROI-cropped thumbnail."""
    full_border = source.get("border_url")
    if not full_border or not roi_crop:
        return full_border
    geo = source.get("geo_bounds")
    if not geo:
        return full_border

    cx = roi_crop["x"] + roi_crop["w"] / 2
    cy = roi_crop["y"] + roi_crop["h"] / 2
    fw, fh = roi_crop["fullW"], roi_crop["fullH"]
    n, s, wl, e = geo["north"], geo["south"], geo["west"], geo["east"]

    center_lon = wl + cx / fw * (e - wl)
    mn, ms = merc_y(n), merc_y(s)
    mc = mn - cy / fh * (mn - ms)
    center_lat = math.degrees(2 * math.atan(math.exp(mc)) - math.pi / 2)

    # Same pixel density as the original image → same zoom.
    # MapTiler's static API uses 512px tiles, so its zoom is the slippy/256px
    # tile zoom minus 1 (i.e. divide by 512, not 256). Verified pixel-aligned.
    zoom = math.log2((fw / (e - wl)) * 360 / 512)

    cw, ch = roi_crop["w"], roi_crop["h"]
    base = full_border.split("/static/")[0]
    return f"{base}/static/{center_lon:.4f},{center_lat:.4f},{zoom:.2f}/{cw}x{ch}.png?attribution=false"


def _roi_crop(source: dict) -> dict | None:
    """Pixel crop box in the full-size archived image for the player."""
    roi = settings.get("roi")
    geo_bounds = source.get("geo_bounds")
    if not roi or not geo_bounds:
        return None
    size_file = archive_path / source["id"] / "full_size.txt"
    if not size_file.exists():
        return None
    try:
        img_w, img_h = map(int, size_file.read_text().split())
    except Exception:
        return None
    cx, cy = geo_to_pixel(roi["lat"], roi["lon"], geo_bounds, img_w, img_h)
    cw, ch = roi["thumb_w"], roi["thumb_h"]
    x, y, x2, y2 = crop_box(cx, cy, cw, ch, img_w, img_h)
    return {"x": x, "y": y, "w": x2 - x, "h": y2 - y, "fullW": img_w, "fullH": img_h}


def _build_tile_ctx(source: dict) -> dict:
    latest = archive_path / source["id"] / "latest.jpg"
    rc = _roi_crop(source)
    return {
        "source": source,
        "has_image": latest.exists(),
        "overlays": render_overlays(source, archive_path),
        "now": int(datetime.now(timezone.utc).timestamp()),
        "roi_crop": rc,
        "thumb_border_url": _thumb_border_url(source, rc),
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    tiles = [_build_tile_ctx(s) for s in sources]
    return templates.TemplateResponse("index.html", {"request": request, "tiles": tiles})


@app.get("/tile/{source_id}", response_class=HTMLResponse)
async def tile(request: Request, source_id: str):
    source = next((s for s in sources if s["id"] == source_id), None)
    if source is None:
        raise HTTPException(status_code=404)
    ctx = _build_tile_ctx(source)
    ctx["request"] = request
    return templates.TemplateResponse("tile.html", ctx)


@app.get("/api/events")
async def events():
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # tells nginx not to buffer this stream
        },
    )


@app.get("/api/frames/{source_id}")
async def frames(source_id: str, hours: float = 0, n: int = 240):
    """Frames for the player. hours>0 limits to that window (0 = all archived).
    Result is capped at n frames, subsampled evenly (oldest + newest kept)."""
    source_dir = archive_path / source_id
    if not source_dir.exists():
        return {"source": source_id, "frames": []}
    files = sorted(source_dir.glob("2*.jpg"))

    if hours > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        kept = []
        for f in files:
            try:
                dt = datetime.strptime(f.stem, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if dt >= cutoff:
                kept.append(f)
        files = kept

    total = len(files)
    if total > n:
        # even subsample across the window, always keeping first and last
        idx = sorted({round(i * (total - 1) / (n - 1)) for i in range(n)})
        files = [files[j] for j in idx]

    return {
        "source": source_id,
        "frames": [
            {"url": f"/static/{source_id}/{f.name}", "ts": _parse_ts(f.stem)}
            for f in files
        ],
    }


def _parse_ts(stem: str) -> str:
    """20250620_143022 → 2025-06-20 14:30 UTC"""
    try:
        dt = datetime.strptime(stem, "%Y%m%d_%H%M%S")
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return stem
