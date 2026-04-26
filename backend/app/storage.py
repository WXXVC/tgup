from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from .config import SETTINGS_PATH
from .models import AppSettings


class SettingsStore:
    def __init__(self, path: Path = SETTINGS_PATH) -> None:
        self.path = path

    def load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        migrated = self._migrate_legacy_payload(payload)
        settings = AppSettings.model_validate(migrated)
        if migrated != payload:
            self.save(settings)
        return settings

    def save(self, settings: AppSettings) -> AppSettings:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(settings.model_dump(mode="json"), ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        return settings

    def _migrate_legacy_payload(self, payload: dict) -> dict:
        migrated = dict(payload)
        legacy_bot_api = migrated.pop("bot_api", None)
        if "bot_api_accounts" not in migrated:
            migrated["bot_api_accounts"] = []
        if legacy_bot_api and (
            legacy_bot_api.get("server_url") or legacy_bot_api.get("bot_token")
        ):
            account_id = "migrated-default"
            migrated["bot_api_accounts"] = [
                {
                    "id": account_id,
                    "name": "Default Bot",
                    "server_url": legacy_bot_api.get("server_url")
                    or "https://api.telegram.org",
                    "bot_token": legacy_bot_api.get("bot_token") or "",
                    "enabled": True,
                }
            ]
            migrated.setdefault("bot_dispatch_mode", "single")
            migrated.setdefault("default_bot_api_account_id", account_id)
            for channel in migrated.get("channels", []):
                if not channel.get("bot_api_account_id"):
                    channel["bot_api_account_id"] = account_id
        migrated.setdefault("bot_dispatch_mode", "single")
        migrated.setdefault("default_bot_api_account_id", "")
        migrated.setdefault("smart_queue_scheduling_enabled", False)
        for channel in migrated.get("channels", []):
            channel.setdefault("bot_api_account_id", "")
        for account in migrated.get("bot_api_accounts", []):
            account.setdefault("id", str(uuid4()))
            account.setdefault("name", "Bot API Account")
            account.setdefault("server_url", "https://api.telegram.org")
            account.setdefault("bot_token", "")
            account.setdefault("enabled", True)
            account.setdefault("send_rate_limit_per_minute", 20)
            account.setdefault("send_rate_limit_per_channel_per_minute", 10)
            account.setdefault("send_jitter_min_ms", 300)
            account.setdefault("send_jitter_max_ms", 1200)
            account.setdefault("auto_slowdown_enabled", True)
            account.setdefault("auto_slowdown_factor_percent", 50)
            account.setdefault("auto_slowdown_duration_seconds", 600)
        return migrated
