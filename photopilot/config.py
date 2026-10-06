"""Settings remembered between runs, and where the program keeps its own files.

Windows: %APPDATA%\\photo-pilot\\
Linux:   ~/.config/photo-pilot/
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def app_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "photo-pilot"


def config_path() -> Path:
    return app_dir() / "config.json"


def preview_dir() -> Path:
    """Detection previews live here, NOT in the photo folder (Immich would import them)."""
    return app_dir() / "previews"


def default_out_dir() -> Path:
    pictures = Path.home() / "Pictures"
    return (pictures if pictures.is_dir() else Path.home()) / "Scanned photos"


def load() -> dict:
    try:
        return json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(cfg: dict) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return path
