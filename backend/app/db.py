from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from .config import DB_PATH


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS uploads (
                id TEXT PRIMARY KEY,
                folder_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                bot_api_account_id TEXT NOT NULL DEFAULT '',
                uploader_engine TEXT NOT NULL DEFAULT '',
                relative_path TEXT NOT NULL,
                absolute_path TEXT NOT NULL,
                source_relative_path TEXT NOT NULL DEFAULT '',
                source_absolute_path TEXT NOT NULL DEFAULT '',
                task_kind TEXT NOT NULL DEFAULT 'single',
                batch_paths TEXT NOT NULL DEFAULT '[]',
                batch_items TEXT NOT NULL DEFAULT '[]',
                completed_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                progress REAL NOT NULL,
                error_message TEXT NOT NULL,
                caption TEXT NOT NULL,
                group_debug TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                message_id INTEGER
            )
            """
        )
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(uploads)").fetchall()
        }
        if "batch_paths" not in columns:
            connection.execute(
                "ALTER TABLE uploads ADD COLUMN batch_paths TEXT NOT NULL DEFAULT '[]'"
            )
        if "source_relative_path" not in columns:
            connection.execute(
                "ALTER TABLE uploads ADD COLUMN source_relative_path TEXT NOT NULL DEFAULT ''"
            )
        if "source_absolute_path" not in columns:
            connection.execute(
                "ALTER TABLE uploads ADD COLUMN source_absolute_path TEXT NOT NULL DEFAULT ''"
            )
        if "task_kind" not in columns:
            connection.execute(
                "ALTER TABLE uploads ADD COLUMN task_kind TEXT NOT NULL DEFAULT 'single'"
            )
        if "batch_items" not in columns:
            connection.execute(
                "ALTER TABLE uploads ADD COLUMN batch_items TEXT NOT NULL DEFAULT '[]'"
            )
        if "completed_count" not in columns:
            connection.execute(
                "ALTER TABLE uploads ADD COLUMN completed_count INTEGER NOT NULL DEFAULT 0"
            )
        if "group_debug" not in columns:
            connection.execute(
                "ALTER TABLE uploads ADD COLUMN group_debug TEXT NOT NULL DEFAULT ''"
            )
        if "bot_api_account_id" not in columns:
            connection.execute(
                "ALTER TABLE uploads ADD COLUMN bot_api_account_id TEXT NOT NULL DEFAULT ''"
            )
        if "uploader_engine" not in columns:
            connection.execute(
                "ALTER TABLE uploads ADD COLUMN uploader_engine TEXT NOT NULL DEFAULT ''"
            )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS uploaded_files (
                folder_id TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                absolute_path TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                modified_at REAL NOT NULL,
                uploaded_at REAL NOT NULL,
                message_id INTEGER,
                PRIMARY KEY (folder_id, relative_path)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scan_state (
                folder_id TEXT PRIMARY KEY,
                subdir_cursor INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS file_index (
                folder_id TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                parent_dir TEXT NOT NULL DEFAULT '',
                absolute_path TEXT NOT NULL,
                file_type TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                modified_at REAL NOT NULL,
                status TEXT NOT NULL,
                last_seen_at REAL NOT NULL,
                PRIMARY KEY (folder_id, relative_path)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_file_index_folder_parent ON file_index(folder_id, parent_dir)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_file_index_folder_seen ON file_index(folder_id, last_seen_at)"
        )
        connection.commit()


@contextmanager
def get_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()
