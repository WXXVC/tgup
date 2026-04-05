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
                relative_path TEXT NOT NULL,
                absolute_path TEXT NOT NULL,
                batch_paths TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL,
                progress REAL NOT NULL,
                error_message TEXT NOT NULL,
                caption TEXT NOT NULL,
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
        connection.commit()


@contextmanager
def get_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()
