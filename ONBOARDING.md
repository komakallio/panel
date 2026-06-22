# KK Dashboard — project handoff

A live weather/sky dashboard for **Komakallio Observatory** (southern Finland):
all-sky cameras, plus satellite and radar imagery, on one auto-refreshing page.
Production: **https://panel.komakallio.fi**.

It polls a handful of public image sources on a schedule, archives every frame,
and serves a grid of tiles (with a click-to-open animation player) that updates
live. Adding or changing a source is normally just YAML — no code.

---

## TL;DR for the next session

- **Run locally:** `uvicorn app.main:app --reload` (from the repo root, with the
  venv active) → http://localhost:8000.
- **Add a source:** edit [`config/sources.yaml`](config/sources.yaml). For a plain
  image URL you need *zero* Python.
- **Deploy:** SSH to the VPS and `cd ~/panel && ./deploy.sh` (runs **on the
  server**, not locally). Full runbook in [`DEPLOY.md`](DEPLOY.md).
- **Debug a source that stopped:** read the logs (`docker compose logs -f app`
  on the VPS), then reproduce the fetch/parse locally with a throwaway script
  (see *Working style* below). This is how every sat24/testbed issue was solved.
- This file is safe to copy to `CLAUDE.md` so future Claude Code sessions
  auto-load it.

---

## Stack

| Concern | Choice |
|---|---|
| Web framework | **FastAPI** + **uvicorn** |
| Scheduling | **APScheduler** (`AsyncIOScheduler`, in-process) |
| Frontend | server-rendered **Jinja2** + **HTMX**, vanilla JS, **SSE** for live updates |
| Image handling | **Pillow** |
| HTTP client | **httpx** (async) |
| Config | **YAML** (`config/*.yaml`) |
| Prod hosting | **Docker Compose** + **Caddy** (reverse proxy, automatic HTTPS) |

Dependencies are pinned in [`requirements.txt`](requirements.txt). Python 3.12.

No database — state is just files on disk under `archive/` (see below).

---

## Repo layout

```
app/
  main.py            FastAPI app: routes, tile context, /api/frames player API, lifespan
  config.py          load_settings() / load_sources() from config/*.yaml
  scheduler.py       APScheduler setup; resolve_fetch_fn() maps source type → fetch coro
  fetcher.py         fetch_http_image() (HEAD/conditional-GET caching) + cleanup_old_files()
  image_utils.py     save_image() (the heart of archiving) + geo/crop helpers
  backfill.py        backfill_frames(): recover missed frames for multi-frame sources
  ws_push.py         optional WebSocket push listeners (real-time, beats polling)
  overlay.py         render_overlays(): age / label / marker overlays (data for the template)
  events.py          SSE pub/sub (notify_new_image → browser)
  templates/
    index.html       the whole dashboard: grid, player modal, SSE, geo marker, live age JS
    tile.html        one grid tile (image + overlays); re-rendered on update
  widgets/
    twilight.html    self-hosted sun/moon chart, embedded in an iframe tile
config/
  sources.yaml       THE source list — edit this to add/remove/tune sources
  settings.yaml      archive_dir + the ROI (region-of-interest) used for crops
custom_sources/
  sat24.py           sat24.com satellite/radar scraper (4 layers, shared page fetch)
  testbed.py         FMI Helsinki Testbed radar scraper
Dockerfile, docker-compose.yml, Caddyfile, deploy.sh   prod stack
DEPLOY.md            manual deployment runbook
.env.example         SITE_HOST etc. (real .env is gitignored, lives only on the VPS)
scripts/             one-off probes (not part of the running app)
```

`archive/` (gitignored) is created at runtime and holds all imagery.

---

## Core concepts

### Sources (`config/sources.yaml`)
Each source has a `type`:
- **`http_image`** — fetch a URL, archive it. Pure config, no code. Used by the
  all-sky cameras (kk_allsky, metsahovi, the four Tampere cams).
- **`custom`** — `module: <name>` → `custom_sources/<name>.py` must expose
  `async def fetch(source, archive_root)`. Used by sat24 and testbed.
- **`embed`** — client-side iframe widget, nothing fetched (the twilight chart).

Common keys: `interval` (seconds between fetches), `retention` (hours to keep
frames), `thumbnail` (grid resize box), `overlays` (see below), `geo_bounds`
(lat/lon→pixel mapping for markers), `link` (where the tile links on click).
`push_ws` opts a source into WebSocket push (see ws_push.py).

### Archive layout (per source)
```
archive/<source_id>/
  YYYYMMDD_HHMMSS.jpg   full-size frame, one per new frame, filename = UTC frame time
  latest.jpg           current display image (ROI crop or thumbnail) for the grid
  full_size.txt        "{w} {h}" of the full frames (player uses it for ROI math)
  latest.md5           hash of the last raw download (live-source dedup)
  latest.etag/.lastmod HTTP validators (http_image conditional-GET caching)
```
Frame timestamps are **UTC**. For `http_image` the filename is the *save* time;
for sat24/testbed it is the source's *real frame* time parsed from the source.

### `save_image()` ([app/image_utils.py](app/image_utils.py)) — read this first
Signature: `save_image(data, source, archive_root, ts=None, update_latest=True)`.
- `ts=None` (live `http_image`): timestamp = now, dedup by `latest.md5` (skip
  unchanged downloads).
- `ts=<datetime>` (backfill): timestamp = the frame's real time, dedup by
  **filename existence** (re-runs are idempotent). `update_latest` is set only
  for the newest frame so an older recovered frame never clobbers `latest.jpg`.

### Backfill ([app/backfill.py](app/backfill.py))
sat24 and testbed expose a *window* of recent frames (not just the latest). Each
poll archives any frame in that window we don't already have, so a gap (app down,
missed poll) self-heals up to the window length. Steady-state cost is unchanged
(existence check skips frames already on disk).

### Overlays ([app/overlay.py](app/overlay.py))
Rendered as data for the template, drawn in the browser (nothing burned into the
JPEG):
- `age` — the image-age badge. **Ages from the frame's real capture time** (newest
  archived filename), not when we saved it; turns red past `stale_after`.
- `label` — static text. `marker` — a lat/lon dot (needs `geo_bounds`, or
  `proj_bounds` for EPSG:3067 like testbed).

### Frontend ([app/templates/index.html](app/templates/index.html))
One page. Server renders the grid; then: an **SSE** stream (`/api/events`)
refreshes a tile the instant a new frame is saved; a JS timer ticks the age
badges every second; clicking a tile opens a **player** modal that pulls frames
from `/api/frames/{id}` and animates them; an optional **geolocation marker** is
projected onto geo-referenced tiles.

### Routes ([app/main.py](app/main.py))
`GET /` dashboard · `GET /tile/{id}` one tile (HTMX refresh) · `GET /api/events`
SSE · `GET /api/frames/{id}?hours=&n=` player frame list (subsampled).

---

## The custom sources (where the subtlety lives)

### sat24 ([custom_sources/sat24.py](custom_sources/sat24.py))
Four dashboard sources (visible / infrared / night-microphysics / radar+sat)
share **one** sat24.com page fetch (90 s cache) so firing them together is a
single request. The page lists ~25 recent frames per layer as composite-tile
URLs; we parse the frame time out of each URL and backfill.

**Gotcha that bit us:** the layers don't share a timestamp format.
- satellite layers: 12-digit `YYYYMMDDHHMM` → `…/202606221340/…`
- radar observations: 14-digit `YYYYMMDDHHMMSS` → `…/20260622131000/…`
- radar's most recent ~25 min: a `runtime-base±offset` token →
  `…/202606221340+015/…` (= 13:40 + 15 min = 13:55). These are
  nowcast-extrapolated but valid for recent *past* times; they keep the radar
  tile as current as the satellite tiles. `_parse_ts()` handles all three.

### testbed ([custom_sources/testbed.py](custom_sources/testbed.py))
FMI Helsinki Testbed radar. The viewer page embeds two index-aligned JS arrays —
`anim_timestamps` (UTC) and `anim_images_*` (opaque, time-encoded `img.php`
URLs). We zip them and backfill. `n=15` in `page_url` = the max frames the page
lists (~75 min of recovery). The image URLs can't be constructed by hand, so
scraping the page is the only way in.

---

## Local development

```bash
python -m venv .venv && . .venv/Scripts/activate   # Windows; use bin/activate on *nix
pip install -r requirements.txt
uvicorn app.main:app --reload                       # http://localhost:8000
```
The app writes to `./archive`. To run the prod Docker image locally without
Caddy, `docker-compose.override.yml` (gitignored) publishes port 8000.

**Config-only changes** (editing `config/*.yaml`) need only an app restart —
in prod `docker compose restart app`, no rebuild.

---

## Deployment (summary — full details in [DEPLOY.md](DEPLOY.md))

Production runs on a small Hetzner VPS (SSH alias `petzval`) as Docker Compose:
the `app` container behind **Caddy**, which terminates HTTPS with an automatic
Let's Encrypt cert. The hostname comes from `SITE_HOST` in `.env` (gitignored,
server-only). HTTPS is required — the browser geolocation feature needs a secure
context.

```bash
# on the VPS:
cd ~/panel && ./deploy.sh        # git pull + docker compose up -d --build + log tail
```
`deploy.sh` runs **on the server**, not in your local shell. The free `*.dy.fi`
name is kept alive by a daily cron (see DEPLOY.md); a real A-record for
`panel.komakallio.fi` also points at the box.

> **Deploy state at handoff:** recent commits on `main` may be unpushed/undeployed.
> Check `git log origin/main..main`; push, then run `./deploy.sh` on the VPS.

---

## Operations & debugging

- **Logs:** `docker compose logs -f app` (or `--tail=100`, or `| grep -i sat24`).
  Successful archiving logs `… archived N new frame(s)` / `saved new frame`;
  parser problems log warnings (e.g. `no timestamped urls found for layer …`).
- **A source stopped updating:** logs first, then reproduce the fetch+parse in a
  throwaway script using the *real* functions (e.g. `from custom_sources.sat24
  import _get_page, _frame_urls`). The page formats change occasionally; this is
  almost always a parsing issue, not an outage.
- **Storage:** frames are pruned per-source by `retention` hours (hourly job).
  The Tampere 4K cams are the heaviest (~1 GB combined at 72 h). Archive files
  are **root-owned** in prod (container runs as root over a bind mount), so a
  manual `rm` needs `sudo`.
- **cleanup_old_frames.py** (untracked, repo root): a one-off used once during
  the backfill migration to drop pre-backfill frames. Safe to delete.

---

## Gotchas & hard-won lessons

- **sat24 radar ≠ satellite format.** See above. If radar (or a layer) goes
  silent after a change, suspect the timestamp parser.
- **Age badges show frame time.** sat24 reads ~15–20 min old, radar ~20–25 min —
  that's the source's real latency, *not* a bug. `stale_after` is set above it
  so red means a genuine stall.
- **http_image only archives on change** (md5 dedup), and the HEAD/ETag probe
  skips downloading unchanged images — so polling fast (e.g. Tampere at 15 s) is
  cheap. Poll faster than the source updates or you can miss frames (these have
  no backfill window).
- **`deploy.sh` is server-side.** Running it in local WSL gives `docker: command
  not found`.
- **Secrets** (`SITE_HOST`, any dy.fi credentials) live only in the VPS `.env`,
  never committed. The GitHub repo (`komakallio/panel`) is **public** — keep it
  secret-free.

---

## Conventions

- Small, focused commits; messages explain the *why*. The maintainer's habit is
  to **commit each time a change is confirmed working**.
- Throwaway inspection/test scripts go in a scratch dir, not the repo (the few
  kept ones live in `scripts/`).
- Validate source-parsing changes against the *live* page before committing —
  every sat24/testbed fix in the history was confirmed this way.
