import importlib
import logging
import sys
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import load_settings, load_sources
from .fetcher import cleanup_old_files, fetch_http_image

log = logging.getLogger(__name__)

_scheduler = AsyncIOScheduler()


def start_scheduler() -> None:
    settings = load_settings()
    sources = load_sources()
    archive_root = Path(settings["archive_dir"])

    roi = settings.get("roi")
    for source in sources:
        if roi:
            source["_roi"] = roi   # available to save_image via source dict
        interval = source.get("interval", 300)
        sid = source["id"]

        fetch_fn = resolve_fetch_fn(source)
        if fetch_fn is None:
            continue

        _scheduler.add_job(
            fetch_fn,
            "interval",
            seconds=interval,
            args=[source, archive_root],
            id=f"fetch_{sid}",
            max_instances=1,
            next_run_time=datetime.now(),   # run immediately on startup
            misfire_grace_time=interval,
        )
        _scheduler.add_job(
            cleanup_old_files,
            "interval",
            hours=1,
            args=[source, archive_root],
            id=f"cleanup_{sid}",
        )
        log.info("scheduled %s (%s) every %ds", sid, source.get("type", "http_image"), interval)

    _scheduler.start()
    log.info("scheduler started with %d sources", len(sources))


def stop_scheduler() -> None:
    _scheduler.shutdown(wait=False)


def resolve_fetch_fn(source: dict):
    """Return the fetch coroutine for a source, or None if it can't be resolved."""
    source_type = source.get("type", "http_image")
    if source_type == "http_image":
        return fetch_http_image
    if source_type == "custom":
        return _load_custom(source.get("module", source["id"]))
    log.warning("unknown source type %r for %s — skipping", source_type, source["id"])
    return None


def _load_custom(module_name: str):
    """Load a fetch function from custom_sources/<module_name>.py."""
    custom_dir = Path("custom_sources")
    if str(custom_dir) not in sys.path:
        sys.path.insert(0, str(custom_dir))
    try:
        mod = importlib.import_module(module_name)
        return mod.fetch
    except Exception as exc:
        log.error("failed to load custom source %r: %s", module_name, exc)
        return None
