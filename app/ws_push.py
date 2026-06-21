"""
WebSocket push listeners.

Some sources announce a new frame over a WebSocket instead of (only) being
polled. taivas.komakallio.fi watches its allsky image and broadcasts
{"type":"imageUpdate"} to wss://.../ws whenever the file is rewritten. A source
opts in with:

    push_ws: "wss://host/ws"

For each such source we hold a persistent connection and trigger an immediate
fetch on every imageUpdate, so frames arrive in real time rather than on the
next poll. The scheduled poll stays on as a slow fallback for when the socket
is down (and a fetch also runs on every (re)connect to catch missed frames).
"""

import asyncio
import json
import logging
from pathlib import Path

from websockets.asyncio.client import connect

from .scheduler import resolve_fetch_fn

log = logging.getLogger(__name__)

_tasks: list[asyncio.Task] = []


async def _safe_fetch(fetch_fn, source: dict, archive_root: Path) -> None:
    try:
        await fetch_fn(source, archive_root)
    except Exception as exc:
        log.warning("ws %s: fetch failed: %s", source["id"], exc)


async def _listen(source: dict, archive_root: Path) -> None:
    sid = source["id"]
    url = source["push_ws"]
    fetch_fn = resolve_fetch_fn(source)
    if fetch_fn is None:
        log.error("ws %s: cannot resolve fetch function — push disabled", sid)
        return

    backoff = 1
    while True:
        try:
            async with connect(url, open_timeout=15, ping_interval=20, ping_timeout=20) as ws:
                log.info("ws %s: connected to %s", sid, url)
                backoff = 1
                # Catch anything published while we were disconnected.
                await _safe_fetch(fetch_fn, source, archive_root)
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except (ValueError, TypeError):
                        continue
                    if msg.get("type") == "imageUpdate":
                        log.info("ws %s: imageUpdate push -> fetching", sid)
                        await _safe_fetch(fetch_fn, source, archive_root)
        except asyncio.CancelledError:
            log.info("ws %s: listener stopped", sid)
            raise
        except Exception as exc:
            log.warning("ws %s: connection lost (%s) — retry in %ds", sid, exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


def start_push_listeners(sources: list[dict], archive_root: Path) -> None:
    for source in sources:
        if source.get("push_ws"):
            _tasks.append(asyncio.create_task(_listen(source, archive_root)))
    if _tasks:
        log.info("started %d websocket push listener(s)", len(_tasks))


async def stop_push_listeners() -> None:
    for t in _tasks:
        t.cancel()
    for t in _tasks:
        try:
            await t
        except asyncio.CancelledError:
            pass
    _tasks.clear()
