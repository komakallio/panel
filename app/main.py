import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import load_settings, load_sources
from .overlay import render_overlays
from .scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

settings = load_settings()
sources = load_sources()
archive_path = Path(settings["archive_dir"])
archive_path.mkdir(parents=True, exist_ok=True)

templates = Jinja2Templates(directory="app/templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="KK Dashboard", lifespan=lifespan)

# Fallback static serving when nginx is not in front (local dev)
app.mount("/static", StaticFiles(directory=str(archive_path)), name="static")


def _build_tile_ctx(source: dict) -> dict:
    latest = archive_path / source["id"] / "latest.jpg"
    return {
        "source": source,
        "has_image": latest.exists(),
        "overlays": render_overlays(source, archive_path),
        "now": int(datetime.now(timezone.utc).timestamp()),
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


@app.get("/api/frames/{source_id}")
async def frames(source_id: str, n: int = 48):
    source_dir = archive_path / source_id
    if not source_dir.exists():
        return {"source": source_id, "frames": []}
    files = sorted(source_dir.glob("2*.jpg"))[-n:]
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
