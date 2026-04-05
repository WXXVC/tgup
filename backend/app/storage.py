from __future__ import annotations

import json
from pathlib import Path

from .config import SETTINGS_PATH
from .models import AppSettings


class SettingsStore:
    def __init__(self, path: Path = SETTINGS_PATH) -> None:
        self.path = path

    def load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings()
        return AppSettings.model_validate(json.loads(self.path.read_text(encoding="utf-8")))

    def save(self, settings: AppSettings) -> AppSettings:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(settings.model_dump(mode="json"), ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        return settings
