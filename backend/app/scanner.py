from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from .file_utils import build_caption, classify_file, derive_status, file_is_locked
from .models import FileEntry
from .upload_repo import UploadRepository


@dataclass
class FileStabilitySnapshot:
    size: int
    modified_at: float
    first_seen_at: float


class FolderScanner:
    def __init__(self, upload_repo: UploadRepository) -> None:
        self.upload_repo = upload_repo
        self._stability_snapshots: dict[tuple[str, str], FileStabilitySnapshot] = {}

    def list_files(self, folder_id: str, path: str, min_stable_seconds: int = 0) -> list[FileEntry]:
        root = Path(path)
        if not root.exists():
            return []
        entries: list[FileEntry] = []
        active_keys: set[tuple[str, str]] = set()
        for file_path in sorted([item for item in root.rglob("*") if item.is_file()]):
            stat = file_path.stat()
            relative = str(file_path.relative_to(root)).replace("\\", "/")
            active_keys.add((folder_id, relative))
            uploaded = self.upload_repo.is_uploaded(folder_id, relative, stat.st_size, stat.st_mtime)
            locked = False if uploaded else self.is_file_unavailable(folder_id, path, file_path, min_stable_seconds)
            entries.append(
                FileEntry(
                    relative_path=relative,
                    absolute_path=str(file_path),
                    file_type=classify_file(file_path),
                    size=stat.st_size,
                    modified_at=stat.st_mtime,
                    status=derive_status(uploaded, locked),
                )
            )
        self._prune_stability_snapshots(folder_id, active_keys)
        return entries

    def list_scannable_files(
        self,
        folder_id: str,
        path: str,
        min_stable_seconds: int = 0,
        excluded_subdirs: list[str] | None = None,
    ) -> list[FileEntry]:
        root = Path(path)
        if not root.exists():
            return []
        excluded = {
            item.strip().replace("\\", "/").strip("/")
            for item in (excluded_subdirs or [])
            if item and item.strip().replace("\\", "/").strip("/")
        }
        entries: list[FileEntry] = []
        active_keys: set[tuple[str, str]] = set()
        for file_path in sorted([item for item in root.rglob("*") if item.is_file()]):
            relative = str(file_path.relative_to(root)).replace("\\", "/")
            if self._is_excluded(relative, excluded):
                continue
            active_keys.add((folder_id, relative))
            stat = file_path.stat()
            uploaded = self.upload_repo.is_uploaded(folder_id, relative, stat.st_size, stat.st_mtime)
            locked = False if uploaded else self.is_file_unavailable(folder_id, path, file_path, min_stable_seconds)
            entries.append(
                FileEntry(
                    relative_path=relative,
                    absolute_path=str(file_path),
                    file_type=classify_file(file_path),
                    size=stat.st_size,
                    modified_at=stat.st_mtime,
                    status=derive_status(uploaded, locked),
                )
            )
        self._prune_stability_snapshots(folder_id, active_keys)
        return entries

    def build_caption(self, folder_path: str, absolute_path: str) -> str:
        return build_caption(Path(folder_path), Path(absolute_path))

    def is_file_unavailable(self, folder_id: str, folder_path: str, file_path: Path, min_stable_seconds: int = 0) -> bool:
        if file_is_locked(file_path):
            return True
        return not self.is_file_stable(folder_id, folder_path, file_path, min_stable_seconds)

    def is_file_stable(self, folder_id: str, folder_path: str, file_path: Path, min_stable_seconds: int = 0) -> bool:
        if min_stable_seconds <= 0:
            return True
        root = Path(folder_path)
        stat = file_path.stat()
        relative = str(file_path.relative_to(root)).replace("\\", "/")
        key = (folder_id, relative)
        now = time.time()
        snapshot = self._stability_snapshots.get(key)
        if not snapshot or snapshot.size != stat.st_size or snapshot.modified_at != stat.st_mtime:
            self._stability_snapshots[key] = FileStabilitySnapshot(
                size=stat.st_size,
                modified_at=stat.st_mtime,
                first_seen_at=now,
            )
            return False
        return (now - snapshot.first_seen_at) >= min_stable_seconds

    def _is_excluded(self, relative_path: str, excluded_subdirs: set[str]) -> bool:
        parent = str(Path(relative_path).parent).replace("\\", "/").strip("/")
        if not parent:
            return False
        return any(parent == item or parent.startswith(f"{item}/") for item in excluded_subdirs)

    def _prune_stability_snapshots(self, folder_id: str, active_keys: set[tuple[str, str]]) -> None:
        stale_keys = [
            key for key in self._stability_snapshots
            if key[0] == folder_id and key not in active_keys
        ]
        for key in stale_keys:
            self._stability_snapshots.pop(key, None)
