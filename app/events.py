import asyncio
import logging
from collections.abc import AsyncGenerator

log = logging.getLogger(__name__)

_subscribers: list[asyncio.Queue] = []


def notify_new_image(source_id: str) -> None:
    """Called by fetcher immediately after a new frame is saved."""
    for q in _subscribers:
        try:
            q.put_nowait(source_id)
        except asyncio.QueueFull:
            pass  # slow/stale client — drop this notification


async def event_stream() -> AsyncGenerator[str, None]:
    """One SSE stream per connected browser tab."""
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=32)
    _subscribers.append(q)
    log.debug("SSE client connected (%d total)", len(_subscribers))
    try:
        while True:
            try:
                source_id = await asyncio.wait_for(q.get(), timeout=30)
                yield f"data: {source_id}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"  # prevent proxy from closing idle connection
    except (asyncio.CancelledError, GeneratorExit):
        pass
    finally:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass
        log.debug("SSE client disconnected (%d remaining)", len(_subscribers))
