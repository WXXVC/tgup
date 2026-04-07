from __future__ import annotations

from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = DATA_DIR / "config"
SESSIONS_DIR = DATA_DIR / "sessions"
PREVIEWS_DIR = DATA_DIR / "previews"
TEMP_SEGMENTS_DIR = DATA_DIR / "tmp" / "video_segments"
DB_PATH = DATA_DIR / "app.db"
SETTINGS_PATH = CONFIG_DIR / "settings.json"


for directory in (DATA_DIR, CONFIG_DIR, SESSIONS_DIR, PREVIEWS_DIR, TEMP_SEGMENTS_DIR):
    directory.mkdir(parents=True, exist_ok=True)
