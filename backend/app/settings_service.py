from __future__ import annotations

import hashlib
import hmac
import secrets
from collections import OrderedDict
from pathlib import Path
from uuid import uuid4

from .models import (
    AccessPasswordRequest,
    AppSettings,
    BotApiAccount,
    BotApiAccountPayload,
    BotDispatchMode,
    BotDispatchSettingsPayload,
    ChannelConfig,
    ChannelPayload,
    FolderConfig,
    FolderPayload,
    LoginStartRequest,
    ProxySettings,
    ProxySettingsPayload,
    UploadEngine,
    UploadEnginePayload,
)
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

    def update_api(
        self,
        api_id: int,
        api_hash: str,
        phone_number: str,
    ) -> AppSettings:
        self._settings.api.api_id = api_id
        self._settings.api.api_hash = api_hash
        self._settings.api.phone_number = phone_number
        return self.save()

    def update_proxy(self, payload: ProxySettingsPayload) -> AppSettings:
        proxy = ProxySettings(**self._normalize_proxy_payload(payload))
        self._validate_proxy(proxy)
        self._settings.proxy = proxy
        return self.save()

    def update_upload_engine(self, payload: UploadEnginePayload) -> AppSettings:
        # 兼容旧接口：当前版本固定为自动混合上传模式，不再实际切换引擎。
        return self.save()

    def resolve_api_payload(
        self, payload: LoginStartRequest
    ) -> tuple[int, str, str]:
        api_id = (
            payload.api_id if payload.api_id is not None else self._settings.api.api_id
        )
        api_hash = payload.api_hash.strip() or self._settings.api.api_hash
        phone_number = payload.phone_number.strip() or self._settings.api.phone_number
        if api_id is None or not api_hash or not phone_number:
            raise ValueError("api credentials are incomplete")
        return api_id, api_hash, phone_number

    def has_access_password(self) -> bool:
        return bool(self._settings.access_password_hash)

    def public_settings(self) -> dict:
        payload = self._settings.model_dump(mode="json")
        payload.pop("access_password_hash", None)
        payload["api"] = {
            "api_id": self._mask_api_id(self._settings.api.api_id),
            "api_hash": self._mask_secret(self._settings.api.api_hash),
            "phone_number": self._mask_phone(self._settings.api.phone_number),
            "api_id_saved": self._settings.api.api_id is not None,
            "api_hash_saved": bool(self._settings.api.api_hash),
            "phone_number_saved": bool(self._settings.api.phone_number),
        }
        payload["proxy"] = {
            "enabled": self._settings.proxy.enabled,
            "type": self._settings.proxy.type.value,
            "host": self._settings.proxy.host,
            "port": self._settings.proxy.port,
            "username": self._settings.proxy.username,
            "password": self._mask_secret(self._settings.proxy.password),
            "password_saved": bool(self._settings.proxy.password),
        }
        payload["upload_engine"] = "hybrid"
        payload["bot_dispatch_mode"] = self._settings.bot_dispatch_mode.value
        payload["default_bot_api_account_id"] = self._settings.default_bot_api_account_id
        payload["smart_queue_scheduling_enabled"] = (
            self._settings.smart_queue_scheduling_enabled
        )
        payload["bot_api_accounts"] = [
            {
                "id": account.id,
                "name": account.name,
                "server_url": account.server_url,
                "bot_token": self._mask_secret(account.bot_token),
                "bot_token_saved": bool(account.bot_token),
                "enabled": account.enabled,
                "send_rate_limit_per_minute": account.send_rate_limit_per_minute,
                "send_rate_limit_per_channel_per_minute": account.send_rate_limit_per_channel_per_minute,
                "send_jitter_min_ms": account.send_jitter_min_ms,
                "send_jitter_max_ms": account.send_jitter_max_ms,
                "auto_slowdown_enabled": account.auto_slowdown_enabled,
                "auto_slowdown_factor_percent": account.auto_slowdown_factor_percent,
                "auto_slowdown_duration_seconds": account.auto_slowdown_duration_seconds,
            }
            for account in self._settings.bot_api_accounts
        ]
        payload["engine_limits"] = self.engine_limits()
        payload["folder_engine_warnings"] = self.folder_engine_warnings()
        payload["access_password_enabled"] = self.has_access_password()
        return payload

    def public_settings_summary(self) -> dict:
        return {
            "folders": [
                {
                    "id": folder.id,
                    "name": folder.name,
                    "path": folder.path,
                    "enabled": folder.enabled,
                }
                for folder in self._settings.folders
            ],
            "access_password_enabled": self.has_access_password(),
        }

    def engine_limits(self) -> dict:
        return {
            "engine": "hybrid",
            "max_upload_size_mb": 4096,
            "default_upload_size_mb": 2048,
            "default_segment_target_mb": 1900,
            "description": "????????50 MB ?????? Bot Token??????? Bot??????? Telethon??? 50 MB ????????????? Telethon?",
        }

    def folder_engine_warnings(self) -> dict:
        max_upload_size_mb = self.engine_limits()["max_upload_size_mb"]
        items = []
        for folder in self._settings.folders:
            if folder.upload_size_limit_mb > max_upload_size_mb:
                items.append(
                    {
                        "folder_id": folder.id,
                        "folder_name": folder.name,
                        "upload_size_limit_mb": folder.upload_size_limit_mb,
                        "max_upload_size_mb": max_upload_size_mb,
                        "message": f"目录上传上限 {folder.upload_size_limit_mb} MB 超过当前引擎限制 {max_upload_size_mb} MB",
                    }
                )
        return {
            "count": len(items),
            "items": items,
        }

    def _mask_api_id(self, value: int | None) -> str:
        if value is None:
            return ""
        text = str(value)
        if len(text) <= 2:
            return "*" * len(text)
        return f"{text[:1]}{'*' * max(1, len(text) - 2)}{text[-1:]}"

    def _mask_secret(self, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        if len(value) <= 6:
            return "*" * len(value)
        return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"

    def _mask_phone(self, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        if len(value) <= 4:
            return "*" * len(value)
        return f"{value[:3]}{'*' * max(1, len(value) - 5)}{value[-2:]}"

    def _normalize_proxy_payload(self, payload: ProxySettingsPayload) -> dict:
        data = payload.model_dump()
        data["host"] = data["host"].strip()
        data["username"] = data["username"].strip()
        data["password"] = data["password"].strip() or self._settings.proxy.password
        return data

    def _validate_proxy(self, proxy: ProxySettings) -> None:
        if not proxy.enabled:
            return
        if not proxy.host.strip():
            raise ValueError("proxy host is required when proxy is enabled")

    def _validate_engine_requirements(self) -> None:
        if self._settings.bot_dispatch_mode == BotDispatchMode.CHANNEL_BOUND:
            invalid_channels = [
                channel.name
                for channel in self._settings.channels
                if channel.enabled and not self.get_bot_api_account(channel.bot_api_account_id, enabled_only=True)
            ]
            if invalid_channels:
                raise ValueError(
                    f"enabled channels missing valid bot binding: {', '.join(invalid_channels)}"
                )

    def resolved_proxy_settings(self) -> ProxySettings:
        return self._settings.proxy.model_copy()

    def resolved_bot_api_accounts(self) -> list[BotApiAccount]:
        return self.list_bot_api_accounts()

    def set_access_password(self, password: str) -> AppSettings:
        password = password.strip()
        if len(password) < 4:
            raise ValueError("password must be at least 4 characters")
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), 200000
        ).hex()
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
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), 200000
        ).hex()
        return hmac.compare_digest(digest, expected)

    def build_access_token(self) -> str:
        if not self.has_access_password():
            return ""
        return hashlib.sha256(
            f"tgup:{self._settings.access_password_hash}:authorized".encode("utf-8")
        ).hexdigest()

    def is_access_token_valid(self, token: str | None) -> bool:
        if not self.has_access_password():
            return True
        if not token:
            return False
        return hmac.compare_digest(token, self.build_access_token())

    def list_bot_api_accounts(self) -> list[BotApiAccount]:
        return [item.model_copy() for item in self._settings.bot_api_accounts]

    def get_bot_api_account(
        self, account_id: str, *, enabled_only: bool = False
    ) -> BotApiAccount | None:
        if not account_id:
            return None
        for account in self._settings.bot_api_accounts:
            if account.id != account_id:
                continue
            if enabled_only and not account.enabled:
                return None
            return account.model_copy()
        return None

    def default_bot_api_account(self) -> BotApiAccount | None:
        account = self.get_bot_api_account(
            self._settings.default_bot_api_account_id, enabled_only=True
        )
        if account:
            return account
        for item in self._settings.bot_api_accounts:
            if item.enabled:
                return item.model_copy()
        return None

    def resolve_bot_api_account_for_channel(
        self, channel_id: str, preferred_account_id: str = ""
    ) -> BotApiAccount | None:
        if preferred_account_id:
            return self.get_bot_api_account(preferred_account_id, enabled_only=True)
        channel = next(
            (item for item in self._settings.channels if item.id == channel_id),
            None,
        )
        if channel and channel.bot_api_account_id:
            account = self.get_bot_api_account(
                channel.bot_api_account_id, enabled_only=True
            )
            if account:
                return account
        return self.default_bot_api_account()

    def add_bot_api_account(self, payload: BotApiAccountPayload) -> BotApiAccount:
        account = BotApiAccount(
            id=str(uuid4()), **self._normalize_bot_api_account_payload(payload)
        )
        self._validate_bot_api_account(account)
        self._settings.bot_api_accounts.append(account)
        if not self._settings.default_bot_api_account_id:
            self._settings.default_bot_api_account_id = account.id
        self._validate_engine_requirements()
        self.save()
        return account

    def update_bot_api_account(
        self, account_id: str, payload: BotApiAccountPayload
    ) -> BotApiAccount | None:
        for index, account in enumerate(self._settings.bot_api_accounts):
            if account.id != account_id:
                continue
            updated = account.model_copy(
                update=self._normalize_bot_api_account_payload(payload, existing=account)
            )
            self._validate_bot_api_account(updated, current_id=account_id)
            self._settings.bot_api_accounts[index] = updated
            self._validate_engine_requirements()
            self.save()
            return updated
        return None

    def delete_bot_api_account(self, account_id: str) -> bool:
        if any(item.bot_api_account_id == account_id for item in self._settings.channels):
            raise ValueError("bot api account is still used by one or more channels")
        if self._settings.default_bot_api_account_id == account_id:
            remaining_enabled = [
                item.id
                for item in self._settings.bot_api_accounts
                if item.id != account_id and item.enabled
            ]
            self._settings.default_bot_api_account_id = remaining_enabled[0] if remaining_enabled else ""
        before = len(self._settings.bot_api_accounts)
        self._settings.bot_api_accounts = [
            item for item in self._settings.bot_api_accounts if item.id != account_id
        ]
        removed = before != len(self._settings.bot_api_accounts)
        if removed:
            self._validate_engine_requirements()
            self.save()
        return removed

    def update_bot_dispatch_settings(
        self, payload: BotDispatchSettingsPayload
    ) -> AppSettings:
        if (
            payload.default_bot_api_account_id
            and not self.get_bot_api_account(payload.default_bot_api_account_id)
        ):
            raise ValueError("default_bot_api_account_id is invalid")
        self._settings.bot_dispatch_mode = payload.mode
        self._settings.default_bot_api_account_id = payload.default_bot_api_account_id
        self._settings.smart_queue_scheduling_enabled = (
            payload.smart_queue_scheduling_enabled
        )
        self._validate_engine_requirements()
        return self.save()

    def add_channel(self, payload: ChannelPayload) -> ChannelConfig:
        channel = ChannelConfig(id=str(uuid4()), **payload.model_dump())
        self._validate_channel(channel)
        self._settings.channels.append(channel)
        self.save()
        return channel

    def update_channel(
        self, channel_id: str, payload: ChannelPayload
    ) -> ChannelConfig | None:
        for index, channel in enumerate(self._settings.channels):
            if channel.id == channel_id:
                updated = channel.model_copy(update=payload.model_dump())
                self._validate_channel(updated)
                self._settings.channels[index] = updated
                self._validate_engine_requirements()
                self.save()
                return updated
        return None

    def delete_channel(self, channel_id: str) -> bool:
        if any(item.channel_id == channel_id for item in self._settings.folders):
            raise ValueError("channel is still used by one or more folders")
        before = len(self._settings.channels)
        self._settings.channels = [
            item for item in self._settings.channels if item.id != channel_id
        ]
        removed = before != len(self._settings.channels)
        if removed:
            self.save()
        return removed

    def add_folder(self, payload: FolderPayload) -> FolderConfig:
        folder = FolderConfig(
            id=str(uuid4()), **self._normalize_folder_payload(payload)
        )
        self._validate_folder(folder)
        self._settings.folders.append(folder)
        self.save()
        return folder

    def update_folder(
        self, folder_id: str, payload: FolderPayload
    ) -> FolderConfig | None:
        for index, folder in enumerate(self._settings.folders):
            if folder.id == folder_id:
                updated = folder.model_copy(
                    update=self._normalize_folder_payload(payload)
                )
                self._validate_folder(updated)
                self._settings.folders[index] = updated
                self.save()
                return updated
        return None

    def normalize_folder_limits_for_current_engine(self) -> dict:
        max_upload_size_mb = self.engine_limits()["max_upload_size_mb"]
        updated_items = []
        changed = 0
        for index, folder in enumerate(self._settings.folders):
            next_upload_limit = min(folder.upload_size_limit_mb, max_upload_size_mb)
            next_segment_target = min(
                folder.segment_target_size_mb, max(100, next_upload_limit - 1)
            )
            if (
                next_upload_limit == folder.upload_size_limit_mb
                and next_segment_target == folder.segment_target_size_mb
            ):
                continue
            updated = folder.model_copy(
                update={
                    "upload_size_limit_mb": next_upload_limit,
                    "segment_target_size_mb": next_segment_target,
                }
            )
            self._settings.folders[index] = updated
            updated_items.append(
                {
                    "folder_id": updated.id,
                    "folder_name": updated.name,
                    "upload_size_limit_mb": updated.upload_size_limit_mb,
                    "segment_target_size_mb": updated.segment_target_size_mb,
                }
            )
            changed += 1
        if changed:
            self.save()
        return {
            "changed": changed,
            "items": updated_items,
        }

    def delete_folder(self, folder_id: str) -> bool:
        before = len(self._settings.folders)
        self._settings.folders = [
            item for item in self._settings.folders if item.id != folder_id
        ]
        removed = before != len(self._settings.folders)
        if removed:
            self.save()
        return removed

    def _normalize_bot_api_account_payload(
        self, payload: BotApiAccountPayload, existing: BotApiAccount | None = None
    ) -> dict:
        data = payload.model_dump()
        data["name"] = data["name"].strip()
        server_url = data["server_url"].strip().rstrip("/")
        if not server_url and existing:
            server_url = existing.server_url
        data["server_url"] = server_url
        token = data["bot_token"].strip()
        if not token and existing:
            token = existing.bot_token
        data["bot_token"] = token
        return data

    def _validate_bot_api_account(
        self, account: BotApiAccount, current_id: str | None = None
    ) -> None:
        if not account.name.strip():
            raise ValueError("bot api account name is required")
        if not account.bot_token.strip():
            raise ValueError("bot token is required")
        if account.send_jitter_max_ms < account.send_jitter_min_ms:
            raise ValueError("send_jitter_max_ms must be greater than or equal to send_jitter_min_ms")
        duplicate = next(
            (
                item
                for item in self._settings.bot_api_accounts
                if item.id != current_id
                and item.name.strip().casefold() == account.name.strip().casefold()
            ),
            None,
        )
        if duplicate:
            raise ValueError("bot api account name must be unique")

    def _validate_channel(self, channel: ChannelConfig) -> None:
        if not channel.name.strip():
            raise ValueError("channel name is required")
        if not channel.target.strip():
            raise ValueError("channel target is required")
        if (
            self._settings.bot_dispatch_mode == BotDispatchMode.CHANNEL_BOUND
            and channel.enabled
            and not channel.bot_api_account_id
        ):
            raise ValueError("bot_api_account_id is required for enabled channels in channel_bound mode")
        if not channel.bot_api_account_id:
            return
        if not self.get_bot_api_account(channel.bot_api_account_id):
            raise ValueError("bot_api_account_id is invalid")

    def _validate_folder(self, folder: FolderConfig) -> None:
        max_upload_size_mb = self.engine_limits()["max_upload_size_mb"]
        if not folder.path.strip():
            raise ValueError("path is required")
        if not any(
            channel.id == folder.channel_id for channel in self._settings.channels
        ):
            raise ValueError("channel_id is invalid")
        if folder.post_upload_action == "move" and not folder.move_target_path.strip():
            raise ValueError(
                "move_target_path is required when post_upload_action is move"
            )
        if folder.post_upload_action == "move":
            try:
                Path(folder.move_target_path)
            except OSError as exc:
                raise ValueError("move_target_path is invalid") from exc
        if folder.segment_target_size_mb >= folder.upload_size_limit_mb:
            raise ValueError(
                "segment_target_size_mb must be smaller than upload_size_limit_mb"
            )
        if folder.upload_size_limit_mb > max_upload_size_mb:
            raise ValueError(
                f"upload_size_limit_mb exceeds current engine limit ({max_upload_size_mb} MB)"
            )
        root = Path(folder.path)
        invalid_subdirs = []
        normalized_subdirs = []
        for item in folder.excluded_subdirs:
            candidate = item.strip().replace("\\", "/").strip("/")
            if not candidate:
                continue
            try:
                (root / candidate).resolve().relative_to(root.resolve())
            except ValueError:
                invalid_subdirs.append(item)
                continue
            normalized_subdirs.append(candidate)
        if invalid_subdirs:
            raise ValueError("excluded_subdirs contains invalid paths")
        folder.excluded_subdirs = list(OrderedDict.fromkeys(normalized_subdirs))

    def _normalize_folder_payload(self, payload: FolderPayload) -> dict:
        data = payload.model_dump()
        data["excluded_subdirs"] = [
            item.strip().replace("\\", "/").strip("/")
            for item in payload.excluded_subdirs
            if item and item.strip().replace("\\", "/").strip("/")
        ]
        return data
