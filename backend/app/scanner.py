from __future__ import annotations

from pathlib import Path

from .file_utils import build_caption, classify_file, derive_status, file_is_locked
from .models import FileEntry
from .upload_repo import UploadRepository


class FolderScanner:
    def __init__(self, upload_repo: UploadRepository) -> None:
        self.upload_repo = upload_repo

    def list_files(self, folder_id: str, path: str) -> list[FileEntry]:
        root = Path(path)
        if not root.exists():
            return []
        entries: list[FileEntry] = []
        for file_path in sorted([item for item in root.rglob("*") if item.is_file()]):
            stat = file_path.stat()
            relative = str(file_path.relative_to(root)).replace("\\", "/")
            locked = file_is_locked(file_path)
            uploaded = self.upload_repo.is_uploaded(folder_id, relative, stat.st_size, stat.st_mtime)
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
        return entries

    def build_caption(self, folder_path: str, absolute_path: str) -> str:
        return build_caption(Path(folder_path), Path(absolute_path))
