from __future__ import annotations

import json
import time
from typing import Any

from .db import get_connection
from .models import UploadBatchItem, UploadStats, UploadStatus, UploadTask


class UploadRepository:
    def list_tasks(self) -> list[UploadTask]:
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM uploads ORDER BY updated_at DESC, created_at DESC"
            ).fetchall()
        return [self._decode_task(row) for row in rows]

    def get_task(self, task_id: str) -> UploadTask | None:
        with get_connection() as connection:
            row = connection.execute("SELECT * FROM uploads WHERE id = ?", (task_id,)).fetchone()
        return self._decode_task(row) if row else None

    def list_task_ids(self) -> set[str]:
        with get_connection() as connection:
            rows = connection.execute("SELECT id FROM uploads").fetchall()
        return {row["id"] for row in rows}

    def upsert_task(self, task: UploadTask) -> UploadTask:
        payload = task.model_dump()
        payload["batch_paths"] = json.dumps(payload["batch_paths"], ensure_ascii=True)
        payload["batch_items"] = json.dumps([item.model_dump(mode="json") for item in task.batch_items], ensure_ascii=True)
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO uploads (
                    id, folder_id, channel_id, relative_path, absolute_path, source_relative_path, source_absolute_path, task_kind, batch_paths, batch_items, completed_count, status,
                    progress, error_message, caption, created_at, updated_at
                ) VALUES (
                    :id, :folder_id, :channel_id, :relative_path, :absolute_path, :source_relative_path, :source_absolute_path, :task_kind, :batch_paths, :batch_items, :completed_count, :status,
                    :progress, :error_message, :caption, :created_at, :updated_at
                )
                ON CONFLICT(id) DO UPDATE SET
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
                    updated_at = excluded.updated_at
                """,
                payload,
            )
            connection.commit()
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
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM uploaded_files
                WHERE folder_id = ? AND relative_path = ? AND file_size = ? AND modified_at = ?
                """,
                (folder_id, relative_path, size, modified_at),
            ).fetchone()
        return row is not None

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
