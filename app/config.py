from pathlib import Path
import yaml

_CONFIG_DIR = Path("config")


def load_settings() -> dict:
    with open(_CONFIG_DIR / "settings.yaml") as f:
        return yaml.safe_load(f)


def load_sources() -> list[dict]:
    with open(_CONFIG_DIR / "sources.yaml") as f:
        data = yaml.safe_load(f)
    return data.get("sources", [])
