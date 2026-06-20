from datetime import datetime, timezone
from pathlib import Path


def render_overlays(source: dict, archive_root: Path) -> list[dict]:
    """Convert source overlay config into template-ready dicts."""
    result = []
    source_dir = archive_root / source["id"]

    for ov in source.get("overlays", []):
        ov_type = ov.get("type")

        if ov_type == "age":
            text, stale = _image_age(source_dir, ov.get("stale_after", 3600))
            result.append({
                "type": "age",
                "text": text,
                "stale": stale,
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


def _image_age(source_dir: Path, stale_after: int) -> tuple[str, bool]:
    latest = source_dir / "latest.jpg"
    if not latest.exists():
        return "no image", True

    age_s = datetime.now(timezone.utc).timestamp() - latest.stat().st_mtime
    if age_s < 60:
        text = "just now"
    elif age_s < 3600:
        text = f"{int(age_s / 60)} min ago"
    elif age_s < 86400:
        text = f"{int(age_s / 3600)} h ago"
    else:
        text = f"{int(age_s / 86400)} d ago"

    return text, age_s > stale_after


def _latlon_to_pct(lat, lon, north, south, west, east) -> tuple[float, float]:
    x = round((lon - west) / (east - west) * 100, 1)
    y = round((north - lat) / (north - south) * 100, 1)
    return x, y
