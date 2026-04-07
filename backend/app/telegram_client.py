from __future__ import annotations

import asyncio
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import (
    PasswordHashInvalidError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)

from .config import SESSIONS_DIR
from .file_utils import (
    classify_file,
    guess_media_mime_type,
    is_previewable_audio,
    is_streamable_video,
)
from .models import LoginStage


class TelegramSessionManager:
    def __init__(self) -> None:
        self.session_path = str(Path(SESSIONS_DIR / "telethon"))
        self.client: TelegramClient | None = None
        self.phone_number = ""
        self.stage = LoginStage.LOGGED_OUT
        self.last_error = ""
        self._phone_code_hash: str | None = None

    async def init_client(self, api_id: int, api_hash: str) -> TelegramClient:
        if self.client:
            await self.client.disconnect()
        self.client = TelegramClient(self.session_path, api_id, api_hash)
        await self.client.connect()
        return self.client

    async def start_login(self, api_id: int, api_hash: str, phone_number: str) -> LoginStage:
        self.last_error = ""
        try:
            client = await self.init_client(api_id, api_hash)
            self.phone_number = phone_number
            if await client.is_user_authorized():
                self.stage = LoginStage.AUTHORIZED
                return self.stage
            result = await client.send_code_request(phone_number)
            self._phone_code_hash = result.phone_code_hash
            self.stage = LoginStage.CODE_REQUIRED
        except Exception as exc:
            self.stage = LoginStage.FAILED
            self.last_error = str(exc)
        return self.stage

    async def verify_code(self, code: str) -> LoginStage:
        if not self.client:
            self.stage = LoginStage.FAILED
            self.last_error = "client not initialized"
            return self.stage
        try:
            await self.client.sign_in(
                phone=self.phone_number,
                code=code,
                phone_code_hash=self._phone_code_hash,
            )
            self.stage = LoginStage.AUTHORIZED
        except SessionPasswordNeededError:
            self.stage = LoginStage.PASSWORD_REQUIRED
        except PhoneCodeInvalidError:
            self.stage = LoginStage.FAILED
            self.last_error = "invalid verification code"
        except Exception as exc:
            self.stage = LoginStage.FAILED
            self.last_error = str(exc)
        return self.stage

    async def verify_password(self, password: str) -> LoginStage:
        if not self.client:
            self.stage = LoginStage.FAILED
            self.last_error = "client not initialized"
            return self.stage
        try:
            await self.client.sign_in(password=password)
            self.stage = LoginStage.AUTHORIZED
        except PasswordHashInvalidError:
            self.stage = LoginStage.FAILED
            self.last_error = "invalid password"
        except Exception as exc:
            self.stage = LoginStage.FAILED
            self.last_error = str(exc)
        return self.stage

    async def restore(self, api_id: int | None, api_hash: str) -> None:
        if not api_id or not api_hash:
            return
        client = await self.init_client(api_id, api_hash)
        self.stage = LoginStage.AUTHORIZED if await client.is_user_authorized() else LoginStage.LOGGED_OUT

    def status(self) -> dict[str, str]:
        return {"stage": self.stage.value, "last_error": self.last_error}

    async def upload_file(
        self,
        channel_target: str,
        file_paths: list[str],
        caption: str,
        progress_callback,
    ):
        if not self.client:
            raise RuntimeError("telegram client is not initialized")
        path_objects = [Path(path) for path in file_paths]
        send_options = self._build_send_options(path_objects)
        return await self.client.send_file(
            entity=channel_target,
            file=file_paths if len(file_paths) > 1 else file_paths[0],
            caption=caption,
            progress_callback=progress_callback,
            **send_options,
        )

    def _build_send_options(self, paths: list[Path]) -> dict:
        options = {"force_document": False}
        if not paths:
            return options

        if len(paths) > 1:
            if all(classify_file(path) == "video" and is_streamable_video(path) for path in paths):
                options["supports_streaming"] = True
            return options

        path = paths[0]
        file_type = classify_file(path)
        mime_type = guess_media_mime_type(path)
        if mime_type:
            options["mime_type"] = mime_type
        if file_type == "video" and is_streamable_video(path):
            options["supports_streaming"] = True
        if file_type == "music" and is_previewable_audio(path):
            options["supports_streaming"] = False
        return options

    async def shutdown(self) -> None:
        if self.client:
            await self.client.disconnect()
            self.client = None


async def maybe_await(value):
    if asyncio.iscoroutine(value):
        return await value
    return value
