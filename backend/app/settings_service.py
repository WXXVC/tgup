from __future__ import annotations

import hashlib
import hmac
import secrets
from pathlib import Path
from uuid import uuid4

from .models import AccessPasswordRequest, AppSettings, ChannelConfig, ChannelPayload, FolderConfig, FolderPayload
from .storage import SettingsStore


class SettingsService:
    def __init__(self, store: SettingsStore) -> None:
        self.store = store
        self._settings = store.load()

    @property
    def settings(self) -> AppSettings:
        return self._settings

    def save(self) -> AppSettings:
        self._settings = self.store.save(self._settings)
        return self._settings

    def update_api(self, api_id: int, api_hash: str, phone_number: str) -> AppSettings:
        self._settings.api.api_id = api_id
        self._settings.api.api_hash = api_hash
        self._settings.api.phone_number = phone_number
        return self.save()

    def has_access_password(self) -> bool:
        return bool(self._settings.access_password_hash)

    def public_settings(self) -> dict:
        payload = self._settings.model_dump(mode="json")
        payload.pop("access_password_hash", None)
        payload["access_password_enabled"] = self.has_access_password()
        return payload

    def set_access_password(self, password: str) -> AppSettings:
        password = password.strip()
        if len(password) < 4:
            raise ValueError("password must be at least 4 characters")
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200000).hex()
        self._settings.access_password_hash = f"{salt}${digest}"
        return self.save()

    def clear_access_password(self) -> AppSettings:
        self._settings.access_password_hash = ""
        return self.save()

    def verify_access_password(self, password: str) -> bool:
        if not self.has_access_password():
            return True
        try:
            salt, expected = self._settings.access_password_hash.split("$", 1)
        except ValueError:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200000).hex()
        return hmac.compare_digest(digest, expected)

    def build_access_token(self) -> str:
        if not self.has_access_password():
            return ""
        return hashlib.sha256(f"tgup:{self._settings.access_password_hash}:authorized".encode("utf-8")).hexdigest()

    def is_access_token_valid(self, token: str | None) -> bool:
        if not self.has_access_password():
            return True
        if not token:
            return False
        return hmac.compare_digest(token, self.build_access_token())

    def add_channel(self, payload: ChannelPayload) -> ChannelConfig:
        channel = ChannelConfig(id=str(uuid4()), **payload.model_dump())
        self._settings.channels.append(channel)
        self.save()
        return channel

    def update_channel(self, channel_id: str, payload: ChannelPayload) -> ChannelConfig | None:
        for index, channel in enumerate(self._settings.channels):
            if channel.id == channel_id:
                updated = channel.model_copy(update=payload.model_dump())
                self._settings.channels[index] = updated
                self.save()
                return updated
        return None

    def delete_channel(self, channel_id: str) -> bool:
        if any(item.channel_id == channel_id for item in self._settings.folders):
            raise ValueError("channel is still used by one or more folders")
        before = len(self._settings.channels)
        self._settings.channels = [item for item in self._settings.channels if item.id != channel_id]
        removed = before != len(self._settings.channels)
        if removed:
            self.save()
        return removed

    def add_folder(self, payload: FolderPayload) -> FolderConfig:
        folder = FolderConfig(id=str(uuid4()), **payload.model_dump())
        self._validate_folder(folder)
        self._settings.folders.append(folder)
        self.save()
        return folder

    def update_folder(self, folder_id: str, payload: FolderPayload) -> FolderConfig | None:
        for index, folder in enumerate(self._settings.folders):
            if folder.id == folder_id:
                updated = folder.model_copy(update=payload.model_dump())
                self._validate_folder(updated)
                self._settings.folders[index] = updated
                self.save()
                return updated
        return None

    def delete_folder(self, folder_id: str) -> bool:
        before = len(self._settings.folders)
        self._settings.folders = [item for item in self._settings.folders if item.id != folder_id]
        removed = before != len(self._settings.folders)
        if removed:
            self.save()
        return removed

    def _validate_folder(self, folder: FolderConfig) -> None:
        if not folder.path.strip():
            raise ValueError("path is required")
        if not any(channel.id == folder.channel_id for channel in self._settings.channels):
            raise ValueError("channel_id is invalid")
        if folder.post_upload_action == "move" and not folder.move_target_path.strip():
            raise ValueError("move_target_path is required when post_upload_action is move")
        if folder.post_upload_action == "move":
            try:
                Path(folder.move_target_path)
            except OSError as exc:
                raise ValueError("move_target_path is invalid") from exc
