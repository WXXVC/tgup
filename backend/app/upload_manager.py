from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from uuid import uuid4

from telethon.tl.types import Message

from .file_utils import classify_file, file_is_locked
from .models import FolderConfig, UploadStatus, UploadTask
from .scanner import FolderScanner
from .settings_service import SettingsService
from .telegram_client import TelegramSessionManager
from .upload_repo import UploadRepository


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
        self.queue: asyncio.Queue[UploadTask] = asyncio.Queue()
        self.worker_task: asyncio.Task | None = None
        self.scan_task: asyncio.Task | None = None
        self.pending_signatures: set[tuple[str, tuple[str, ...]]] = set()

    async def start(self) -> None:
        if not self.worker_task:
            self.worker_task = asyncio.create_task(self._worker())
        if not self.scan_task:
            self.scan_task = asyncio.create_task(self._scanner_loop())

    async def stop(self) -> None:
        for task in (self.worker_task, self.scan_task):
            if task:
                task.cancel()
        await self.telegram.shutdown()

    async def enqueue_manual(self, folder_id: str, relative_paths: list[str]) -> None:
        folder = self._folder_map().get(folder_id)
        if not folder:
            raise ValueError("folder not found")
        normalized = self._normalize_relative_paths(folder, relative_paths)
        if not normalized:
            raise ValueError("no files selected")
        if len(normalized) > 1 and self._all_media(folder, normalized):
            await self._enqueue_task(folder, normalized[0], normalized)
            return
        for relative_path in normalized:
            await self._enqueue_task(folder, relative_path, [relative_path])

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
        if not paths:
            raise ValueError("file not found")
        updated = self.upload_repo.update_task(
            task.id,
            status=UploadStatus.PENDING,
            progress=0,
            error_message="",
            updated_at=time.time(),
        )
        signature = self._task_signature(task.folder_id, task.batch_paths or [task.relative_path])
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

    async def _scanner_loop(self) -> None:
        while True:
            now = time.time()
            for folder in self.settings_service.settings.folders:
                if folder.enabled and folder.auto_upload:
                    await self._scan_folder(folder)
            elapsed = time.time() - now
            sleep_for = min(
                [item.scan_interval_seconds for item in self.settings_service.settings.folders],
                default=30,
            )
            await asyncio.sleep(max(5, sleep_for - elapsed))

    async def _scan_folder(self, folder: FolderConfig) -> None:
        for item in self.scanner.list_files(folder.id, folder.path):
            if item.status == UploadStatus.PENDING:
                await self._enqueue_task(folder, item.relative_path, [item.relative_path])

    async def _enqueue_task(
        self,
        folder: FolderConfig,
        relative_path: str,
        batch_paths: list[str],
    ) -> None:
        root = Path(folder.path)
        absolute_path = root / relative_path
        if not absolute_path.exists():
            return
        signature = self._task_signature(folder.id, batch_paths)
        if signature in self.pending_signatures:
            return
        stat = absolute_path.stat()
        if len(batch_paths) == 1 and self.upload_repo.is_uploaded(
            folder.id,
            relative_path,
            stat.st_size,
            stat.st_mtime,
        ):
            return
        task = UploadTask(
            id=str(uuid4()),
            folder_id=folder.id,
            channel_id=folder.channel_id,
            relative_path=relative_path,
            absolute_path=str(absolute_path),
            batch_paths=batch_paths,
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

    async def _worker(self) -> None:
        while True:
            task = await self.queue.get()
            await self._process_task(task)
            self.queue.task_done()

    async def _process_task(self, task: UploadTask) -> None:
        folder = self._folder_map().get(task.folder_id)
        channel = self._channel_map().get(task.channel_id)
        signature = self._task_signature(task.folder_id, task.batch_paths or [task.relative_path])
        try:
            paths = self._resolve_task_paths(folder, task)
            if not folder or not channel or not paths:
                self.upload_repo.update_task(
                    task.id,
                    status=UploadStatus.FAILED,
                    error_message="missing folder, channel, or file",
                )
                return
            locked_path = next((path for path in paths if file_is_locked(path)), None)
            if locked_path:
                self.upload_repo.update_task(
                    task.id,
                    status=UploadStatus.LOCKED,
                    error_message=f"file is locked: {locked_path.name}",
                )
                return
            self.upload_repo.update_task(task.id, status=UploadStatus.UPLOADING, progress=0, error_message="")
            total_size = sum(path.stat().st_size for path in paths)
            use_album = len(paths) > 1 and all(classify_file(path) in {"video", "image"} for path in paths)
            message = await self._send_paths(channel.target, paths, task.caption, task.id, total_size, use_album)
            message_id = self._extract_message_id(message)
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
            self.upload_repo.update_task(task.id, status=UploadStatus.UPLOADED, progress=100)
        except Exception as exc:
            self.upload_repo.update_task(task.id, status=UploadStatus.FAILED, error_message=str(exc))
        finally:
            self.pending_signatures.discard(signature)

    async def _send_paths(
        self,
        channel_target: str,
        paths: list[Path],
        caption: str,
        task_id: str,
        total_size: int,
        use_album: bool,
    ):
        if use_album:
            return await self.telegram.upload_file(
                channel_target,
                [str(path) for path in paths],
                caption,
                self._progress_callback(task_id, max(1, total_size)),
            )
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
            denominator = total or total_size
            progress = round((current / max(1, denominator)) * 100, 2)
            self.upload_repo.update_task(task_id, progress=progress)

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
            progress = round((uploaded / max(1, total_size)) * 100, 2)
            self.upload_repo.update_task(task_id, progress=progress)

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
        root = Path(folder.path).resolve()
        resolved: list[Path] = []
        for relative_path in task.batch_paths or [task.relative_path]:
            candidate = (root / relative_path).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            if candidate.exists() and candidate.is_file():
                resolved.append(candidate)
        return resolved

    def _all_media(self, folder: FolderConfig, relative_paths: list[str]) -> bool:
        root = Path(folder.path)
        return all(classify_file(root / relative_path) in {"video", "image"} for relative_path in relative_paths)

    def _task_signature(self, folder_id: str, batch_paths: list[str]) -> tuple[str, tuple[str, ...]]:
        return folder_id, tuple(sorted(batch_paths))
