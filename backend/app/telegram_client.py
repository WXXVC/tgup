from __future__ import annotations

import asyncio
import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import (
    PasswordHashInvalidError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)
from telethon.tl.types import DocumentAttributeVideo
from telethon import functions, types, utils

from .config import PREVIEWS_DIR, SESSIONS_DIR
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
        if len(path_objects) > 1:
            return await self._send_album_with_thumbs(channel_target, path_objects, caption, progress_callback)
        send_options, cleanup_paths = self._build_send_options(path_objects)
        try:
            return await self.client.send_file(
                entity=channel_target,
                file=file_paths if len(file_paths) > 1 else file_paths[0],
                caption=caption,
                progress_callback=progress_callback,
                **send_options,
            )
        finally:
            for cleanup_path in cleanup_paths:
                cleanup_path.unlink(missing_ok=True)

    async def _send_album_with_thumbs(
        self,
        channel_target: str,
        paths: list[Path],
        caption: str,
        progress_callback,
    ):
        if not self.client:
            raise RuntimeError("telegram client is not initialized")
        entity = await self.client.get_input_entity(channel_target)
        parsed_caption = await self.client._parse_message_text(caption or "", ())
        cleanup_paths: list[Path] = []
        media: list[types.InputSingleMedia] = []
        try:
            used_callback = None if not progress_callback else (
                lambda sent, total: progress_callback(sent_count + 1 if sent == total else sent_count + sent / total, len(paths))
            )
            for sent_count, path in enumerate(paths):
                media_options, media_cleanup_paths = self._build_media_options(path)
                cleanup_paths.extend(media_cleanup_paths)
                _, input_media, _ = await self.client._file_to_media(
                    str(path),
                    force_document=False,
                    progress_callback=used_callback,
                    attributes=media_options.get("attributes"),
                    thumb=media_options.get("thumb"),
                    supports_streaming=media_options.get("supports_streaming", False),
                    mime_type=media_options.get("mime_type"),
                    nosound_video=True,
                )
                if isinstance(input_media, (types.InputMediaUploadedPhoto, types.InputMediaPhotoExternal)):
                    response = await self.client(functions.messages.UploadMediaRequest(entity, media=input_media))
                    input_media = utils.get_input_media(response.photo)
                elif isinstance(input_media, types.InputMediaUploadedDocument):
                    response = await self.client(functions.messages.UploadMediaRequest(entity, media=input_media))
                    input_media = utils.get_input_media(
                        response.document,
                        supports_streaming=media_options.get("supports_streaming", False),
                    )
                message, entities = parsed_caption if sent_count == 0 else ("", None)
                media.append(types.InputSingleMedia(input_media, message=message, entities=entities))

            request = functions.messages.SendMultiMediaRequest(entity, multi_media=media)
            result = await self.client(request)
            random_ids = [item.random_id for item in media]
            return self.client._get_response_message(random_ids, result, entity)
        finally:
            for cleanup_path in cleanup_paths:
                cleanup_path.unlink(missing_ok=True)

    def _build_send_options(self, paths: list[Path]) -> tuple[dict, list[Path]]:
        options = {"force_document": False}
        cleanup_paths: list[Path] = []
        if not paths:
            return options, cleanup_paths

        if len(paths) > 1:
            if all(classify_file(path) == "video" and is_streamable_video(path) for path in paths):
                options["supports_streaming"] = True
            return options, cleanup_paths

        item_options, item_cleanup_paths = self._build_media_options(paths[0])
        options.update(item_options)
        cleanup_paths.extend(item_cleanup_paths)
        return options, cleanup_paths

    def _build_media_options(self, path: Path) -> tuple[dict, list[Path]]:
        options: dict = {}
        cleanup_paths: list[Path] = []
        file_type = classify_file(path)
        mime_type = guess_media_mime_type(path)
        if mime_type:
            options["mime_type"] = mime_type
        if file_type == "video" and is_streamable_video(path):
            options["supports_streaming"] = True
            metadata = self._probe_video_metadata(path)
            if metadata:
                duration, width, height = metadata
                options["attributes"] = [
                    DocumentAttributeVideo(
                        duration=max(1, math.ceil(duration)),
                        w=width,
                        h=height,
                        supports_streaming=True,
                    )
                ]
            thumb_path = self._build_video_thumbnail(path)
            if thumb_path:
                options["thumb"] = str(thumb_path)
                cleanup_paths.append(thumb_path)
        if file_type == "music" and is_previewable_audio(path):
            options["supports_streaming"] = False
        return options, cleanup_paths

    def _build_video_thumbnail(self, path: Path) -> Path | None:
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            return None
        metadata = self._probe_video_metadata(path)
        capture_at = 1.0
        if metadata:
            duration, _, _ = metadata
            if duration > 0:
                capture_at = min(max(duration * 0.1, 1.0), max(1.0, duration - 0.5))
        token = hashlib.sha1(
            f"{path.resolve()}:{path.stat().st_mtime_ns}:{path.stat().st_size}".encode("utf-8")
        ).hexdigest()[:16]
        output_path = PREVIEWS_DIR / f"{path.stem}.{token}.jpg"
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{capture_at:.2f}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            "scale=320:-2",
            "-q:v",
            "4",
            str(output_path),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 0:
            output_path.unlink(missing_ok=True)
            return None
        return output_path

    def _probe_video_metadata(self, path: Path) -> tuple[float, int, int] | None:
        if not shutil.which("ffprobe"):
            return None
        command = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height",
            "-of",
            "json",
            str(path),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return None
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return None
        duration = float(payload.get("format", {}).get("duration") or 0)
        video_stream = next(
            (
                stream for stream in payload.get("streams", [])
                if stream.get("codec_type") == "video"
            ),
            None,
        )
        width = int(video_stream.get("width") or 0) if video_stream else 0
        height = int(video_stream.get("height") or 0) if video_stream else 0
        if duration <= 0 or width <= 0 or height <= 0:
            return None
        return duration, width, height

    async def shutdown(self) -> None:
        if self.client:
            await self.client.disconnect()
            self.client = None


async def maybe_await(value):
    if asyncio.iscoroutine(value):
        return await value
    return value
