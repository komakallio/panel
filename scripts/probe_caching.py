#!/usr/bin/env python3
"""Probe the HTTP caching capabilities of image sources.

For each `http_image` source in config/sources.yaml (or a URL given on the
command line), report whether the server supports HEAD, Last-Modified, ETag,
and conditional requests (If-Modified-Since / If-None-Match). This tells us
how cheaply the fetcher can poll it, and which validator it will rely on.

Run this whenever adding a new source to see what polling strategy it gets.

Usage:
    python scripts/probe_caching.py                  # all http_image sources
    python scripts/probe_caching.py <url> [--insecure]
"""
import sys
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "config" / "sources.yaml"


def human(n: float | None) -> str:
    if n is None:
        return "?"
    n = float(n)
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} MB"


def probe(name: str, url: str, verify: bool = True) -> None:
    print(f"\n=== {name} ===")
    print(f"  {url}")
    try:
        with httpx.Client(timeout=30, follow_redirects=True, verify=verify) as c:
            g = c.get(url)
            size = len(g.content)
            lm = g.headers.get("Last-Modified")
            etag = g.headers.get("ETag")
            cc = g.headers.get("Cache-Control", "-")
            print(f"  GET   : {g.status_code}  {human(size)}  "
                  f"Last-Modified={lm or '-'}  ETag={etag or '-'}  Cache-Control={cc}")

            head_lm = head_etag = None
            try:
                h = c.head(url)
                head_lm = h.headers.get("Last-Modified")
                head_etag = h.headers.get("ETag")
                head_ok = h.status_code < 400
                print(f"  HEAD  : {h.status_code}  "
                      f"Last-Modified={head_lm or '-'}  ETag={head_etag or '-'}")
            except Exception as e:
                head_ok = False
                print(f"  HEAD  : FAILED ({type(e).__name__})")

            ims_304 = inm_304 = None
            if lm:
                r = c.get(url, headers={"If-Modified-Since": lm})
                ims_304 = r.status_code == 304
                detail = "304 (cheap)" if ims_304 else f"{r.status_code}, {human(len(r.content))} re-sent"
                print(f"  If-Modified-Since : {detail}")
            if etag:
                r = c.get(url, headers={"If-None-Match": etag})
                inm_304 = r.status_code == 304
                detail = "304 (cheap)" if inm_304 else f"{r.status_code}, {human(len(r.content))} re-sent"
                print(f"  If-None-Match     : {detail}")

            # What the fetcher will effectively use
            if head_ok and (head_lm or head_etag):
                via = "Last-Modified" if head_lm else "ETag"
                strategy = f"HEAD probe on {via} — redundant polls are header-only, no image download"
            elif ims_304:
                strategy = "conditional GET (If-Modified-Since 304) — cheap revalidation"
            elif inm_304:
                strategy = "conditional GET (If-None-Match 304) — cheap revalidation"
            else:
                strategy = ("NO validators — every poll re-downloads the full image "
                            "(MD5 dedup still prevents re-archiving). Avoid fast polling.")
            print(f"  -> {strategy}")
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")


def main() -> None:
    positional = [a for a in sys.argv[1:] if not a.startswith("-")]
    insecure = "--insecure" in sys.argv[1:]
    if positional:
        probe("cli", positional[0], verify=not insecure)
        return
    data = yaml.safe_load(SOURCES.read_text(encoding="utf-8"))
    http_sources = [s for s in data.get("sources", [])
                    if s.get("type", "http_image") == "http_image"]
    if not http_sources:
        print("No http_image sources found in", SOURCES)
        return
    for s in http_sources:
        probe(s["id"], s["url"], verify=s.get("verify_ssl", True))


if __name__ == "__main__":
    main()
