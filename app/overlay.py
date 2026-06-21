from datetime import datetime, timezone
from pathlib import Path


def render_overlays(source: dict, archive_root: Path) -> list[dict]:
    """Convert source overlay config into template-ready dicts."""
    result = []
    source_dir = archive_root / source["id"]

    for ov in source.get("overlays", []):
        ov_type = ov.get("type")

        if ov_type == "age":
            stale_after = ov.get("stale_after", 3600)
            text, stale, mtime = _image_age(source_dir, stale_after)
            result.append({
                "type": "age",
                "text": text,
                "stale": stale,
                "mtime": mtime,            # epoch seconds, for live client-side ticking
                "stale_after": stale_after,
                "position": ov.get("position", "top-right"),
            })

        elif ov_type == "label":
            result.append({
                "type": "label",
                "text": ov.get("text", source.get("name", "")),
                "position": ov.get("position", "bottom-left"),
            })

        elif ov_type == "marker":
            geo = source.get("geo_bounds")
            if geo:
                x, y = _latlon_to_pct(
                    ov["lat"], ov["lon"],
                    geo["north"], geo["south"], geo["west"], geo["east"],
                )
                result.append({
                    "type": "marker",
                    "label": ov.get("label", ""),
                    "x_pct": x,
                    "y_pct": y,
                })

    return result


def _image_age(source_dir: Path, stale_after: int) -> tuple[str, bool, int | None]:
    latest = source_dir / "latest.jpg"
    if not latest.exists():
        return "no image", True, None

    mtime = latest.stat().st_mtime
    age_s = datetime.now(timezone.utc).timestamp() - mtime
    return _format_age(age_s), age_s > stale_after, round(mtime)


def _format_age(age_s: float) -> str:
    """Initial age text; the browser then updates it live each second.
    <1 min: seconds, 1-5 min: M:SS, then rounded minutes / hours / days."""
    if age_s < 0:
        age_s = 0
    if age_s < 60:
        return f"{int(age_s)}s ago"
    if age_s < 300:
        return f"{int(age_s // 60)}:{int(age_s % 60):02d} ago"
    if age_s < 3600:
        return f"{int(age_s / 60)} min ago"
    if age_s < 86400:
        return f"{int(age_s / 3600)} h ago"
    return f"{int(age_s / 86400)} d ago"


def _latlon_to_pct(lat, lon, north, south, west, east) -> tuple[float, float]:
    x = round((lon - west) / (east - west) * 100, 1)
    y = round((north - lat) / (north - south) * 100, 1)
    return x, y
