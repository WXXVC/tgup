from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from .db import get_connection
from .models import (
    FileEntry,
    FileListPagination,
    FileListStats,
    UploadBatchItem,
    UploadListPagination,
    UploadListResponse,
    UploadStats,
    UploadStatus,
    UploadTask,
)


@dataclass
class UploadListCacheEntry:
    cached_at: float
    tasks: list[UploadTask]


class UploadRepository:
    TASK_LIST_CACHE_SECONDS = 1.0
    ACTIVE_TASK_STATUSES = {
        UploadStatus.PENDING.value,
        UploadStatus.UPLOADING.value,
        UploadStatus.LOCKED.value,
        UploadStatus.STABILIZING.value,
    }

    def __init__(self) -> None:
        self._task_list_cache: UploadListCacheEntry | None = None

    def list_tasks(self) -> list[UploadTask]:
        now = time.time()
        cached = self._task_list_cache
        if cached and (now - cached.cached_at) <= self.TASK_LIST_CACHE_SECONDS:
            return [item.model_copy() for item in cached.tasks]
        try:
            with get_connection() as connection:
                rows = connection.execute(
                    "SELECT * FROM uploads ORDER BY updated_at DESC, created_at DESC"
                ).fetchall()
        except sqlite3.Error:
            return []
        tasks = [self._decode_task(row) for row in rows]
        self._task_list_cache = UploadListCacheEntry(
            cached_at=now,
            tasks=[item.model_copy() for item in tasks],
        )
        return [item.model_copy() for item in tasks]

    def list_tasks_paginated(
        self,
        *,
        page: int = 1,
        page_size: int = 10,
        folder_id: str = "all",
        status: str = "all",
        error_category: str = "all",
        scheduling: str = "all",
        search: str = "",
        sort: str = "updated_desc",
    ) -> UploadListResponse:
        tasks = self.list_tasks()
        total_all = len(tasks)
        filtered = self._filter_tasks(
            tasks,
            folder_id=folder_id,
            status=status,
            error_category=error_category,
            scheduling=scheduling,
            search=search,
            sort=sort,
        )
        page_size = page_size if page_size in {10, 20, 50, 100} else 10
        total_items = len(filtered)
        total_pages = max(1, (total_items + page_size - 1) // page_size)
        page = min(max(1, page), total_pages)
        start_index = (page - 1) * page_size
        end_index = start_index + page_size
        return UploadListResponse(
            items=filtered[start_index:end_index],
            pagination=UploadListPagination(
                page=page,
                page_size=page_size,
                total_pages=total_pages,
                total_items=total_items,
                start=(start_index + 1) if total_items else 0,
                end=min(end_index, total_items),
            ),
            total_all=total_all,
        )

    def get_task(self, task_id: str) -> UploadTask | None:
        with get_connection() as connection:
            row = connection.execute("SELECT * FROM uploads WHERE id = ?", (task_id,)).fetchone()
        return self._decode_task(row) if row else None

    def list_task_ids(self) -> set[str]:
        with get_connection() as connection:
            rows = connection.execute("SELECT id FROM uploads").fetchall()
        return {row["id"] for row in rows}

    def list_recoverable_tasks(self) -> list[UploadTask]:
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM uploads WHERE status IN (?, ?) ORDER BY created_at ASC",
                (UploadStatus.PENDING.value, UploadStatus.UPLOADING.value),
            ).fetchall()
        return [self._decode_task(row) for row in rows]

    def upsert_task(self, task: UploadTask) -> UploadTask:
        payload = task.model_dump()
        payload["batch_paths"] = json.dumps(payload["batch_paths"], ensure_ascii=True)
        payload["batch_items"] = json.dumps([item.model_dump(mode="json") for item in task.batch_items], ensure_ascii=True)
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO uploads (
                    id, folder_id, channel_id, bot_api_account_id, uploader_engine, relative_path, absolute_path, source_relative_path, source_absolute_path, task_kind, batch_paths, batch_items, completed_count, status,
                    progress, error_message, caption, group_debug, created_at, updated_at
                ) VALUES (
                    :id, :folder_id, :channel_id, :bot_api_account_id, :uploader_engine, :relative_path, :absolute_path, :source_relative_path, :source_absolute_path, :task_kind, :batch_paths, :batch_items, :completed_count, :status,
                    :progress, :error_message, :caption, :group_debug, :created_at, :updated_at
                )
                ON CONFLICT(id) DO UPDATE SET
                    bot_api_account_id = excluded.bot_api_account_id,
                    uploader_engine = excluded.uploader_engine,
                    source_relative_path = excluded.source_relative_path,
                    source_absolute_path = excluded.source_absolute_path,
                    task_kind = excluded.task_kind,
                    batch_paths = excluded.batch_paths,
                    batch_items = excluded.batch_items,
                    completed_count = excluded.completed_count,
                    status = excluded.status,
                    progress = excluded.progress,
                    error_message = excluded.error_message,
                    caption = excluded.caption,
                    group_debug = excluded.group_debug,
                    updated_at = excluded.updated_at
                """,
                payload,
            )
            connection.commit()
        self.invalidate_task_list_cache()
        return task

    def _decode_task(self, row) -> UploadTask:
        payload = dict(row)
        payload["batch_paths"] = json.loads(payload.get("batch_paths") or "[]")
        payload["batch_items"] = [
            UploadBatchItem.model_validate(item)
            for item in json.loads(payload.get("batch_items") or "[]")
        ]
        return UploadTask.model_validate(payload)

    def update_task(self, task_id: str, **changes: Any) -> UploadTask | None:
        task = self.get_task(task_id)
        if not task:
            return None
        updated = task.model_copy(update={**changes, "updated_at": time.time()})
        return self.upsert_task(updated)

    def delete_task(self, task_id: str) -> bool:
        with get_connection() as connection:
            cursor = connection.execute("DELETE FROM uploads WHERE id = ?", (task_id,))
            connection.commit()
        if cursor.rowcount:
            self.invalidate_task_list_cache()
        return cursor.rowcount > 0

    def delete_tasks(self, task_ids: list[str]) -> int:
        if not task_ids:
            return 0
        placeholders = ", ".join("?" for _ in task_ids)
        with get_connection() as connection:
            cursor = connection.execute(
                f"DELETE FROM uploads WHERE id IN ({placeholders})",
                tuple(task_ids),
            )
            connection.commit()
        if cursor.rowcount:
            self.invalidate_task_list_cache()
        return cursor.rowcount

    def clear_tasks(self, statuses: list[UploadStatus] | None = None) -> int:
        with get_connection() as connection:
            if statuses:
                placeholders = ", ".join("?" for _ in statuses)
                cursor = connection.execute(
                    f"DELETE FROM uploads WHERE status IN ({placeholders})",
                    tuple(status.value for status in statuses),
                )
            else:
                cursor = connection.execute("DELETE FROM uploads")
            connection.commit()
        if cursor.rowcount:
            self.invalidate_task_list_cache()
        return cursor.rowcount

    def stats(self) -> UploadStats:
        stats = UploadStats()
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM uploads GROUP BY status"
            ).fetchall()
        total = 0
        for row in rows:
            status = row["status"]
            count = row["count"]
            total += count
            if hasattr(stats, status):
                setattr(stats, status, count)
        stats.total = total
        return stats

    def is_uploaded(self, folder_id: str, relative_path: str, size: int, modified_at: float) -> bool:
        try:
            with get_connection() as connection:
                row = connection.execute(
                    """
                    SELECT 1 FROM uploaded_files
                    WHERE folder_id = ? AND relative_path = ? AND file_size = ? AND modified_at = ?
                    """,
                    (folder_id, relative_path, size, modified_at),
                ).fetchone()
            return row is not None
        except sqlite3.Error:
            return False

    def mark_uploaded(
        self,
        folder_id: str,
        relative_path: str,
        absolute_path: str,
        size: int,
        modified_at: float,
        message_id: int | None = None,
    ) -> None:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO uploaded_files (
                    folder_id, relative_path, absolute_path, file_size, modified_at, uploaded_at, message_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(folder_id, relative_path) DO UPDATE SET
                    absolute_path = excluded.absolute_path,
                    file_size = excluded.file_size,
                    modified_at = excluded.modified_at,
                    uploaded_at = excluded.uploaded_at,
                    message_id = excluded.message_id
                """,
                (folder_id, relative_path, absolute_path, size, modified_at, time.time(), message_id),
            )
            connection.commit()

    def invalidate_task_list_cache(self) -> None:
        self._task_list_cache = None

    def get_scan_cursor(self, folder_id: str) -> int:
        if not folder_id:
            return 0
        try:
            with get_connection() as connection:
                row = connection.execute(
                    "SELECT subdir_cursor FROM scan_state WHERE folder_id = ?",
                    (folder_id,),
                ).fetchone()
        except sqlite3.Error:
            return 0
        if not row:
            return 0
        try:
            return max(0, int(row["subdir_cursor"] or 0))
        except Exception:
            return 0

    def set_scan_cursor(self, folder_id: str, cursor: int) -> None:
        if not folder_id:
            return
        normalized_cursor = max(0, int(cursor or 0))
        try:
            with get_connection() as connection:
                connection.execute(
                    """
                    INSERT INTO scan_state (folder_id, subdir_cursor, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(folder_id) DO UPDATE SET
                        subdir_cursor = excluded.subdir_cursor,
                        updated_at = excluded.updated_at
                    """,
                    (folder_id, normalized_cursor, time.time()),
                )
                connection.commit()
        except sqlite3.Error:
            return

    def db_available_for_file_ops(self) -> bool:
        try:
            with get_connection() as connection:
                connection.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    def upsert_file_index_entries(
        self,
        folder_id: str,
        entries: list[FileEntry],
    ) -> None:
        if not folder_id or not entries:
            return
        now = time.time()
        rows = []
        for entry in entries:
            parent_dir = "/".join(str(entry.relative_path).replace("\\", "/").split("/")[:-1])
            status = entry.status.value if hasattr(entry.status, "value") else str(entry.status)
            rows.append(
                (
                    folder_id,
                    entry.relative_path,
                    parent_dir,
                    entry.absolute_path,
                    entry.file_type,
                    int(entry.size),
                    float(entry.modified_at),
                    status,
                    now,
                )
            )
        try:
            with get_connection() as connection:
                connection.executemany(
                    """
                    INSERT INTO file_index (
                        folder_id, relative_path, parent_dir, absolute_path, file_type,
                        file_size, modified_at, status, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(folder_id, relative_path) DO UPDATE SET
                        parent_dir = excluded.parent_dir,
                        absolute_path = excluded.absolute_path,
                        file_type = excluded.file_type,
                        file_size = excluded.file_size,
                        modified_at = excluded.modified_at,
                        status = excluded.status,
                        last_seen_at = excluded.last_seen_at
                    """,
                    rows,
                )
                connection.commit()
        except sqlite3.Error:
            return

    def query_file_index(
        self,
        *,
        folder_id: str,
        subdir: str = "",
        scope: str = "direct",
        file_type: str = "all",
        status: str = "all",
        search: str = "",
        page: int = 1,
        page_size: int = 10,
    ) -> tuple[list[FileEntry], FileListStats, FileListPagination, int, int]:
        normalized_subdir = str(subdir or "").strip().replace("\\", "/").strip("/")
        normalized_scope = scope if scope in {"direct", "recursive"} else "direct"
        normalized_type = str(file_type or "all").strip().lower()
        normalized_status = str(status or "all").strip().lower()
        normalized_search = str(search or "").strip().lower()
        page_size = page_size if page_size in {10, 20, 50, 100} else 10
        page = max(1, page)

        conditions = ["folder_id = ?"]
        params: list[Any] = [folder_id]
        if normalized_scope == "direct":
            conditions.append("parent_dir = ?")
            params.append(normalized_subdir)
        elif normalized_subdir:
            conditions.append("(parent_dir = ? OR parent_dir LIKE ?)")
            params.extend([normalized_subdir, f"{normalized_subdir}/%"])
        if normalized_type != "all":
            conditions.append("LOWER(file_type) = ?")
            params.append(normalized_type)
        if normalized_status != "all":
            conditions.append("LOWER(status) = ?")
            params.append(normalized_status)
        if normalized_search:
            conditions.append("(LOWER(relative_path) LIKE ? OR LOWER(absolute_path) LIKE ?)")
            like = f"%{normalized_search}%"
            params.extend([like, like])

        where_sql = " AND ".join(conditions)
        try:
            with get_connection() as connection:
                total_all = connection.execute(
                    "SELECT COUNT(*) FROM file_index WHERE folder_id = ?",
                    (folder_id,),
                ).fetchone()[0]
                total_items = connection.execute(
                    f"SELECT COUNT(*) FROM file_index WHERE {where_sql}",
                    tuple(params),
                ).fetchone()[0]
                stat_rows = connection.execute(
                    f"SELECT status, COUNT(*) AS count FROM file_index WHERE {where_sql} GROUP BY status",
                    tuple(params),
                ).fetchall()
                total_pages = max(1, (total_items + page_size - 1) // page_size)
                page = min(max(1, page), total_pages)
                offset = (page - 1) * page_size
                rows = connection.execute(
                    f"""
                    SELECT relative_path, absolute_path, file_type, file_size, modified_at, status
                    FROM file_index
                    WHERE {where_sql}
                    ORDER BY relative_path COLLATE NOCASE ASC
                    LIMIT ? OFFSET ?
                    """,
                    tuple([*params, page_size, offset]),
                ).fetchall()
        except sqlite3.Error:
            empty = FileListPagination(
                page=1,
                page_size=page_size,
                total_pages=1,
                total_items=0,
                start=0,
                end=0,
            )
            return [], FileListStats(), empty, 0, 0
        stats = FileListStats(total=total_items)
        for row in stat_rows:
            key = str(row["status"] or "").lower()
            if key == "pending":
                stats.pending = row["count"]
            elif key == "uploaded":
                stats.uploaded = row["count"]
            elif key == "locked":
                stats.locked = row["count"]
            elif key == "stabilizing":
                stats.stabilizing = row["count"]
        items = [
            FileEntry(
                relative_path=row["relative_path"],
                absolute_path=row["absolute_path"],
                file_type=row["file_type"],
                size=row["file_size"],
                modified_at=row["modified_at"],
                status=row["status"],
            )
            for row in rows
        ]
        pagination = FileListPagination(
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            total_items=total_items,
            start=(offset + 1) if total_items else 0,
            end=min(offset + page_size, total_items),
        )
        return items, stats, pagination, total_all, total_items

    def has_active_task_for_file(self, folder_id: str, relative_path: str) -> bool:
        normalized_path = str(relative_path or "").strip().replace("\\", "/")
        if not folder_id or not normalized_path:
            return False
        for task in self.list_tasks():
            task_status = task.status.value if hasattr(task.status, "value") else str(task.status)
            if task.folder_id != folder_id or task_status not in self.ACTIVE_TASK_STATUSES:
                continue
            if task.source_relative_path == normalized_path:
                return True
            if task.relative_path == normalized_path:
                return True
            if normalized_path in (task.batch_paths or []):
                return True
        return False

    def has_active_task_for_signature(
        self,
        folder_id: str,
        batch_paths: list[str],
        source_relative_path: str = "",
    ) -> bool:
        normalized_batch_paths = tuple(sorted(
            str(item or "").strip().replace("\\", "/")
            for item in (batch_paths or [])
            if str(item or "").strip()
        ))
        normalized_source = str(source_relative_path or "").strip().replace("\\", "/")
        if not folder_id or (not normalized_batch_paths and not normalized_source):
            return False
        for task in self.list_tasks():
            task_status = task.status.value if hasattr(task.status, "value") else str(task.status)
            if task.folder_id != folder_id or task_status not in self.ACTIVE_TASK_STATUSES:
                continue
            task_source = str(task.source_relative_path or "").strip().replace("\\", "/")
            if normalized_source:
                if task_source == normalized_source:
                    return True
                continue
            task_batch = tuple(sorted(
                str(item or "").strip().replace("\\", "/")
                for item in (task.batch_paths or [task.relative_path])
                if str(item or "").strip()
            ))
            if task_batch == normalized_batch_paths:
                return True
        return False

    def _filter_tasks(
        self,
        tasks: list[UploadTask],
        *,
        folder_id: str,
        status: str,
        error_category: str,
        scheduling: str,
        search: str,
        sort: str,
    ) -> list[UploadTask]:
        status_rank = {name: index for index, name in enumerate(["uploading", "pending", "stabilizing", "failed", "locked", "uploaded"])}
        normalized_search = search.strip().lower()
        items = []
        for task in tasks:
            if folder_id != "all" and task.folder_id != folder_id:
                continue
            task_status = task.status.value if hasattr(task.status, "value") else str(task.status)
            if status != "all" and task_status != status:
                continue
            parsed = self._parse_upload_error(task.error_message or "")
            if error_category != "all" and parsed["category"] != error_category:
                continue
            if scheduling != "all":
                if scheduling == "normal":
                    if parsed["category"] in {"smart_skip", "local_rate_limit"}:
                        continue
                elif scheduling == "auto_slowdown":
                    if parsed["category"] != "local_rate_limit" or "429 自动降速" not in parsed["message"]:
                        continue
                elif parsed["category"] != scheduling:
                    continue
            if normalized_search:
                haystack = f"{task.relative_path} {task.caption or ''} {parsed['message']}".lower()
                if normalized_search not in haystack:
                    continue
            items.append(task)

        def compare_key(task: UploadTask):
            task_status = task.status.value if hasattr(task.status, "value") else str(task.status)
            rank = status_rank.get(task_status, 999)
            if sort == "created_desc":
                secondary = -(task.created_at or 0)
            elif sort == "progress_desc":
                secondary = -(task.progress or 0)
            elif sort == "name_asc":
                secondary = task.relative_path or ""
            else:
                secondary = -(task.updated_at or 0)
            tertiary = -(task.updated_at or 0)
            return (rank, secondary, tertiary)

        return sorted(items, key=compare_key)

    def _parse_upload_error(self, message: str) -> dict[str, str]:
        normalized = str(message or "").strip()
        separator = normalized.find("|")
        if separator <= 0:
            return {"category": "unknown", "message": normalized}
        return {
            "category": normalized[:separator],
            "message": normalized[separator + 1 :],
        }
