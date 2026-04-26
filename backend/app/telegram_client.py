from __future__ import annotations

import asyncio
import hashlib
import json
import math
import logging
import re
import socks
import shutil
import subprocess
from pathlib import Path

from telethon import TelegramClient
from telethon import errors as tg_errors
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
    is_telegram_previewable_video,
)
from .models import LoginStage

logger = logging.getLogger(__name__)


class TelegramSessionManager:
    def __init__(self) -> None:
        self.session_path = str(Path(SESSIONS_DIR / "telethon"))
        self.client: TelegramClient | None = None
        self.phone_number = ""
        self.stage = LoginStage.LOGGED_OUT
        self.last_error = ""
        self._phone_code_hash: str | None = None

    def _build_proxy(self, proxy_settings: dict | None):
        if not proxy_settings or not proxy_settings.get("enabled"):
            return None
        proxy_type = proxy_settings.get("type", "http")
        proxy_kind = socks.HTTP if proxy_type == "http" else socks.SOCKS5
        return (
            proxy_kind,
            proxy_settings.get("host", "").strip(),
            int(proxy_settings.get("port") or 1080),
            True,
            proxy_settings.get("username", "").strip() or None,
            proxy_settings.get("password", "").strip() or None,
        )

    async def init_client(
        self, api_id: int, api_hash: str, proxy_settings: dict | None = None
    ) -> TelegramClient:
        if self.client:
            await self.client.disconnect()
        logger.info(
            "Initializing Telethon client (proxy_enabled=%s, proxy_type=%s, proxy_host=%s, proxy_port=%s)",
            bool(proxy_settings and proxy_settings.get("enabled")),
            (proxy_settings or {}).get("type", ""),
            (proxy_settings or {}).get("host", ""),
            (proxy_settings or {}).get("port", ""),
        )
        self.client = TelegramClient(
            self.session_path, api_id, api_hash, proxy=self._build_proxy(proxy_settings)
        )
        await self.client.connect()
        return self.client

    async def start_login(
        self,
        api_id: int,
        api_hash: str,
        phone_number: str,
        proxy_settings: dict | None = None,
    ) -> LoginStage:
        self.last_error = ""
        try:
            client = await self.init_client(api_id, api_hash, proxy_settings)
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

    async def restore(
        self, api_id: int | None, api_hash: str, proxy_settings: dict | None = None
    ) -> None:
        if not api_id or not api_hash:
            return
        try:
            client = await self.init_client(api_id, api_hash, proxy_settings)
            self.stage = (
                LoginStage.AUTHORIZED
                if await client.is_user_authorized()
                else LoginStage.LOGGED_OUT
            )
            self.last_error = ""
            logger.info("Telethon restore completed (stage=%s)", self.stage.value)
        except Exception as exc:
            self.stage = LoginStage.FAILED
            self.last_error = self._classify_connect_error(exc)
            logger.exception("Telethon restore failed: %s", self.last_error)

    def status(self) -> dict[str, str]:
        return {"stage": self.stage.value, "last_error": self.last_error}

    async def _resolve_target_entity(self, channel_target: str):
        if not self.client:
            raise RuntimeError("telegram client is not initialized")
        normalized = str(channel_target or "").strip()
        if re.fullmatch(r"-100\d+", normalized):
            entity_id, entity_kind = utils.resolve_id(int(normalized))
            if entity_kind is types.PeerChannel:
                return await self.client.get_entity(types.PeerChannel(entity_id))
        return await self.client.get_entity(normalized)

    async def _resolve_target_input_entity(self, channel_target: str):
        if not self.client:
            raise RuntimeError("telegram client is not initialized")
        normalized = str(channel_target or "").strip()
        if re.fullmatch(r"-100\d+", normalized):
            entity_id, entity_kind = utils.resolve_id(int(normalized))
            if entity_kind is types.PeerChannel:
                return await self.client.get_input_entity(types.PeerChannel(entity_id))
        return await self.client.get_input_entity(normalized)

    async def self_check(self) -> dict:
        if not self.client:
            return {
                "ok": False,
                "stage": self.stage.value,
                "last_error": "telegram client is not initialized",
                "authorized": False,
                "session_kind": "unknown",
                "can_call_user_only_methods": False,
            }
        try:
            authorized = await self.client.is_user_authorized()
            if not authorized:
                self.stage = LoginStage.LOGGED_OUT
                return {
                    "ok": False,
                    "stage": self.stage.value,
                    "last_error": self.last_error,
                    "authorized": False,
                    "session_kind": "unknown",
                    "can_call_user_only_methods": False,
                }
            me = await self.client.get_me()
            is_bot = bool(getattr(me, "bot", False))
            self.stage = LoginStage.AUTHORIZED
            self.last_error = ""
            return {
                "ok": True,
                "stage": self.stage.value,
                "last_error": "",
                "authorized": True,
                "session_kind": "bot" if is_bot else "user",
                "can_call_user_only_methods": not is_bot,
                "user_id": getattr(me, "id", None),
                "username": getattr(me, "username", "") or "",
                "first_name": getattr(me, "first_name", "") or "",
                "last_name": getattr(me, "last_name", "") or "",
                "phone": getattr(me, "phone", "") or "",
            }
        except Exception as exc:
            self.stage = LoginStage.FAILED
            self.last_error = self._classify_connect_error(exc)
            logger.exception("Telethon self-check failed: %s", self.last_error)
            return {
                "ok": False,
                "stage": self.stage.value,
                "last_error": self.last_error,
                "authorized": False,
                "session_kind": "unknown",
                "can_call_user_only_methods": False,
            }

    async def setup_bot_for_channel(
        self,
        channel_target: str,
        bot_username: str,
        admin_title: str = "Uploader Bot",
    ) -> dict:
        if not self.client:
            raise RuntimeError("telegram client is not initialized")
        logger.info(
            "Starting bot setup for target=%s bot=%s admin_title=%s",
            channel_target,
            bot_username,
            admin_title,
        )
        target_entity = await self._resolve_target_entity(channel_target)
        bot_entity = await self.client.get_input_entity(bot_username)

        invited = False
        promoted = False

        try:
            if isinstance(target_entity, types.Chat):
                await self.client(
                    functions.messages.AddChatUserRequest(
                        chat_id=target_entity.id,
                        user_id=bot_entity,
                        fwd_limit=0,
                    )
                )
            elif isinstance(target_entity, types.Channel):
                await self.client(
                    functions.channels.InviteToChannelRequest(
                        channel=target_entity,
                        users=[bot_entity],
                    )
                )
            else:
                raise RuntimeError("unsupported target type")
            invited = True
        except tg_errors.UserAlreadyParticipantError:
            invited = False
        except Exception as exc:
            logger.exception(
                "Bot invite failed for target=%s bot=%s", channel_target, bot_username
            )
            raise RuntimeError(self._classify_bot_setup_error(exc, "invite")) from exc

        try:
            if isinstance(target_entity, types.Chat):
                promoted = False
            elif isinstance(target_entity, types.Channel):
                if getattr(target_entity, "megagroup", False):
                    promoted = False
                else:
                    await self.client(
                        functions.channels.EditAdminRequest(
                            channel=target_entity,
                            user_id=bot_entity,
                            admin_rights=self._build_channel_admin_rights(target_entity),
                            rank=(admin_title or "Uploader Bot")[:16],
                        )
                    )
                    promoted = True
        except Exception as exc:
            logger.exception(
                "Bot promote failed for target=%s bot=%s", channel_target, bot_username
            )
            raise RuntimeError(self._classify_bot_setup_error(exc, "promote")) from exc

        target_kind = "chat"
        if isinstance(target_entity, types.Channel):
            target_kind = "supergroup" if getattr(target_entity, "megagroup", False) else "channel"

        result = {
            "ok": True,
            "invited": invited,
            "promoted": promoted,
            "target_kind": target_kind,
            "target_title": getattr(target_entity, "title", "") or "",
            "bot_username": bot_username.lstrip("@"),
        }
        logger.info("Bot setup succeeded: %s", result)
        return result

    async def upload_file(
        self,
        channel_target: str,
        file_paths: list[str],
        caption: str,
        progress_callback,
        force_document: bool = False,
    ):
        if not self.client:
            raise RuntimeError("telegram client is not initialized")
        path_objects = [Path(path) for path in file_paths]
        if len(path_objects) > 1:
            return await self._send_album_with_thumbs(
                channel_target, path_objects, caption, progress_callback
            )
        send_options, cleanup_paths = self._build_send_options(
            path_objects, force_document=force_document
        )
        try:
            return await self.client.send_file(
                entity=await self._resolve_target_input_entity(channel_target),
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
        entity = await self._resolve_target_input_entity(channel_target)
        parsed_caption = await self.client._parse_message_text(caption or "", ())
        cleanup_paths: list[Path] = []
        media: list[types.InputSingleMedia] = []
        try:
            used_callback = (
                None
                if not progress_callback
                else (
                    lambda sent, total: progress_callback(
                        sent_count + 1 if sent == total else sent_count + sent / total,
                        len(paths),
                    )
                )
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
                if isinstance(
                    input_media,
                    (types.InputMediaUploadedPhoto, types.InputMediaPhotoExternal),
                ):
                    response = await self.client(
                        functions.messages.UploadMediaRequest(entity, media=input_media)
                    )
                    input_media = utils.get_input_media(response.photo)
                elif isinstance(input_media, types.InputMediaUploadedDocument):
                    response = await self.client(
                        functions.messages.UploadMediaRequest(entity, media=input_media)
                    )
                    input_media = utils.get_input_media(
                        response.document,
                        supports_streaming=media_options.get(
                            "supports_streaming", False
                        ),
                    )
                message, entities = parsed_caption if sent_count == 0 else ("", None)
                media.append(
                    types.InputSingleMedia(
                        input_media, message=message, entities=entities
                    )
                )

            request = functions.messages.SendMultiMediaRequest(
                entity, multi_media=media
            )
            result = await self.client(request)
            random_ids = [item.random_id for item in media]
            return self.client._get_response_message(random_ids, result, entity)
        finally:
            for cleanup_path in cleanup_paths:
                cleanup_path.unlink(missing_ok=True)

    def _build_send_options(
        self, paths: list[Path], force_document: bool = False
    ) -> tuple[dict, list[Path]]:
        options = {"force_document": force_document}
        cleanup_paths: list[Path] = []
        if not paths:
            return options, cleanup_paths

        if force_document:
            return options, cleanup_paths

        if len(paths) > 1:
            if all(
                classify_file(path) == "video" and is_streamable_video(path)
                for path in paths
            ):
                options["supports_streaming"] = True
            return options, cleanup_paths

        item_options, item_cleanup_paths = self._build_media_options(
            paths[0], force_document=force_document
        )
        options.update(item_options)
        cleanup_paths.extend(item_cleanup_paths)
        return options, cleanup_paths

    def _build_media_options(
        self, path: Path, force_document: bool = False
    ) -> tuple[dict, list[Path]]:
        options: dict = {}
        cleanup_paths: list[Path] = []
        if force_document:
            return options, cleanup_paths
        file_type = classify_file(path)
        mime_type = guess_media_mime_type(path)
        if mime_type:
            options["mime_type"] = mime_type
        if file_type == "video" and is_telegram_previewable_video(path):
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
            f"{path.resolve()}:{path.stat().st_mtime_ns}:{path.stat().st_size}".encode(
                "utf-8"
            )
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
        if (
            result.returncode != 0
            or not output_path.exists()
            or output_path.stat().st_size <= 0
        ):
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
                stream
                for stream in payload.get("streams", [])
                if stream.get("codec_type") == "video"
            ),
            None,
        )
        width = int(video_stream.get("width") or 0) if video_stream else 0
        height = int(video_stream.get("height") or 0) if video_stream else 0
        if duration <= 0 or width <= 0 or height <= 0:
            return None
        return duration, width, height

    def _build_channel_admin_rights(
        self, target_entity: types.Channel
    ) -> types.ChatAdminRights:
        if getattr(target_entity, "megagroup", False):
            return types.ChatAdminRights(
                change_info=False,
                delete_messages=False,
                ban_users=False,
                invite_users=False,
                pin_messages=False,
                manage_call=False,
                other=False,
                manage_topics=False,
            )
        return types.ChatAdminRights(
            change_info=False,
            post_messages=True,
            edit_messages=False,
            delete_messages=False,
            invite_users=False,
            other=False,
        )

    def _classify_bot_setup_error(self, exc: Exception, stage: str) -> str:
        if isinstance(exc, tg_errors.ChatAdminRequiredError):
            return f"permission|current account lacks admin rights to {stage} bot in this target"
        if isinstance(exc, tg_errors.RightForbiddenError):
            return f"permission|current account cannot grant requested admin rights during {stage}"
        if isinstance(exc, tg_errors.FreshChangeAdminsForbiddenError):
            return "permission|account recently received admin rights and cannot change admins yet"
        if isinstance(exc, tg_errors.BotGroupsBlockedError):
            return "permission|bot is configured to reject group/channel invitations"
        if isinstance(exc, tg_errors.UserPrivacyRestrictedError):
            return f"permission|privacy settings prevent completing bot {stage}"
        if isinstance(exc, tg_errors.AdminsTooMuchError):
            return "permission|target already has too many administrators"
        if isinstance(exc, tg_errors.UserChannelsTooMuchError):
            return "limit|bot has joined too many channels or groups"
        if isinstance(exc, tg_errors.UserKickedError):
            return "permission|bot was previously removed or banned from this target"
        if isinstance(exc, tg_errors.UserBotError):
            return "invalid|resolved target user is not a valid bot account"
        return str(exc)

    def _classify_connect_error(self, exc: Exception) -> str:
        if isinstance(exc, ConnectionError):
            return (
                "connect|Telethon 无法连接 Telegram 数据中心，请检查当前网络/代理是否支持 "
                f"MTProto 连接：{exc}"
            )
        if isinstance(exc, TimeoutError):
            return (
                "connect|Telethon 连接 Telegram 超时，请检查当前网络/代理是否可访问 Telegram："
                f"{exc}"
            )
        return str(exc)

    async def shutdown(self) -> None:
        if self.client:
            await self.client.disconnect()
            self.client = None


async def maybe_await(value):
    if asyncio.iscoroutine(value):
        return await value
    return value
