from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from uuid import uuid4

from telethon import errors as tg_errors
from telethon.tl.types import Message

from .file_utils import ALBUM_MAX_FILE_SIZE, classify_file, file_is_locked, is_album_eligible
from .models import FolderConfig, UploadBatchItem, UploadStatus, UploadTask
from .scanner import FolderScanner
from .settings_service import SettingsService
from .telegram_client import TelegramSessionManager
from .upload_repo import UploadRepository
from .video_splitter import VideoSplitter


class UploadManager:
    def __init__(
        self,
        settings_service: SettingsService,
        upload_repo: UploadRepository,
        scanner: FolderScanner,
        telegram: TelegramSessionManager,
    ) -> None:
        self.settings_service = settings_service
        self.upload_repo = upload_repo
        self.scanner = scanner
        self.telegram = telegram
        self.video_splitter = VideoSplitter()
        self.queue: asyncio.Queue[UploadTask] = asyncio.Queue()
        self.worker_tasks: list[asyncio.Task] = []
        self.scan_task: asyncio.Task | None = None
        self.active_upload_tasks: dict[str, asyncio.Task] = {}
        self.pending_signatures: set[tuple[str, tuple[str, ...]]] = set()
        self.deleted_task_ids: set[str] = set()
        self.current_upload_speed_bytes: float = 0.0
        self._speed_samples: dict[str, tuple[int, float]] = {}
        self._task_speed_bytes: dict[str, float] = {}

    async def start(self) -> None:
        self.video_splitter.cleanup_orphans(self.upload_repo.list_task_ids())
        if not self.scan_task:
            self.scan_task = asyncio.create_task(self._scanner_loop())
        await self.apply_runtime_settings()

    async def stop(self) -> None:
        for task in [*self.worker_tasks, self.scan_task]:
            if task:
                task.cancel()
        self.worker_tasks.clear()
        await self.telegram.shutdown()

    async def apply_runtime_settings(self) -> None:
        desired = max(1, min(4, self.settings_service.settings.upload_workers))
        current = len(self.worker_tasks)
        if current < desired:
            for index in range(current, desired):
                self.worker_tasks.append(asyncio.create_task(self._worker(index + 1)))
        elif current > desired:
            extra = self.worker_tasks[desired:]
            self.worker_tasks = self.worker_tasks[:desired]
            for task in extra:
                task.cancel()

    async def enqueue_manual(self, folder_id: str, relative_paths: list[str]) -> None:
        folder = self._folder_map().get(folder_id)
        if not folder:
            raise ValueError("folder not found")
        normalized = self._normalize_relative_paths(folder, relative_paths)
        if not normalized:
            raise ValueError("no files selected")
        for lead_path, batch_paths in self._build_upload_batches(folder, normalized, group_by_parent=False):
            await self._enqueue_task(folder, lead_path, batch_paths, force=True)

    async def trigger_scan(self, folder_id: str | None = None) -> None:
        folders = self.settings_service.settings.folders
        if folder_id:
            folders = [item for item in folders if item.id == folder_id]
        for folder in folders:
            await self._scan_folder(folder)

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
            batch_items=self._build_batch_items(task.batch_paths or [task.relative_path], UploadStatus.PENDING, progress=0),
            updated_at=time.time(),
        )
        signature = self._task_signature(task.folder_id, task.batch_paths or [task.relative_path], task.source_relative_path)
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
            signature = self._task_signature(task.folder_id, task.batch_paths or [task.relative_path], task.source_relative_path)
            self.pending_signatures.discard(signature)
            self.deleted_task_ids.add(task_id)
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
                [item.scan_interval_seconds for item in self.settings_service.settings.folders],
                default=30,
            )
            await asyncio.sleep(max(5, sleep_for - elapsed))

    async def _scan_folder(self, folder: FolderConfig) -> None:
        pending_paths = [
            item.relative_path
            for item in self.scanner.list_scannable_files(folder.id, folder.path, folder.excluded_subdirs)
            if item.status == UploadStatus.PENDING
        ]
        for lead_path, batch_paths in self._build_upload_batches(folder, pending_paths, group_by_parent=True):
            await self._enqueue_task(folder, lead_path, batch_paths)

    async def _enqueue_task(
        self,
        folder: FolderConfig,
        relative_path: str,
        batch_paths: list[str],
        force: bool = False,
    ) -> None:
        root = Path(folder.path)
        absolute_path = root / relative_path
        if not absolute_path.exists():
            return
        task_kind = "media_group" if len(batch_paths) > 1 else "single"
        should_split = len(batch_paths) == 1 and self._should_split_large_video(folder, absolute_path)
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
        if should_split:
            split_result = self._split_large_video_if_needed(folder, absolute_path, task_id)
            if split_result:
                effective_batch_paths = [str(path) for path in split_result.segment_paths]
                batch_item_names = [path.name for path in split_result.segment_paths]
                task_kind = "split_video"
        stat = absolute_path.stat()
        if not force and len(batch_paths) == 1 and task_kind != "split_video" and self.upload_repo.is_uploaded(
            folder.id,
            relative_path,
            stat.st_size,
            stat.st_mtime,
        ):
            return
        task = UploadTask(
            id=task_id,
            folder_id=folder.id,
            channel_id=folder.channel_id,
            relative_path=relative_path,
            absolute_path=str(absolute_path),
            source_relative_path=source_relative_path,
            source_absolute_path=source_absolute_path,
            task_kind=task_kind,
            batch_paths=effective_batch_paths,
            batch_items=self._build_batch_items(batch_item_names, UploadStatus.PENDING, progress=0),
            completed_count=0,
            status=UploadStatus.PENDING,
            progress=0,
            error_message="",
            caption=self.scanner.build_caption(folder.path, str(absolute_path)),
            created_at=time.time(),
            updated_at=time.time(),
        )
        self.pending_signatures.add(signature)
        self.upload_repo.upsert_task(task)
        await self.queue.put(task)

    async def _worker(self, _: int) -> None:
        while True:
            task = await self.queue.get()
            signature = self._task_signature(task.folder_id, task.batch_paths or [task.relative_path], task.source_relative_path)
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

    async def _process_task(self, task: UploadTask) -> None:
        folder = self._folder_map().get(task.folder_id)
        channel = self._channel_map().get(task.channel_id)
        signature = self._task_signature(task.folder_id, task.batch_paths or [task.relative_path], task.source_relative_path)
        try:
            task, paths = self._ensure_task_paths(folder, task)
            if not folder or not channel or not paths:
                self.upload_repo.update_task(
                    task.id,
                    status=UploadStatus.FAILED,
                    error_message="missing folder, channel, or file",
                    batch_items=self._build_batch_items(self._task_display_items(task), UploadStatus.FAILED, "missing folder, channel, or file", 0),
                )
                return
            locked_path = next((path for path in paths if file_is_locked(path)), None)
            if locked_path:
                self.upload_repo.update_task(
                    task.id,
                    status=UploadStatus.LOCKED,
                    error_message=f"file is locked: {locked_path.name}",
                    batch_items=self._build_batch_items(self._task_display_items(task), UploadStatus.LOCKED, f"file is locked: {locked_path.name}", 0),
                )
                return
            self.upload_repo.update_task(
                task.id,
                status=UploadStatus.UPLOADING,
                progress=0,
                error_message="",
                batch_items=self._build_batch_items(self._task_display_items(task), UploadStatus.UPLOADING, progress=0),
            )
            total_size = sum(path.stat().st_size for path in paths)
            use_album = len(paths) > 1 and all(classify_file(path) in {"video", "image"} for path in paths)
            message = await self._send_paths(channel.target, paths, task.caption, task.id, total_size, use_album, allow_album_fallback=task.task_kind == "split_video")
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
                    relative = str(uploaded_path.relative_to(folder.path)).replace("\\", "/")
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
                batch_items=self._build_batch_items(self._task_display_items(task), UploadStatus.UPLOADED, progress=100),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = self._classify_upload_exception(exc, task)
            self.upload_repo.update_task(
                task.id,
                status=UploadStatus.FAILED,
                error_message=message,
                batch_items=self._build_batch_items(self._task_display_items(task), UploadStatus.FAILED, message, 0),
            )
        finally:
            if task.task_kind == "split_video":
                self.video_splitter.cleanup(task.id)
            self.pending_signatures.discard(signature)

    async def _send_paths(
        self,
        channel_target: str,
        paths: list[Path],
        caption: str,
        task_id: str,
        total_size: int,
        use_album: bool,
        allow_album_fallback: bool = False,
    ):
        if use_album:
            try:
                return await self.telegram.upload_file(
                    channel_target,
                    [str(path) for path in paths],
                    caption,
                    self._progress_callback(task_id, max(1, total_size)),
                )
            except Exception:
                if not allow_album_fallback:
                    raise
                self.upload_repo.update_task(
                    task_id,
                    error_message="媒体组上传失败，已自动降级为逐个上传",
                )
                return await self._send_sequential_paths(channel_target, paths, caption, task_id, total_size)
        return await self._send_sequential_paths(channel_target, paths, caption, task_id, total_size)

    async def _send_sequential_paths(
        self,
        channel_target: str,
        paths: list[Path],
        caption: str,
        task_id: str,
        total_size: int,
    ):
        uploaded_messages = []
        sent_bytes = 0
        for index, path in enumerate(paths):
            file_size = path.stat().st_size
            message = await self.telegram.upload_file(
                channel_target,
                [str(path)],
                caption if index == 0 else "",
                self._sequential_progress_callback(task_id, total_size, sent_bytes, file_size),
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
                batch_items=self._build_batch_items(batch_paths or [task.relative_path] if task else [], UploadStatus.UPLOADING, progress=progress),
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
                batch_items=self._build_batch_items(batch_paths or [task.relative_path] if task else [], UploadStatus.UPLOADING, progress=progress),
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
                    target = target_root / f"{path.stem}_{int(time.time())}{path.suffix}"
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

    def _normalize_relative_paths(self, folder: FolderConfig, relative_paths: list[str]) -> list[str]:
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

    def _resolve_task_paths(self, folder: FolderConfig | None, task: UploadTask) -> list[Path]:
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
    ) -> list[tuple[str, list[str]]]:
        if not relative_paths:
            return []

        if not folder.media_group_upload:
            return [(relative_path, [relative_path]) for relative_path in relative_paths]

        root = Path(folder.path)
        groups: dict[str, list[str]] = {}
        ordered_keys: list[str] = []
        for relative_path in relative_paths:
            parent = str(Path(relative_path).parent).replace("\\", "/")
            key = parent if group_by_parent else "__manual__"
            if key not in groups:
                groups[key] = []
                ordered_keys.append(key)
            groups[key].append(relative_path)

        batches: list[tuple[str, list[str]]] = []
        album_limit_bytes = min(ALBUM_MAX_FILE_SIZE, folder.upload_size_limit_mb * 1024 * 1024)
        for key in ordered_keys:
            paths = groups[key]
            media_paths = [path for path in paths if is_album_eligible(root / path, album_limit_bytes)]
            non_media_paths = [path for path in paths if path not in media_paths]
            if len(media_paths) > 1:
                batches.append((media_paths[0], media_paths))
            else:
                for path in media_paths:
                    batches.append((path, [path]))
            for path in non_media_paths:
                batches.append((path, [path]))
        return batches

    def _task_signature(self, folder_id: str, batch_paths: list[str], source_relative_path: str = "") -> tuple[str, tuple[str, ...]]:
        return folder_id, tuple([source_relative_path]) if source_relative_path else tuple(sorted(batch_paths))

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
        self._task_speed_bytes[task_id] = instantaneous if previous <= 0 else (previous * 0.55) + (instantaneous * 0.45)
        self.current_upload_speed_bytes = sum(self._task_speed_bytes.values())

    def _clear_task_speed(self, task_id: str) -> None:
        self._speed_samples.pop(task_id, None)
        self._task_speed_bytes.pop(task_id, None)
        self.current_upload_speed_bytes = sum(self._task_speed_bytes.values())

    def _split_large_video_if_needed(self, folder: FolderConfig, absolute_path: Path, task_id: str):
        if not self._should_split_large_video(folder, absolute_path):
            return None
        return self.video_splitter.split(
            absolute_path,
            task_id,
            folder.upload_size_limit_mb,
            folder.segment_target_size_mb,
        )

    def _should_split_large_video(self, folder: FolderConfig, absolute_path: Path) -> bool:
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
            return [item.relative_path for item in task.batch_items] or [Path(path).name for path in task.batch_paths]
        return task.batch_paths or [task.relative_path]

    def _ensure_task_paths(self, folder: FolderConfig | None, task: UploadTask) -> tuple[UploadTask, list[Path]]:
        if not folder:
            return task, []
        paths = self._resolve_task_paths(folder, task)
        if task.task_kind != "split_video" or paths:
            return task, paths
        source_path = Path(task.source_absolute_path or task.absolute_path)
        if not source_path.exists():
            return task, []
        split_result = self.video_splitter.split(
            source_path,
            task.id,
            folder.upload_size_limit_mb,
            folder.segment_target_size_mb,
        )
        updated = self.upload_repo.update_task(
            task.id,
            batch_paths=[str(path) for path in split_result.segment_paths],
            batch_items=self._build_batch_items([path.name for path in split_result.segment_paths], UploadStatus.PENDING, progress=0),
        )
        return updated or task, split_result.segment_paths

    def _classify_upload_exception(self, exc: Exception, task: UploadTask) -> str:
        if isinstance(exc, tg_errors.FloodWaitError):
            seconds = getattr(exc, "seconds", 0) or 0
            return f"Telegram 限流，请在 {seconds} 秒后重试"
        if isinstance(exc, (tg_errors.ChatWriteForbiddenError, tg_errors.ChatAdminRequiredError)):
            return "当前账号没有向目标频道发送消息的权限"
        if isinstance(exc, tg_errors.UserBannedInChannelError):
            return "当前账号已被目标频道限制，无法上传"
        if isinstance(exc, tg_errors.VideoContentTypeError):
            return "视频编码或封装格式不被 Telegram 预览播放支持，建议转码后重试"
        if isinstance(exc, tg_errors.MediaEmptyError):
            return "媒体内容为空或 Telegram 未能识别该文件"
        if isinstance(exc, tg_errors.PhotoExtInvalidError):
            return "图片格式不受 Telegram 图片消息支持，请改为常见图片格式后重试"
        if isinstance(exc, tg_errors.PhotoInvalidDimensionsError):
            return "图片尺寸不符合 Telegram 要求，请调整后重试"
        if isinstance(exc, tg_errors.FilePartsInvalidError):
            return "文件分片上传失败，通常与文件本身损坏或上传中断有关"
        if isinstance(exc, tg_errors.MessageTooLongError):
            return "文件描述过长，Telegram 拒绝发送"

        message = str(exc).strip()
        lowered = message.lower()
        if "sendmultimediarequest" in lowered or "provided media object is invalid" in lowered:
            return "媒体组中包含 Telegram 不支持成组发送的文件，请拆分上传或关闭媒体组上传"
        if "file reference" in lowered and "expired" in lowered:
            return "Telegram 上传句柄已过期，请重试该任务"
        if "entity" in lowered and "not found" in lowered:
            return "目标频道不存在，或当前账号无法访问该频道"
        if "file parts" in lowered and "invalid" in lowered:
            return "文件分片校验失败，请确认文件未损坏后重试"
        if "message caption is too long" in lowered:
            return "文件描述过长，Telegram 拒绝发送"
        if "too large" in lowered or "file is too big" in lowered:
            return "文件体积超过当前账号或 Telegram 的发送限制"

        if message:
            return message

        task_type = "媒体组任务" if len(task.batch_paths or []) > 1 else "单文件任务"
        return f"{task_type} 上传失败，请重试"

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
