# CLAUDE.md — KK Dashboard

Live sky/weather dashboard for Komakallio Observatory (all-sky cameras +
satellite/radar), served at https://panel.komakallio.fi. FastAPI + APScheduler +
HTMX; no database — frames are JPEG files under `archive/`.

**Read [ONBOARDING.md](ONBOARDING.md) for the full picture** (architecture, source
types, `save_image`, backfill, the custom scrapers) and **[DEPLOY.md](DEPLOY.md)**
for deployment. This file is just the must-knows.

## Commands
- Run locally: `uvicorn app.main:app --reload` → http://localhost:8000 (venv +
  `requirements.txt`).
- Deploy: SSH to the VPS, `cd ~/panel && ./deploy.sh`. **`deploy.sh` runs on the
  server, not in your local shell.**
- Config-only change (`config/*.yaml`): `docker compose restart app` — no rebuild.

## Must-know rules
- **The GitHub repo (`komakallio/panel`) is public.** Never commit secrets; they
  live only in the VPS `.env` (gitignored).
- **Add a source** by editing `config/sources.yaml`: `http_image` needs no code;
  `custom` needs `custom_sources/<module>.py` with
  `async def fetch(source, archive_root)`.
- **sat24 timestamps differ per layer**: satellite = 12-digit, radar obs =
  14-digit, radar recent = `base±offset`. If a layer goes silent after a change,
  suspect `_parse_ts` in `custom_sources/sat24.py`.
- **Age badges show the frame's real capture time**, not save time. sat24 reading
  ~15–25 min old is normal source latency, not a bug; `stale_after` is set above
  it so red means a genuine stall.
- Source page formats drift — **validate scraper changes against the live page
  before committing** (reuse the real functions, e.g.
  `from custom_sources.sat24 import _get_page, _frame_urls`).

## Working style
- Commit each time a change is confirmed working; commit messages explain the *why*.
- Keep throwaway probe/test scripts out of the repo (use a scratch dir); only
  durable tools go in `scripts/`.
