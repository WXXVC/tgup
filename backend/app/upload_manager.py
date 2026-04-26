from __future__ import annotations

import asyncio
import difflib
import re
import shutil
import time
import logging
from pathlib import Path
from uuid import uuid4

from telethon import errors as tg_errors
from telethon.tl.types import Message

from .bot_api_client import LocalBotApiClient
from .bot_api_pool import BotApiClientPool
from .file_utils import (
    ALBUM_MAX_FILE_SIZE,
    classify_file,
    file_is_locked,
    is_album_eligible,
    is_telegram_previewable_video,
)
from .models import (
    FolderConfig,
    UploadBatchItem,
    UploadStatus,
    UploadTask,
)
from .scanner import FolderScanner
from .settings_service import SettingsService
from .telegram_client import TelegramSessionManager
from .upload_engine import UploadEngineClient
from .upload_repo import UploadRepository
from .video_splitter import VideoSplitter

logger = logging.getLogger(__name__)


class UploadManager:
    SIMILARITY_RECHECK_SECONDS = 5
    TELEGRAM_MEDIA_GROUP_LIMIT = 10
    BOT_API_FILE_LIMIT_BYTES = 50 * 1024 * 1024
    SCAN_SUBDIR_BATCH_SIZE = 12
    SIMILARITY_NOISE_PATTERNS = (
        r"\b(?:\d{3,4}p|4k|8k|x26[45]|h\.?26[45]|hevc|avc|hdr|sdr|uhd|web[-_. ]?dl|blu[-_. ]?ray|bdrip|webrip|dvdrip)\b",
        r"\b(?:aac|flac|mp3|ddp\d?(?:\.\d)?|dts|truehd|atmos)\b",
        r"\b(?:sample|trailer|preview|cover|thumb|poster)\b",
    )

    def __init__(
        self,
        settings_service: SettingsService,
        upload_repo: UploadRepository,
        scanner: FolderScanner,
        telegram: TelegramSessionManager,
        bot_api_pool: BotApiClientPool,
    ) -> None:
        self.settings_service = settings_service
        self.upload_repo = upload_repo
        self.scanner = scanner
        self.telegram = telegram
        self.bot_api_pool = bot_api_pool
        self.video_splitter = VideoSplitter()
        self.queue: asyncio.Queue[UploadTask] = asyncio.Queue()
        self.worker_task: asyncio.Task | None = None
        self.scan_task: asyncio.Task | None = None
        self.active_upload_tasks: dict[str, asyncio.Task] = {}
        self.pending_signatures: set[tuple[str, tuple[str, ...]]] = set()
        self.deleted_task_ids: set[str] = set()
        self.current_upload_speed_bytes: float = 0.0
        self._speed_samples: dict[str, tuple[int, float]] = {}
        self._task_speed_bytes: dict[str, float] = {}
        self._channel_locks: dict[str, asyncio.Lock] = {}
        self._manual_task_ids: set[str] = set()
        self._locked_retry_tasks: dict[str, asyncio.Task] = {}
        self._scan_subdir_cursors: dict[str, int] = {}
        self._scan_jobs_inflight: set[str] = set()

    async def start(self) -> None:
        self.video_splitter.cleanup_orphans(self.upload_repo.list_task_ids())
        await self._restore_recoverable_tasks()
        if not self.scan_task:
            self.scan_task = asyncio.create_task(self._scanner_loop())
        await self.apply_runtime_settings()

    async def stop(self) -> None:
        for retry_task in list(self._locked_retry_tasks.values()):
            retry_task.cancel()
        for task in [self.worker_task, self.scan_task]:
            if task:
                task.cancel()
        self.worker_task = None
        await self.telegram.shutdown()
        await self.bot_api_pool.shutdown()

    async def apply_runtime_settings(self) -> None:
        self._configure_upload_clients()
        if not self.worker_task or self.worker_task.done():
            self.worker_task = asyncio.create_task(self._worker())

    def _configure_upload_clients(self) -> None:
        self.bot_api_pool.configure(
            self.settings_service.resolved_bot_api_accounts(),
            self.settings_service.resolved_proxy_settings().model_dump(mode="json"),
        )

    def current_uploader(self) -> UploadEngineClient:
        return self.telegram

    def current_engine_status(self) -> dict[str, str]:
        telethon_status = self.telegram.status()
        bot_status = self.bot_api_pool.status()
        enabled_bots = bot_status.get("enabled", 0)
        ready_bots = len(
            [
                item
                for item in bot_status.get("items", [])
                if item.get("enabled") and item.get("stage") == "authorized"
            ]
        )
        telethon_ready = telethon_status.get("stage") == "authorized"
        small_file_ready = telethon_ready or ready_bots > 0
        overall_stage = "authorized" if small_file_ready else "logged_out"
        last_error = telethon_status.get("last_error", "")
        if not last_error and enabled_bots > 0 and ready_bots <= 0:
            if telethon_ready:
                last_error = "Bot Token ??????????????? Telethon"
            else:
                last_error = "Bot Token ???????????????"
        if not telethon_ready:
            if last_error:
                last_error = f"{last_error}?Telethon ????????????"
            else:
                last_error = "Telethon ????????????"
        return {
            "stage": overall_stage,
            "engine": "hybrid",
            "last_error": last_error,
            "telethon_stage": telethon_status.get("stage", "logged_out"),
            "telethon_last_error": telethon_status.get("last_error", ""),
            "small_file_ready": "true" if small_file_ready else "false",
            "large_file_ready": "true" if telethon_ready else "false",
            "bot_enabled_accounts": str(enabled_bots),
            "bot_ready_accounts": str(ready_bots),
        }

    async def _restore_recoverable_tasks(self) -> None:
        for task in self.upload_repo.list_recoverable_tasks():
            updated = self.upload_repo.update_task(
                task.id,
                status=UploadStatus.PENDING,
                progress=0,
                error_message="",
                batch_items=self._build_batch_items(
                    self._task_display_items(task), UploadStatus.PENDING, progress=0
                ),
                completed_count=0,
            )
            recover_task = updated or task
            signature = self._task_signature(
                recover_task.folder_id,
                recover_task.batch_paths or [recover_task.relative_path],
                recover_task.source_relative_path,
            )
            if signature in self.pending_signatures:
                continue
            self.pending_signatures.add(signature)
            await self.queue.put(recover_task)

    async def enqueue_manual(self, folder_id: str, relative_paths: list[str]) -> None:
        folder = self._folder_map().get(folder_id)
        if not folder:
            raise ValueError("folder not found")
        normalized = self._normalize_relative_paths(folder, relative_paths)
        if not normalized:
            raise ValueError("no files selected")
        for lead_path, batch_paths, group_debug in self._build_upload_batches(
            folder, normalized, group_by_parent=False
        ):
            await self._enqueue_task(
                folder, lead_path, batch_paths, group_debug=group_debug, force=True, manual=True
            )

    async def trigger_scan(self, folder_id: str | None = None) -> None:
        folders = self.settings_service.settings.folders
        if folder_id:
            folders = [item for item in folders if item.id == folder_id]
        for folder in folders:
            if folder.id in self._scan_jobs_inflight:
                logger.info("trigger_scan skipped_inflight folder_id=%s name=%s", folder.id, folder.name)
                continue
            self._scan_jobs_inflight.add(folder.id)
            try:
                started_at = time.perf_counter()
                logger.info("trigger_scan started folder_id=%s name=%s", folder.id, folder.name)
                await self._scan_folder(folder)
                elapsed_ms = (time.perf_counter() - started_at) * 1000
                logger.info("trigger_scan finished folder_id=%s name=%s elapsed_ms=%.1f", folder.id, folder.name, elapsed_ms)
            except Exception:
                logger.exception("trigger_scan failed folder_id=%s name=%s", folder.id, folder.name)
                raise
            finally:
                self._scan_jobs_inflight.discard(folder.id)

    async def retry_task(self, task_id: str) -> UploadTask:
        task = self.upload_repo.get_task(task_id)
        if not task:
            raise ValueError("task not found")
        if task.status in {UploadStatus.PENDING, UploadStatus.UPLOADING}:
            raise ValueError("task is already queued or running")
        folder = self._folder_map().get(task.folder_id)
        if not folder:
            raise ValueError("folder not found")
        paths = self._resolve_task_paths(folder, task)
        if task.task_kind != "split_video" and not paths:
            raise ValueError("file not found")
        updated = self.upload_repo.update_task(
            task.id,
            status=UploadStatus.PENDING,
            progress=0,
            error_message="",
            completed_count=0,
            batch_items=self._build_batch_items(
                task.batch_paths or [task.relative_path],
                UploadStatus.PENDING,
                progress=0,
            ),
            updated_at=time.time(),
        )
        signature = self._task_signature(
            task.folder_id,
            task.batch_paths or [task.relative_path],
            task.source_relative_path,
        )
        self._manual_task_ids.add(task.id)
        if signature not in self.pending_signatures and updated:
            self.pending_signatures.add(signature)
            await self.queue.put(updated)
        return updated or task

    async def retry_tasks(self, task_ids: list[str]) -> list[UploadTask]:
        retried: list[UploadTask] = []
        seen: set[str] = set()
        for task_id in task_ids:
            if task_id in seen:
                continue
            seen.add(task_id)
            retried.append(await self.retry_task(task_id))
        return retried

    async def delete_tasks(self, task_ids: list[str]) -> int:
        deleted = 0
        seen: set[str] = set()
        for task_id in task_ids:
            if task_id in seen:
                continue
            seen.add(task_id)
            task = self.upload_repo.get_task(task_id)
            if not task:
                continue
            signature = self._task_signature(
                task.folder_id,
                task.batch_paths or [task.relative_path],
                task.source_relative_path,
            )
            self.pending_signatures.discard(signature)
            self.deleted_task_ids.add(task_id)
            retry_task = self._locked_retry_tasks.pop(task_id, None)
            if retry_task:
                retry_task.cancel()
            if self.upload_repo.delete_task(task_id):
                deleted += 1
            if task.task_kind == "split_video":
                self.video_splitter.cleanup(task.id)
            active_task = self.active_upload_tasks.get(task_id)
            if active_task:
                active_task.cancel()
        return deleted

    async def _scanner_loop(self) -> None:
        while True:
            now = time.time()
            for folder in self.settings_service.settings.folders:
                if folder.enabled and folder.auto_upload:
                    await self._scan_folder(folder)
            await self.apply_runtime_settings()
            elapsed = time.time() - now
            sleep_for = min(
                [
                    item.scan_interval_seconds
                    for item in self.settings_service.settings.folders
                ],
                default=30,
            )
            await asyncio.sleep(max(5, sleep_for - elapsed))

    async def _scan_folder(self, folder: FolderConfig) -> None:
        pending_paths = self._list_pending_paths(folder)
        if self._should_recheck_similarity_batches(folder, pending_paths):
            await asyncio.sleep(self.SIMILARITY_RECHECK_SECONDS)
            pending_paths = self._list_pending_paths(folder)
        for lead_path, batch_paths, group_debug in self._build_upload_batches(
            folder, pending_paths, group_by_parent=True
        ):
            await self._enqueue_task(
                folder, lead_path, batch_paths, group_debug=group_debug
            )

    async def _enqueue_task(
        self,
        folder: FolderConfig,
        relative_path: str,
        batch_paths: list[str],
        group_debug: str = "",
        force: bool = False,
        manual: bool = False,
    ) -> None:
        root = Path(folder.path)
        absolute_path = root / relative_path
        if not absolute_path.exists():
            return
        task_kind = "media_group" if len(batch_paths) > 1 else "single"
        should_split = len(batch_paths) == 1 and self._should_split_large_video(
            folder, absolute_path
        )
        source_relative_path = relative_path if should_split else ""
        source_absolute_path = str(absolute_path)
        effective_batch_paths = list(batch_paths)
        batch_item_names = list(batch_paths)
        task_id = str(uuid4())
        signature = self._task_signature(
            folder.id,
            [relative_path] if should_split else effective_batch_paths,
            source_relative_path,
        )
        if signature in self.pending_signatures:
            return
        if self.upload_repo.has_active_task_for_signature(
            folder.id,
            [relative_path] if should_split else effective_batch_paths,
            source_relative_path,
        ):
            return
        if should_split:
            try:
                split_result = self._split_large_video_if_needed(
                    folder, absolute_path, task_id
                )
            except Exception as exc:
                self._record_split_failure(
                    folder=folder,
                    relative_path=relative_path,
                    absolute_path=absolute_path,
                    task_id=task_id,
                    source_relative_path=relative_path,
                    source_absolute_path=source_absolute_path,
                    caption=self.scanner.build_caption(folder.path, str(absolute_path)),
                    error_message=f"split_error|{str(exc).strip() or 'video split failed'}",
                )
                self.video_splitter.cleanup(task_id)
                return
            if split_result:
                effective_batch_paths = [
                    str(path) for path in split_result.segment_paths
                ]
                batch_item_names = [path.name for path in split_result.segment_paths]
                task_kind = "split_video"
        stat = absolute_path.stat()
        if (
            not force
            and len(batch_paths) == 1
            and task_kind != "split_video"
            and self.upload_repo.is_uploaded(
                folder.id,
                relative_path,
                stat.st_size,
                stat.st_mtime,
            )
        ):
            return
        task = UploadTask(
            id=task_id,
            folder_id=folder.id,
            channel_id=folder.channel_id,
            bot_api_account_id=self._resolve_bot_api_account_id(folder.channel_id),
            relative_path=relative_path,
            absolute_path=str(absolute_path),
            source_relative_path=source_relative_path,
            source_absolute_path=source_absolute_path,
            task_kind=task_kind,
            batch_paths=effective_batch_paths,
            batch_items=self._build_batch_items(
                batch_item_names, UploadStatus.PENDING, progress=0
            ),
            completed_count=0,
            status=UploadStatus.PENDING,
            progress=0,
            error_message="",
            caption=self.scanner.build_caption(folder.path, str(absolute_path)),
            group_debug=group_debug,
            created_at=time.time(),
            updated_at=time.time(),
        )
        if manual:
            self._manual_task_ids.add(task_id)
        self.pending_signatures.add(signature)
        self.upload_repo.upsert_task(task)
        await self.queue.put(task)

    async def _worker(self) -> None:
        while True:
            task = await self._get_next_schedulable_task()
            signature = self._task_signature(
                task.folder_id,
                task.batch_paths or [task.relative_path],
                task.source_relative_path,
            )
            current = self.upload_repo.get_task(task.id)
            if task.id in self.deleted_task_ids or not current:
                self.deleted_task_ids.discard(task.id)
                self.pending_signatures.discard(signature)
                self.queue.task_done()
                continue
            active_upload_task = asyncio.create_task(self._process_task(task))
            self.active_upload_tasks[task.id] = active_upload_task
            try:
                await active_upload_task
            except asyncio.CancelledError:
                pass
            finally:
                self._clear_task_speed(task.id)
                self.active_upload_tasks.pop(task.id, None)
                self.deleted_task_ids.discard(task.id)
                self.queue.task_done()

    async def _get_next_schedulable_task(self) -> UploadTask:
        task = await self.queue.get()
        if not self.settings_service.settings.smart_queue_scheduling_enabled:
            return task

        pulled: list[UploadTask] = [task]
        try:
            while True:
                pulled.append(self.queue.get_nowait())
        except asyncio.QueueEmpty:
            pass

        selected_index = 0
        for index, candidate in enumerate(pulled):
            if self._task_can_send_now(candidate):
                selected_index = index
                break

        selected = pulled.pop(selected_index)
        for skipped in pulled[:selected_index]:
            self.upload_repo.update_task(
                skipped.id,
                error_message="smart_skip|当前 Bot 正在限频，任务暂时后移",
            )
        for candidate in pulled:
            self.queue.task_done()
            await self.queue.put(candidate)
        return selected

    def _task_can_send_now(self, task: UploadTask) -> bool:
        folder = self._folder_map().get(task.folder_id)
        paths = self._resolve_task_paths(folder, task)
        if not paths:
            return True
        try:
            bot_client = self._resolve_bot_uploader_for_paths(
                paths, task.bot_api_account_id, task.channel_id
            )
        except Exception:
            return True
        if not isinstance(bot_client, LocalBotApiClient):
            return True
        return bot_client.preview_wait_seconds() <= 0

    async def _process_task(self, task: UploadTask) -> None:
        folder = self._folder_map().get(task.folder_id)
        channel = self._channel_map().get(task.channel_id)
        signature = self._task_signature(
            task.folder_id,
            task.batch_paths or [task.relative_path],
            task.source_relative_path,
        )
        try:
            task, paths = self._ensure_task_paths(folder, task)
            if not folder or not channel or not paths:
                self.upload_repo.update_task(
                    task.id,
                    status=UploadStatus.FAILED,
                    error_message="missing folder, channel, or file",
                    batch_items=self._build_batch_items(
                        self._task_display_items(task),
                        UploadStatus.FAILED,
                        "missing folder, channel, or file",
                        0,
                    ),
                )
                return
            unavailable_path = self._find_unavailable_path(folder, task, paths)
            if unavailable_path:
                reason = (
                    f"文件被占用：{unavailable_path.name}"
                    if file_is_locked(unavailable_path)
                    else f"文件仍在写入，等待稳定：{unavailable_path.name}"
                )
                self.upload_repo.update_task(
                    task.id,
                    status=UploadStatus.LOCKED,
                    progress=0,
                    completed_count=0,
                    error_message=reason,
                    batch_items=self._build_batch_items(
                        self._task_display_items(task), UploadStatus.LOCKED, reason, 0
                    ),
                )
                self._schedule_locked_retry(task, folder, unavailable_path)
                return
            self.upload_repo.update_task(
                task.id,
                status=UploadStatus.UPLOADING,
                progress=0,
                error_message="",
                batch_items=self._build_batch_items(
                    self._task_display_items(task), UploadStatus.UPLOADING, progress=0
                ),
            )
            total_size = sum(path.stat().st_size for path in paths)
            force_document = any(
                classify_file(path) == "video"
                and not is_telegram_previewable_video(path)
                for path in paths
            )
            use_album = len(paths) > 1 and not force_document and all(
                classify_file(path) in {"video", "image"} for path in paths
            )
            bot_api_account_id = self._resolve_bot_api_account_id(task.channel_id)
            if bot_api_account_id and bot_api_account_id != task.bot_api_account_id:
                updated = self.upload_repo.update_task(
                    task.id, bot_api_account_id=bot_api_account_id
                )
                task = updated or task.model_copy(
                    update={"bot_api_account_id": bot_api_account_id}
                )
            bot_uploader = self._resolve_bot_uploader_for_paths(
                paths, task.bot_api_account_id, task.channel_id
            )
            selected_engine = "bot" if isinstance(bot_uploader, LocalBotApiClient) else "telethon"
            if task.uploader_engine != selected_engine:
                updated = self.upload_repo.update_task(
                    task.id,
                    uploader_engine=selected_engine,
                )
                task = updated or task.model_copy(update={"uploader_engine": selected_engine})
            if isinstance(bot_uploader, LocalBotApiClient):
                wait_seconds = bot_uploader.preview_wait_seconds()
                if wait_seconds > 0:
                    wait_reason = bot_uploader.last_wait_reason() or "global"
                    if wait_reason == "channel":
                        reason_text = "单频道限频"
                    elif wait_reason == "global+channel":
                        reason_text = "全局与单频道限频"
                    elif wait_reason == "auto_slowdown":
                        reason_text = "429 自动降速"
                    else:
                        reason_text = "全局限频"
                    wait_message = (
                        f"local_rate_limit|{reason_text}，等待约 {int(wait_seconds) + 1} 秒"
                    )
                    self.upload_repo.update_task(
                        task.id,
                        error_message=wait_message,
                        batch_items=self._build_batch_items(
                            self._task_display_items(task),
                            UploadStatus.UPLOADING,
                            wait_message,
                            0,
                        ),
                    )
            async with self._channel_lock(task.channel_id):
                message = await self._send_paths(
                    task.channel_id,
                    channel.target,
                    paths,
                    task.caption,
                    task.id,
                    total_size,
                    use_album,
                    force_document=force_document,
                    allow_album_fallback=task.task_kind == "split_video",
                    bot_api_account_id=task.bot_api_account_id,
                )
                message_id = self._extract_message_id(message)
            if task.task_kind == "split_video":
                source_path = Path(task.source_absolute_path or task.absolute_path)
                stat = source_path.stat()
                relative = task.source_relative_path or task.relative_path
                self.upload_repo.mark_uploaded(
                    folder.id,
                    relative,
                    str(source_path),
                    stat.st_size,
                    stat.st_mtime,
                    message_id,
                )
                self._apply_post_action(folder, source_path)
            else:
                for uploaded_path in paths:
                    stat = uploaded_path.stat()
                    relative = str(uploaded_path.relative_to(folder.path)).replace(
                        "\\", "/"
                    )
                    self.upload_repo.mark_uploaded(
                        folder.id,
                        relative,
                        str(uploaded_path),
                        stat.st_size,
                        stat.st_mtime,
                        message_id,
                    )
                    self._apply_post_action(folder, uploaded_path)
            self.upload_repo.update_task(
                task.id,
                status=UploadStatus.UPLOADED,
                progress=100,
                completed_count=len(paths),
                error_message="",
                batch_items=self._build_batch_items(
                    self._task_display_items(task), UploadStatus.UPLOADED, progress=100
                ),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = self._classify_upload_exception(exc, task)
            self.upload_repo.update_task(
                task.id,
                status=UploadStatus.FAILED,
                error_message=message,
                batch_items=self._build_batch_items(
                    self._task_display_items(task), UploadStatus.FAILED, message, 0
                ),
            )
        finally:
            if task.task_kind == "split_video":
                self.video_splitter.cleanup(task.id)
            self._manual_task_ids.discard(task.id)
            self.pending_signatures.discard(signature)

    def _find_unavailable_path(
        self,
        folder: FolderConfig,
        task: UploadTask,
        paths: list[Path],
    ) -> Path | None:
        manual_task = task.id in self._manual_task_ids
        for path in paths:
            if file_is_locked(path):
                return path
            if manual_task:
                continue
            if self.scanner.is_file_unavailable(
                task.folder_id, folder.path, path, folder.min_stable_seconds
            ):
                return path
        return None

    def _schedule_locked_retry(
        self,
        task: UploadTask,
        folder: FolderConfig,
        unavailable_path: Path,
    ) -> None:
        existing = self._locked_retry_tasks.pop(task.id, None)
        if existing:
            existing.cancel()
        delay = max(5, int(folder.min_stable_seconds or 0))
        if file_is_locked(unavailable_path):
            delay = max(delay, 5)
        self._locked_retry_tasks[task.id] = asyncio.create_task(
            self._retry_locked_task_later(task.id, delay)
        )

    async def _retry_locked_task_later(self, task_id: str, delay: int) -> None:
        try:
            await asyncio.sleep(delay)
            task = self.upload_repo.get_task(task_id)
            if not task or task.status != UploadStatus.LOCKED:
                return
            await self.retry_task(task_id)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            self._locked_retry_tasks.pop(task_id, None)

    async def _send_paths(
        self,
        channel_id: str,
        channel_target: str,
        paths: list[Path],
        caption: str,
        task_id: str,
        total_size: int,
        use_album: bool,
        force_document: bool = False,
        allow_album_fallback: bool = False,
        bot_api_account_id: str = "",
    ):
        uploader = self._resolve_uploader_for_paths(
            paths, bot_api_account_id, channel_id
        )
        if use_album:
            try:
                return await uploader.upload_file(
                    channel_target,
                    [str(path) for path in paths],
                    caption,
                    self._progress_callback(task_id, max(1, total_size)),
                    force_document=force_document,
                )
            except Exception:
                if not allow_album_fallback:
                    raise
                self.upload_repo.update_task(
                    task_id,
                    error_message="媒体组上传失败，已自动降级为逐个上传",
                )
                return await self._send_sequential_paths(
                    channel_id,
                    channel_target,
                    paths,
                    caption,
                    task_id,
                    total_size,
                    force_document,
                    bot_api_account_id,
                )
        return await self._send_sequential_paths(
            channel_id,
            channel_target,
            paths,
            caption,
            task_id,
            total_size,
            force_document,
            bot_api_account_id,
        )

    async def _send_sequential_paths(
        self,
        channel_id: str,
        channel_target: str,
        paths: list[Path],
        caption: str,
        task_id: str,
        total_size: int,
        force_document: bool = False,
        bot_api_account_id: str = "",
    ):
        uploaded_messages = []
        sent_bytes = 0
        for index, path in enumerate(paths):
            file_size = path.stat().st_size
            uploader = self._resolve_uploader_for_paths(
                [path], bot_api_account_id, channel_id
            )
            message = await uploader.upload_file(
                channel_target,
                [str(path)],
                caption if index == 0 else "",
                self._sequential_progress_callback(
                    task_id, total_size, sent_bytes, file_size
                ),
                force_document=force_document,
            )
            uploaded_messages.append(message)
            sent_bytes += file_size
            self.upload_repo.update_task(
                task_id,
                progress=round((sent_bytes / max(1, total_size)) * 100, 2),
            )
        return uploaded_messages

    def _progress_callback(self, task_id: str, total_size: int):
        def callback(current: int, total: int) -> None:
            self._record_upload_speed(task_id, current)
            denominator = total or total_size
            progress = round((current / max(1, denominator)) * 100, 2)
            task = self.upload_repo.get_task(task_id)
            batch_paths = self._task_display_items(task) if task else []
            self.upload_repo.update_task(
                task_id,
                progress=progress,
                batch_items=self._build_batch_items(
                    batch_paths or [task.relative_path] if task else [],
                    UploadStatus.UPLOADING,
                    progress=progress,
                ),
            )

        return callback

    def _sequential_progress_callback(
        self,
        task_id: str,
        total_size: int,
        sent_bytes: int,
        file_size: int,
    ):
        def callback(current: int, total: int) -> None:
            current_total = total or file_size
            uploaded = sent_bytes + min(current, current_total)
            self._record_upload_speed(task_id, uploaded)
            progress = round((uploaded / max(1, total_size)) * 100, 2)
            task = self.upload_repo.get_task(task_id)
            batch_paths = self._task_display_items(task) if task else []
            self.upload_repo.update_task(
                task_id,
                progress=progress,
                batch_items=self._build_batch_items(
                    batch_paths or [task.relative_path] if task else [],
                    UploadStatus.UPLOADING,
                    progress=progress,
                ),
            )

        return callback

    def _apply_post_action(self, folder: FolderConfig, path: Path) -> None:
        try:
            if folder.post_upload_action == "keep":
                return
            if folder.post_upload_action == "delete":
                path.unlink(missing_ok=True)
                return
            if folder.post_upload_action == "move":
                target_root = Path(folder.move_target_path)
                target_root.mkdir(parents=True, exist_ok=True)
                target = target_root / path.name
                if target.exists():
                    target = (
                        target_root / f"{path.stem}_{int(time.time())}{path.suffix}"
                    )
                shutil.move(str(path), str(target))
        except OSError:
            return

    def _extract_message_id(self, message) -> int | None:
        if isinstance(message, list):
            first = message[0] if message else None
            return getattr(first, "id", None)
        if isinstance(message, Message):
            return getattr(message, "id", None)
        return getattr(message, "id", None)

    def _folder_map(self) -> dict[str, FolderConfig]:
        return {item.id: item for item in self.settings_service.settings.folders}

    def _channel_map(self) -> dict[str, object]:
        return {item.id: item for item in self.settings_service.settings.channels}

    def _normalize_relative_paths(
        self, folder: FolderConfig, relative_paths: list[str]
    ) -> list[str]:
        root = Path(folder.path).resolve()
        normalized: list[str] = []
        seen: set[str] = set()
        for relative_path in relative_paths:
            candidate = (root / relative_path).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            if not candidate.exists() or not candidate.is_file():
                continue
            normalized_path = str(candidate.relative_to(root)).replace("\\", "/")
            if normalized_path in seen:
                continue
            seen.add(normalized_path)
            normalized.append(normalized_path)
        return normalized

    def _resolve_task_paths(
        self, folder: FolderConfig | None, task: UploadTask
    ) -> list[Path]:
        if not folder:
            return []
        resolved: list[Path] = []
        for relative_path in task.batch_paths or [task.relative_path]:
            candidate = Path(relative_path)
            if not candidate.is_absolute():
                root = Path(folder.path).resolve()
                candidate = (root / relative_path).resolve()
                try:
                    candidate.relative_to(root)
                except ValueError:
                    continue
            if candidate.exists() and candidate.is_file():
                resolved.append(candidate)
        return resolved

    def _build_upload_batches(
        self,
        folder: FolderConfig,
        relative_paths: list[str],
        *,
        group_by_parent: bool,
    ) -> list[tuple[str, list[str], str]]:
        if not relative_paths:
            return []

        if not folder.media_group_upload:
            return [
                (
                    relative_path,
                    [relative_path],
                    self._build_group_debug(
                        folder,
                        [relative_path],
                        group_by_parent,
                        "media-group disabled; single upload",
                        1,
                        1,
                    ),
                )
                for relative_path in relative_paths
            ]

        root = Path(folder.path)
        batches: list[tuple[str, list[str], str]] = []
        album_limit_bytes = min(
            ALBUM_MAX_FILE_SIZE, folder.upload_size_limit_mb * 1024 * 1024
        )
        for paths in self._group_relative_paths(
            relative_paths, group_by_parent
        ).values():
            media_paths = [
                path
                for path in paths
                if is_album_eligible(root / path, album_limit_bytes)
            ]
            non_media_paths = [path for path in paths if path not in media_paths]
            if folder.media_group_filename_similarity:
                similar_groups = self._cluster_similar_media_paths(
                    media_paths,
                    folder.media_group_similarity_threshold,
                )
                for similar_paths in similar_groups:
                    batches.extend(
                        self._build_media_batches(
                            folder,
                            similar_paths,
                            group_by_parent=group_by_parent,
                            base_reason=f"same parent + filename similarity >= {folder.media_group_similarity_threshold}%",
                        )
                    )
            elif len(media_paths) > 1:
                batches.extend(
                    self._build_media_batches(
                        folder,
                        media_paths,
                        group_by_parent=group_by_parent,
                        base_reason="same parent + eligible media grouped directly",
                    )
                )
            else:
                for path in media_paths:
                    batches.append(
                        (
                            path,
                            [path],
                            self._build_group_debug(
                                folder,
                                [path],
                                group_by_parent,
                                "eligible media count < 2; single upload",
                                1,
                                1,
                            ),
                        )
                    )
            for path in non_media_paths:
                batches.append(
                    (
                        path,
                        [path],
                        self._build_group_debug(
                            folder,
                            [path],
                            group_by_parent,
                            "file type or size not eligible for media-group",
                            1,
                            1,
                        ),
                    )
                )
        return batches

    def _group_relative_paths(
        self, relative_paths: list[str], group_by_parent: bool
    ) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for relative_path in relative_paths:
            parent = str(Path(relative_path).parent).replace("\\", "/")
            key = parent if group_by_parent else "__manual__"
            groups.setdefault(key, []).append(relative_path)
        return groups

    def _cluster_similar_media_paths(
        self, relative_paths: list[str], threshold: int
    ) -> list[list[str]]:
        if not relative_paths:
            return []

        names = [self._similarity_name(path) for path in relative_paths]
        clusters: list[list[str]] = []
        remaining = list(range(len(relative_paths)))

        while remaining:
            seed = remaining.pop(0)
            component = [seed]
            stayed: list[int] = []
            for candidate in remaining:
                if (
                    self._filename_similarity_percent(names[seed], names[candidate])
                    >= threshold
                ):
                    component.append(candidate)
                else:
                    stayed.append(candidate)
            remaining = stayed
            clusters.append([relative_paths[item] for item in component])
        return clusters

    def _build_media_batches(
        self,
        folder: FolderConfig,
        relative_paths: list[str],
        *,
        group_by_parent: bool,
        base_reason: str,
    ) -> list[tuple[str, list[str], str]]:
        if not relative_paths:
            return []
        batches: list[tuple[str, list[str], str]] = []
        total_chunks = max(
            1,
            (len(relative_paths) + self.TELEGRAM_MEDIA_GROUP_LIMIT - 1)
            // self.TELEGRAM_MEDIA_GROUP_LIMIT,
        )
        for index in range(0, len(relative_paths), self.TELEGRAM_MEDIA_GROUP_LIMIT):
            chunk = relative_paths[index : index + self.TELEGRAM_MEDIA_GROUP_LIMIT]
            chunk_number = (index // self.TELEGRAM_MEDIA_GROUP_LIMIT) + 1
            batches.append(
                (
                    chunk[0],
                    chunk,
                    self._build_group_debug(
                        folder,
                        chunk,
                        group_by_parent,
                        base_reason,
                        chunk_number,
                        total_chunks,
                    ),
                )
            )
        return batches

    def _build_group_debug(
        self,
        folder: FolderConfig,
        relative_paths: list[str],
        group_by_parent: bool,
        base_reason: str,
        chunk_number: int,
        total_chunks: int,
    ) -> str:
        parent = (
            str(Path(relative_paths[0]).parent).replace("\\", "/")
            if relative_paths
            else ""
        )
        scope = (
            f"parent: {parent or '(root)'}"
            if group_by_parent
            else "scope: manual selection"
        )
        similarity = (
            f"filename similarity: on ({folder.media_group_similarity_threshold}%)"
            if folder.media_group_filename_similarity
            else "filename similarity: off"
        )
        chunk_info = (
            f"chunk: {chunk_number}/{total_chunks}, max {self.TELEGRAM_MEDIA_GROUP_LIMIT} per media-group"
            if total_chunks > 1
            else f"chunk: not split, current group size {len(relative_paths)}"
        )
        return " | ".join([base_reason, scope, similarity, chunk_info])

    def _similarity_name(self, relative_path: str) -> str:
        stem = Path(relative_path).stem.casefold()
        stem = re.sub(r"[\[\(\{].*?[\]\)\}]", " ", stem, flags=re.UNICODE)
        for pattern in self.SIMILARITY_NOISE_PATTERNS:
            stem = re.sub(pattern, " ", stem, flags=re.IGNORECASE | re.UNICODE)
        stem = re.sub(
            r"(?:^|\s)(?:s\d{1,2}e\d{1,3}|e\d{1,3}|ep\d{1,3}|vol(?:ume)?\s*\d+|part\s*\d+|cd\s*\d+|disc\s*\d+|page\s*\d+|p\s*\d+)(?:\s|$)",
            " ",
            stem,
            flags=re.IGNORECASE | re.UNICODE,
        )
        stem = re.sub(r"[\W_]+", " ", stem, flags=re.UNICODE)
        tokens = [token for token in stem.split() if token]
        significant_tokens = [token for token in tokens if not token.isdigit()]
        base_tokens = significant_tokens or tokens
        return " ".join(base_tokens).strip()

    def _filename_similarity_percent(self, left: str, right: str) -> float:
        if not left or not right:
            return 0.0
        if left == right:
            return 100.0
        left_compact = left.replace(" ", "")
        right_compact = right.replace(" ", "")
        char_ratio = difflib.SequenceMatcher(None, left_compact, right_compact).ratio()
        left_tokens = set(left.split())
        right_tokens = set(right.split())
        token_ratio = 0.0
        if left_tokens and right_tokens:
            token_ratio = len(left_tokens & right_tokens) / len(
                left_tokens | right_tokens
            )
        prefix_ratio = difflib.SequenceMatcher(
            None, left[: min(len(left), 24)], right[: min(len(right), 24)]
        ).ratio()
        score = max(
            char_ratio,
            (char_ratio * 0.55) + (token_ratio * 0.3) + (prefix_ratio * 0.15),
        )
        return score * 100

    def _list_pending_paths(self, folder: FolderConfig) -> list[str]:
        if folder.id not in self._scan_subdir_cursors:
            self._scan_subdir_cursors[folder.id] = self.upload_repo.get_scan_cursor(
                folder.id
            )
        cursor = self._scan_subdir_cursors.get(folder.id, 0)
        entries, next_cursor, total_subdirs = self.scanner.list_scannable_files_chunked(
            folder.id,
            folder.path,
            min_stable_seconds=folder.min_stable_seconds,
            excluded_subdirs=folder.excluded_subdirs,
            subdir_cursor=cursor,
            subdir_batch_size=self.SCAN_SUBDIR_BATCH_SIZE,
        )
        persisted_cursor = next_cursor if total_subdirs > 0 else 0
        self._scan_subdir_cursors[folder.id] = persisted_cursor
        self.upload_repo.set_scan_cursor(folder.id, persisted_cursor)
        return [
            item.relative_path
            for item in entries
            if item.status == UploadStatus.PENDING
            and not self.upload_repo.has_active_task_for_file(
                folder.id, item.relative_path
            )
        ]

    def _should_recheck_similarity_batches(
        self, folder: FolderConfig, relative_paths: list[str]
    ) -> bool:
        if not folder.media_group_upload or not folder.media_group_filename_similarity:
            return False
        if len(relative_paths) < 2:
            return False

        root = Path(folder.path)
        album_limit_bytes = min(
            ALBUM_MAX_FILE_SIZE, folder.upload_size_limit_mb * 1024 * 1024
        )
        for paths in self._group_relative_paths(
            relative_paths, group_by_parent=True
        ).values():
            media_paths = [
                path
                for path in paths
                if is_album_eligible(root / path, album_limit_bytes)
            ]
            if len(media_paths) < 2:
                continue
            similar_groups = self._cluster_similar_media_paths(
                media_paths,
                folder.media_group_similarity_threshold,
            )
            if any(len(group) > 1 for group in similar_groups):
                return True
        return False

    def _task_signature(
        self, folder_id: str, batch_paths: list[str], source_relative_path: str = ""
    ) -> tuple[str, tuple[str, ...]]:
        return folder_id, tuple(
            [source_relative_path]
        ) if source_relative_path else tuple(sorted(batch_paths))

    def _record_upload_speed(self, task_id: str, uploaded_bytes: int) -> None:
        now = time.monotonic()
        last_uploaded, last_time = self._speed_samples.get(task_id, (0, now))
        self._speed_samples[task_id] = (uploaded_bytes, now)
        delta_bytes = uploaded_bytes - last_uploaded
        delta_time = now - last_time
        if delta_bytes <= 0 or delta_time <= 0:
            return
        instantaneous = delta_bytes / delta_time
        previous = self._task_speed_bytes.get(task_id, 0.0)
        self._task_speed_bytes[task_id] = (
            instantaneous
            if previous <= 0
            else (previous * 0.55) + (instantaneous * 0.45)
        )
        self.current_upload_speed_bytes = sum(self._task_speed_bytes.values())

    def _clear_task_speed(self, task_id: str) -> None:
        self._speed_samples.pop(task_id, None)
        self._task_speed_bytes.pop(task_id, None)
        self.current_upload_speed_bytes = sum(self._task_speed_bytes.values())

    def _split_large_video_if_needed(
        self, folder: FolderConfig, absolute_path: Path, task_id: str
    ):
        if not self._should_split_large_video(folder, absolute_path):
            return None
        return self.video_splitter.split(
            absolute_path,
            task_id,
            folder.upload_size_limit_mb,
            folder.segment_target_size_mb,
        )

    def _should_split_large_video(
        self, folder: FolderConfig, absolute_path: Path
    ) -> bool:
        if not folder.split_large_video_upload:
            return False
        if classify_file(absolute_path) != "video":
            return False
        limit_bytes = folder.upload_size_limit_mb * 1024 * 1024
        return absolute_path.stat().st_size > limit_bytes

    def _task_display_items(self, task: UploadTask | None) -> list[str]:
        if not task:
            return []
        if task.task_kind == "split_video":
            return [item.relative_path for item in task.batch_items] or [
                Path(path).name for path in task.batch_paths
            ]
        return task.batch_paths or [task.relative_path]

    def _resolve_bot_api_account_id(self, channel_id: str) -> str:
        channel = self._channel_map().get(channel_id)
        try:
            return self.bot_api_pool.pick_account_id(
                dispatch_mode=self.settings_service.settings.bot_dispatch_mode,
                default_account_id=self.settings_service.settings.default_bot_api_account_id,
                channel=channel,
            )
        except Exception:
            return ""

    def _resolve_bot_uploader_for_paths(
        self,
        paths: list[Path],
        bot_api_account_id: str,
        channel_id: str,
    ) -> LocalBotApiClient | None:
        if not paths:
            return None
        if any(path.stat().st_size > self.BOT_API_FILE_LIMIT_BYTES for path in paths):
            return None
        account_id = bot_api_account_id or self._resolve_bot_api_account_id(channel_id)
        if not account_id:
            return None
        try:
            return self.bot_api_pool.get_client(account_id)
        except Exception:
            return None

    def _resolve_uploader_for_paths(
        self,
        paths: list[Path],
        bot_api_account_id: str,
        channel_target_or_id: str,
    ) -> UploadEngineClient:
        bot_uploader = self._resolve_bot_uploader_for_paths(
            paths, bot_api_account_id, channel_target_or_id
        )
        return bot_uploader or self.telegram

    def _channel_lock(self, channel_id: str) -> asyncio.Lock:
        key = str(channel_id or "")
        if key not in self._channel_locks:
            self._channel_locks[key] = asyncio.Lock()
        return self._channel_locks[key]

    def _ensure_task_paths(
        self, folder: FolderConfig | None, task: UploadTask
    ) -> tuple[UploadTask, list[Path]]:
        if not folder:
            return task, []
        paths = self._resolve_task_paths(folder, task)
        if task.task_kind != "split_video" or paths:
            return task, paths
        source_path = Path(task.source_absolute_path or task.absolute_path)
        if not source_path.exists():
            return task, []
        try:
            split_result = self.video_splitter.split(
                source_path,
                task.id,
                folder.upload_size_limit_mb,
                folder.segment_target_size_mb,
            )
        except Exception as exc:
            message = f"split_error|{str(exc).strip() or 'video split failed'}"
            failed = self.upload_repo.update_task(
                task.id,
                status=UploadStatus.FAILED,
                error_message=message,
                batch_items=self._build_batch_items(
                    self._task_display_items(task), UploadStatus.FAILED, message, 0
                ),
            )
            self.video_splitter.cleanup(task.id)
            return failed or task, []
        updated = self.upload_repo.update_task(
            task.id,
            batch_paths=[str(path) for path in split_result.segment_paths],
            batch_items=self._build_batch_items(
                [path.name for path in split_result.segment_paths],
                UploadStatus.PENDING,
                progress=0,
            ),
        )
        return updated or task, split_result.segment_paths

    def _classify_upload_exception(self, exc: Exception, task: UploadTask) -> str:
        video_content_error_cls = getattr(
            tg_errors, "VideoContentTypeInvalidError", None
        ) or getattr(tg_errors, "VideoContentTypeError", None)
        if isinstance(exc, tg_errors.FloodWaitError):
            seconds = getattr(exc, "seconds", 0) or 0
            return f"rate_limit|Telegram 限流，请在 {seconds} 秒后重试"
        if isinstance(
            exc, (tg_errors.ChatWriteForbiddenError, tg_errors.ChatAdminRequiredError)
        ):
            return "permission|当前账号没有向目标频道发送消息的权限"
        if isinstance(exc, tg_errors.UserBannedInChannelError):
            return "permission|当前账号已被目标频道限制，无法上传"
        if video_content_error_cls and isinstance(exc, video_content_error_cls):
            return "format|视频编码或封装格式不被 Telegram 预览播放支持，建议转码后重试"
        if isinstance(exc, tg_errors.MediaEmptyError):
            return "format|媒体内容为空或 Telegram 未能识别该文件"
        if isinstance(exc, tg_errors.PhotoExtInvalidError):
            return "format|图片格式不受 Telegram 图片消息支持，请改为常见图片格式后重试"
        if isinstance(exc, tg_errors.PhotoInvalidDimensionsError):
            return "format|图片尺寸不符合 Telegram 要求，请调整后重试"
        if isinstance(exc, tg_errors.FilePartsInvalidError):
            return "upload_error|文件分片上传失败，通常与文件本身损坏或上传中断有关"
        if isinstance(exc, tg_errors.MessageTooLongError):
            return "format|文件描述过长，Telegram 拒绝发送"

        message = str(exc).strip()
        lowered = message.lower()
        if (
            "sendmultimediarequest" in lowered
            or "provided media object is invalid" in lowered
        ):
            return "batch_error|媒体组中包含 Telegram 不支持成组发送的文件，请拆分上传或关闭媒体组上传"
        if "file reference" in lowered and "expired" in lowered:
            return "session_error|Telegram 上传句柄已过期，请重试该任务"
        if "entity" in lowered and "not found" in lowered:
            return "not_found|目标频道不存在，或当前账号无法访问该频道"
        if "file parts" in lowered and "invalid" in lowered:
            return "upload_error|文件分片校验失败，请确认文件未损坏后重试"
        if "message caption is too long" in lowered:
            return "format|文件描述过长，Telegram 拒绝发送"
        if "too large" in lowered or "file is too big" in lowered:
            return "size_limit|文件体积超过当前账号或 Telegram 的发送限制"

        if message:
            return f"unknown|{message}"

        task_type = "媒体组任务" if len(task.batch_paths or []) > 1 else "单文件任务"
        return f"unknown|{task_type} 上传失败，请重试"

    def _build_batch_items(
        self,
        relative_paths: list[str],
        status: UploadStatus,
        error_message: str = "",
        progress: float = 0.0,
    ) -> list[UploadBatchItem]:
        return [
            UploadBatchItem(
                relative_path=relative_path,
                status=status,
                progress=progress,
                error_message=error_message,
            )
            for relative_path in relative_paths
        ]

    def _record_split_failure(
        self,
        *,
        folder: FolderConfig,
        relative_path: str,
        absolute_path: Path,
        task_id: str,
        source_relative_path: str,
        source_absolute_path: str,
        caption: str,
        error_message: str,
    ) -> None:
        task = UploadTask(
            id=task_id,
            folder_id=folder.id,
            channel_id=folder.channel_id,
            bot_api_account_id=self._resolve_bot_api_account_id(folder.channel_id),
            relative_path=relative_path,
            absolute_path=str(absolute_path),
            source_relative_path=source_relative_path,
            source_absolute_path=source_absolute_path,
            task_kind="split_video",
            batch_paths=[],
            batch_items=self._build_batch_items(
                [relative_path], UploadStatus.FAILED, error_message, 0
            ),
            completed_count=0,
            status=UploadStatus.FAILED,
            progress=0,
            error_message=error_message,
            caption=caption,
            group_debug="split failed before enqueue",
            created_at=time.time(),
            updated_at=time.time(),
        )
        self.upload_repo.upsert_task(task)
