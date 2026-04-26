from __future__ import annotations

import json
import mimetypes
import asyncio
import time
import random
from collections import deque
import math
from pathlib import Path
from urllib.parse import quote

import httpx

from .file_utils import classify_file, is_telegram_previewable_video

OFFICIAL_BOT_API_URL = "https://api.telegram.org"


class BotApiUploadError(RuntimeError):
    pass


class LocalBotApiClient:
    def __init__(self) -> None:
        self.server_url = ""
        self.bot_token = ""
        self.proxy_url: str | None = None
        self.last_error = ""
        self.send_rate_limit_per_minute = 20
        self.send_rate_limit_per_channel_per_minute = 10
        self.send_jitter_min_ms = 300
        self.send_jitter_max_ms = 1200
        self.auto_slowdown_enabled = True
        self.auto_slowdown_factor_percent = 50
        self.auto_slowdown_duration_seconds = 600
        self._recent_send_timestamps: deque[float] = deque()
        self._per_channel_recent_send_timestamps: dict[str, deque[float]] = {}
        self._rate_limit_lock = asyncio.Lock()
        self._last_wait_reason = ""
        self._slowdown_until_monotonic = 0.0
        self._slowdown_reason = ""

    def configure(
        self,
        server_url: str,
        bot_token: str,
        send_rate_limit_per_minute: int = 20,
        send_rate_limit_per_channel_per_minute: int = 10,
        send_jitter_min_ms: int = 300,
        send_jitter_max_ms: int = 1200,
        auto_slowdown_enabled: bool = True,
        auto_slowdown_factor_percent: int = 50,
        auto_slowdown_duration_seconds: int = 600,
        proxy_settings: dict | None = None,
    ) -> None:
        self.server_url = (server_url or OFFICIAL_BOT_API_URL).strip().rstrip("/")
        self.bot_token = bot_token.strip()
        self.send_rate_limit_per_minute = max(1, int(send_rate_limit_per_minute or 20))
        self.send_rate_limit_per_channel_per_minute = max(
            1, int(send_rate_limit_per_channel_per_minute or 10)
        )
        self.send_jitter_min_ms = max(0, int(send_jitter_min_ms or 0))
        self.send_jitter_max_ms = max(self.send_jitter_min_ms, int(send_jitter_max_ms or 0))
        self.auto_slowdown_enabled = bool(auto_slowdown_enabled)
        self.auto_slowdown_factor_percent = max(
            10, min(100, int(auto_slowdown_factor_percent or 50))
        )
        self.auto_slowdown_duration_seconds = max(
            30, int(auto_slowdown_duration_seconds or 600)
        )
        self.proxy_url = self._build_proxy_url(proxy_settings)

    def status(self) -> dict[str, str]:
        ready = bool(self.bot_token)
        recent_count = self.recent_send_count()
        wait_seconds = self.preview_wait_seconds()
        return {
            "stage": "authorized" if ready else "logged_out",
            "last_error": self.last_error,
            "wait_seconds": str(max(0, math.ceil(wait_seconds))),
            "send_rate_limit_per_minute": str(self.send_rate_limit_per_minute),
            "send_rate_limit_per_channel_per_minute": str(
                self.send_rate_limit_per_channel_per_minute
            ),
            "recent_send_count": str(recent_count),
            "remaining_quota": str(
                max(0, self._effective_global_limit() - recent_count)
            ),
            "last_wait_reason": self._last_wait_reason,
            "effective_send_rate_limit_per_minute": str(self._effective_global_limit()),
            "effective_send_rate_limit_per_channel_per_minute": str(
                self._effective_channel_limit()
            ),
            "slowdown_active": "true" if self.slowdown_wait_seconds() > 0 else "false",
            "slowdown_wait_seconds": str(max(0, math.ceil(self.slowdown_wait_seconds()))),
            "slowdown_reason": self._slowdown_reason,
        }

    async def test_connection(self) -> dict:
        if not self.bot_token:
            raise BotApiUploadError("bot token is not configured")
        async with httpx.AsyncClient(timeout=20, proxy=self.proxy_url) as client:
            response = await client.get(self._method_url("getMe"))
        payload = self._parse_response(response)
        result = payload.get("result") or {}
        return {
            "ok": True,
            "username": result.get("username", ""),
            "first_name": result.get("first_name", ""),
            "id": result.get("id"),
        }

    async def upload_file(
        self,
        channel_target: str,
        file_paths: list[str],
        caption: str,
        progress_callback,
        force_document: bool = False,
    ):
        if not self.bot_token:
            raise BotApiUploadError("bot token is not configured")
        await self._acquire_send_slot(channel_target)
        await self._apply_send_jitter()
        if len(file_paths) > 1:
            return await self._send_media_group(channel_target, file_paths, caption)
        return await self._send_single(
            channel_target, file_paths[0], caption, progress_callback, force_document
        )

    async def _send_single(
        self,
        channel_target: str,
        file_path: str,
        caption: str,
        progress_callback,
        force_document: bool = False,
    ):
        path = Path(file_path)
        method, field_name = self._resolve_single_method(path, force_document)
        data = {"chat_id": channel_target}
        if caption:
            data["caption"] = caption
        if method == "sendVideo":
            data["supports_streaming"] = "true"
        total = max(1, path.stat().st_size)
        last_reported = 0

        async def file_stream():
            nonlocal last_reported
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    last_reported += len(chunk)
                    if progress_callback:
                        progress_callback(last_reported, total)
                    yield chunk

        request_path = self._method_url(method)
        async with httpx.AsyncClient(timeout=None, proxy=self.proxy_url) as client:
            boundary = "tgup-botapi-upload"
            body = self._build_multipart_body(
                boundary, data, field_name, path.name, file_stream
            )
            response = await client.post(
                request_path,
                content=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
        payload = self._parse_response(response)
        if progress_callback:
            progress_callback(total, total)
        return payload.get("result")

    async def _send_media_group(
        self, channel_target: str, file_paths: list[str], caption: str
    ):
        media = []
        files = {}
        open_handles = []
        try:
            for index, file_path in enumerate(file_paths):
                path = Path(file_path)
                attach_name = f"file{index}"
                media_type = "photo" if classify_file(path) == "image" else "video"
                item = {"type": media_type, "media": f"attach://{attach_name}"}
                if index == 0 and caption:
                    item["caption"] = caption
                media.append(item)
                handle = path.open("rb")
                open_handles.append(handle)
                files[attach_name] = (
                    path.name,
                    handle,
                    mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                )
            async with httpx.AsyncClient(timeout=None, proxy=self.proxy_url) as client:
                response = await client.post(
                    self._method_url("sendMediaGroup"),
                    data={
                        "chat_id": channel_target,
                        "media": json.dumps(media, ensure_ascii=True),
                    },
                    files=files,
                )
            payload = self._parse_response(response)
            return payload.get("result")
        finally:
            for handle in open_handles:
                handle.close()

    def _resolve_single_method(
        self, path: Path, force_document: bool = False
    ) -> tuple[str, str]:
        if force_document:
            return "sendDocument", "document"
        file_type = classify_file(path)
        if file_type == "image":
            return "sendPhoto", "photo"
        if file_type == "video" and is_telegram_previewable_video(path):
            return "sendVideo", "video"
        if file_type == "video":
            return "sendDocument", "document"
        if file_type == "music":
            return "sendAudio", "audio"
        return "sendDocument", "document"

    def _method_url(self, method: str) -> str:
        return f"{self.server_url}/bot{quote(self.bot_token, safe='')}/{method}"

    def _build_proxy_url(self, proxy_settings: dict | None) -> str | None:
        if not proxy_settings or not proxy_settings.get("enabled"):
            return None
        host = str(proxy_settings.get("host") or "").strip()
        if not host:
            return None
        scheme = "http" if proxy_settings.get("type") == "http" else "socks5"
        username = str(proxy_settings.get("username") or "").strip()
        password = str(proxy_settings.get("password") or "").strip()
        auth = ""
        if username:
            auth = quote(username, safe="")
            if password:
                auth = f"{auth}:{quote(password, safe='')}"
            auth = f"{auth}@"
        port = int(proxy_settings.get("port") or 1080)
        return f"{scheme}://{auth}{host}:{port}"

    def _parse_response(self, response: httpx.Response) -> dict:
        try:
            payload = response.json()
        except ValueError as exc:
            raise BotApiUploadError(
                f"official bot api returned invalid response: {response.text}"
            ) from exc
        if response.status_code >= 400 or not payload.get("ok"):
            message = self._classify_error(
                response.status_code,
                payload.get("description") or response.text or "bot api upload failed",
            )
            if (
                message.startswith("rate_limit|")
                and self.auto_slowdown_enabled
            ):
                self._activate_auto_slowdown("telegram_429")
            self.last_error = message
            raise BotApiUploadError(message)
        self.last_error = ""
        return payload

    def _classify_error(self, status_code: int, message: str) -> str:
        lowered = message.lower()
        if "file is too big" in lowered or "request entity too large" in lowered:
            return "size_limit|file too big for current Bot API engine limit"
        if "chat not found" in lowered:
            return "not_found|chat not found or bot cannot access this target"
        if "forbidden" in lowered or "not enough rights" in lowered:
            return "permission|bot is forbidden to send to this chat or lacks required rights"
        if "failed to get http url content" in lowered:
            return "network|official bot api failed to fetch remote content"
        if "too many requests" in lowered or status_code == 429:
            return "rate_limit|bot api rate limited the request, retry later"
        if "unauthorized" in lowered or status_code == 401:
            return "auth|bot token is invalid or expired"
        if status_code >= 500:
            return "server_error|official bot api internal error"
        return message

    async def shutdown(self) -> None:
        return None

    def preview_wait_seconds(self) -> float:
        window_seconds = 60.0
        now = time.monotonic()
        self._trim_send_timestamps(now, window_seconds)
        if len(self._recent_send_timestamps) < self.send_rate_limit_per_minute:
            return 0.0
        oldest = self._recent_send_timestamps[0]
        return max(0.0, (oldest + window_seconds) - now)

    def recent_send_count(self) -> int:
        window_seconds = 60.0
        now = time.monotonic()
        self._trim_send_timestamps(now, window_seconds)
        return len(self._recent_send_timestamps)

    def last_wait_reason(self) -> str:
        return self._last_wait_reason

    def slowdown_wait_seconds(self) -> float:
        return max(0.0, self._slowdown_until_monotonic - time.monotonic())

    async def _acquire_send_slot(self, channel_target: str) -> None:
        async with self._rate_limit_lock:
            window_seconds = 60.0
            now = time.monotonic()
            self._trim_send_timestamps(now, window_seconds)
            channel_key = self._channel_key(channel_target)
            self._trim_channel_send_timestamps(channel_key, now, window_seconds)
            wait_for, wait_reason = self._compute_wait_state(
                channel_key, now, window_seconds
            )
            slowdown_wait = self.slowdown_wait_seconds()
            if slowdown_wait > wait_for:
                wait_for = slowdown_wait
                wait_reason = "auto_slowdown"
            if wait_for > 0:
                self._last_wait_reason = wait_reason
                await asyncio.sleep(wait_for)
                now = time.monotonic()
                self._trim_send_timestamps(now, window_seconds)
                self._trim_channel_send_timestamps(channel_key, now, window_seconds)
            else:
                self._last_wait_reason = ""
            current = time.monotonic()
            self._recent_send_timestamps.append(current)
            self._per_channel_recent_send_timestamps.setdefault(channel_key, deque()).append(
                current
            )

    def _trim_send_timestamps(self, now: float, window_seconds: float) -> None:
        while self._recent_send_timestamps and (
            now - self._recent_send_timestamps[0]
        ) >= window_seconds:
            self._recent_send_timestamps.popleft()

    def _trim_channel_send_timestamps(
        self, channel_key: str, now: float, window_seconds: float
    ) -> None:
        queue = self._per_channel_recent_send_timestamps.get(channel_key)
        if not queue:
            return
        while queue and (now - queue[0]) >= window_seconds:
            queue.popleft()
        if not queue:
            self._per_channel_recent_send_timestamps.pop(channel_key, None)

    def _compute_wait_state(
        self, channel_key: str, now: float, window_seconds: float
    ) -> tuple[float, str]:
        global_wait = 0.0
        effective_global_limit = self._effective_global_limit()
        if len(self._recent_send_timestamps) >= effective_global_limit:
            global_wait = max(
                0.0, (self._recent_send_timestamps[0] + window_seconds) - now
            )
        channel_queue = self._per_channel_recent_send_timestamps.get(channel_key) or deque()
        channel_wait = 0.0
        effective_channel_limit = self._effective_channel_limit()
        if len(channel_queue) >= effective_channel_limit:
            channel_wait = max(0.0, (channel_queue[0] + window_seconds) - now)
        if global_wait <= 0 and channel_wait <= 0:
            return 0.0, ""
        if channel_wait > global_wait:
            return channel_wait, "channel"
        if global_wait > channel_wait:
            return global_wait, "global"
        return global_wait, "global+channel"

    def _channel_key(self, channel_target: str) -> str:
        return str(channel_target or "").strip()

    async def _apply_send_jitter(self) -> None:
        if self.send_jitter_max_ms <= 0:
            return
        lower = min(self.send_jitter_min_ms, self.send_jitter_max_ms)
        upper = max(self.send_jitter_min_ms, self.send_jitter_max_ms)
        wait_ms = lower if lower == upper else random.randint(lower, upper)
        if wait_ms > 0:
            await asyncio.sleep(wait_ms / 1000.0)

    def _activate_auto_slowdown(self, reason: str) -> None:
        self._slowdown_until_monotonic = max(
            self._slowdown_until_monotonic,
            time.monotonic() + self.auto_slowdown_duration_seconds,
        )
        self._slowdown_reason = reason

    def _effective_global_limit(self) -> int:
        if self.slowdown_wait_seconds() <= 0 or not self.auto_slowdown_enabled:
            return self.send_rate_limit_per_minute
        return max(
            1,
            math.floor(
                self.send_rate_limit_per_minute
                * (self.auto_slowdown_factor_percent / 100.0)
            ),
        )

    def _effective_channel_limit(self) -> int:
        if self.slowdown_wait_seconds() <= 0 or not self.auto_slowdown_enabled:
            return self.send_rate_limit_per_channel_per_minute
        return max(
            1,
            math.floor(
                self.send_rate_limit_per_channel_per_minute
                * (self.auto_slowdown_factor_percent / 100.0)
            ),
        )

    def _build_multipart_body(
        self,
        boundary: str,
        data: dict[str, str],
        field_name: str,
        filename: str,
        stream_factory,
    ):
        async def generator():
            for key, value in data.items():
                yield f"--{boundary}\r\n".encode("utf-8")
                yield f'Content-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode(
                    "utf-8"
                )
            yield f"--{boundary}\r\n".encode("utf-8")
            mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            yield (
                f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
                f"Content-Type: {mime_type}\r\n\r\n"
            ).encode("utf-8")
            async for chunk in stream_factory():
                yield chunk
            yield b"\r\n"
            yield f"--{boundary}--\r\n".encode("utf-8")

        return generator()
