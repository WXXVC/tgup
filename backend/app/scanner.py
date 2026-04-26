from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from .file_utils import build_caption, classify_file, derive_status, file_is_locked
from .models import FileEntry, FileListPagination, FileListStats, FileTreeNode
from .upload_repo import UploadRepository


@dataclass
class FileStabilitySnapshot:
    size: int
    modified_at: float
    first_seen_at: float


@dataclass
class FileListCacheEntry:
    cached_at: float
    entries: list[FileEntry]


@dataclass
class FileListPageCacheEntry:
    cached_at: float
    items: list[FileEntry]
    stats: FileListStats
    pagination: FileListPagination
    total_all: int


@dataclass
class DirectoryTreeCacheEntry:
    cached_at: float
    nodes: list[FileTreeNode]


class FolderScanner:
    FILE_LIST_CACHE_SECONDS = 1.5
    FILE_LIST_PAGE_CACHE_SECONDS = 1.5
    DIRECTORY_TREE_CACHE_SECONDS = 10.0
    DIRECTORY_TREE_VIEW_CACHE_SECONDS = 5.0
    DEFAULT_SCAN_SUBDIR_BATCH_SIZE = 12

    def __init__(self, upload_repo: UploadRepository) -> None:
        self.upload_repo = upload_repo
        self._stability_snapshots: dict[tuple[str, str], FileStabilitySnapshot] = {}
        self._file_list_cache: dict[tuple[str, str, int], FileListCacheEntry] = {}
        self._file_page_cache: dict[tuple[str, str, str, str, str, str, str, int, int], FileListPageCacheEntry] = {}
        self._directory_tree_cache: dict[str, DirectoryTreeCacheEntry] = {}
        self._directory_tree_view_cache: dict[tuple[str, str], DirectoryTreeCacheEntry] = {}

    def list_files(self, folder_id: str, path: str, min_stable_seconds: int = 0) -> list[FileEntry]:
        cache_key = (folder_id, str(Path(path)), int(min_stable_seconds or 0))
        now = time.time()
        cached = self._file_list_cache.get(cache_key)
        if cached and (now - cached.cached_at) <= self.FILE_LIST_CACHE_SECONDS:
            return [item.model_copy() for item in cached.entries]

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
            unavailable_reason = "" if uploaded else self.file_unavailable_reason(folder_id, path, file_path, min_stable_seconds)
            entries.append(
                FileEntry(
                    relative_path=relative,
                    absolute_path=str(file_path),
                    file_type=classify_file(file_path),
                    size=stat.st_size,
                    modified_at=stat.st_mtime,
                    status=derive_status(uploaded, unavailable_reason),
                )
            )
        self._prune_stability_snapshots(folder_id, active_keys)
        copied_entries = [item.model_copy() for item in entries]
        self._file_list_cache[cache_key] = FileListCacheEntry(
            cached_at=now,
            entries=copied_entries,
        )
        return [item.model_copy() for item in copied_entries]

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
            unavailable_reason = "" if uploaded else self.file_unavailable_reason(folder_id, path, file_path, min_stable_seconds)
            entries.append(
                FileEntry(
                    relative_path=relative,
                    absolute_path=str(file_path),
                    file_type=classify_file(file_path),
                    size=stat.st_size,
                    modified_at=stat.st_mtime,
                    status=derive_status(uploaded, unavailable_reason),
                )
            )
        self._prune_stability_snapshots(folder_id, active_keys)
        return entries

    def list_scannable_files_chunked(
        self,
        folder_id: str,
        path: str,
        *,
        min_stable_seconds: int = 0,
        excluded_subdirs: list[str] | None = None,
        subdir_cursor: int = 0,
        subdir_batch_size: int | None = None,
    ) -> tuple[list[FileEntry], int, int]:
        root = Path(path)
        if not root.exists():
            return [], 0, 0
        db_available = self.upload_repo.db_available_for_file_ops()
        excluded = {
            item.strip().replace("\\", "/").strip("/")
            for item in (excluded_subdirs or [])
            if item and item.strip().replace("\\", "/").strip("/")
        }
        batch_size = max(1, int(subdir_batch_size or self.DEFAULT_SCAN_SUBDIR_BATCH_SIZE))

        root_files: list[Path] = []
        top_level_dirs: list[Path] = []
        for item in sorted(root.iterdir(), key=lambda p: p.name.casefold()):
            relative = str(item.relative_to(root)).replace("\\", "/")
            if item.is_dir():
                if relative in excluded or self._is_excluded(relative, excluded):
                    continue
                top_level_dirs.append(item)
            elif item.is_file():
                root_files.append(item)

        total_subdirs = len(top_level_dirs)
        if total_subdirs <= 0:
            selected_dirs: list[Path] = []
            next_cursor = 0
        else:
            cursor = subdir_cursor % total_subdirs
            end = min(cursor + batch_size, total_subdirs)
            selected_dirs = top_level_dirs[cursor:end]
            next_cursor = 0 if end >= total_subdirs else end

        def build_entry(file_path: Path) -> FileEntry:
            stat = file_path.stat()
            relative = str(file_path.relative_to(root)).replace("\\", "/")
            uploaded = (
                self.upload_repo.is_uploaded(
                    folder_id, relative, stat.st_size, stat.st_mtime
                )
                if db_available
                else False
            )
            unavailable_reason = "" if uploaded else self.file_unavailable_reason(
                folder_id, path, file_path, min_stable_seconds
            )
            return FileEntry(
                relative_path=relative,
                absolute_path=str(file_path),
                file_type=classify_file(file_path),
                size=stat.st_size,
                modified_at=stat.st_mtime,
                status=derive_status(uploaded, unavailable_reason),
            )

        entries: list[FileEntry] = []
        for file_path in root_files:
            relative = str(file_path.relative_to(root)).replace("\\", "/")
            if self._is_excluded(relative, excluded):
                continue
            entries.append(build_entry(file_path))
        for top_dir in selected_dirs:
            for current_dir, dir_names, file_names in os.walk(top_dir):
                dir_names.sort(key=str.casefold)
                file_names.sort(key=str.casefold)
                current_path = Path(current_dir)
                for file_name in file_names:
                    file_path = current_path / file_name
                    relative = str(file_path.relative_to(root)).replace("\\", "/")
                    if self._is_excluded(relative, excluded):
                        continue
                    entries.append(build_entry(file_path))
        return entries, next_cursor, total_subdirs

    def build_caption(self, folder_path: str, absolute_path: str) -> str:
        return build_caption(Path(folder_path), Path(absolute_path))

    def list_files_paginated(
        self,
        folder_id: str,
        path: str,
        *,
        min_stable_seconds: int = 0,
        subdir: str = "",
        scope: str = "direct",
        file_type: str = "all",
        status: str = "all",
        search: str = "",
        page: int = 1,
        page_size: int = 10,
    ) -> tuple[list[FileEntry], FileListStats, FileListPagination, int]:
        root = Path(path)
        if not root.exists():
            empty_pagination = FileListPagination(
                page=1,
                page_size=page_size if page_size in {10, 20, 50, 100} else 10,
                total_pages=1,
                total_items=0,
                start=0,
                end=0,
            )
            return [], FileListStats(), empty_pagination, 0
        db_available = self.upload_repo.db_available_for_file_ops()

        normalized_subdir = str(subdir or "").strip().replace("\\", "/").strip("/")
        normalized_scope = scope if scope in {"direct", "recursive"} else "direct"
        normalized_type = str(file_type or "all").strip().lower()
        normalized_status = str(status or "all").strip().lower()
        normalized_search = str(search or "").strip().lower()
        page_size = page_size if page_size in {10, 20, 50, 100} else 10
        page = max(1, page)
        cache_key = (
            folder_id,
            str(root),
            normalized_subdir,
            normalized_scope,
            normalized_type,
            normalized_status,
            normalized_search,
            page,
            page_size,
        )
        now = time.time()
        cached_page = self._file_page_cache.get(cache_key)
        if cached_page and (now - cached_page.cached_at) <= self.FILE_LIST_PAGE_CACHE_SECONDS:
            return (
                [item.model_copy() for item in cached_page.items],
                cached_page.stats.model_copy(),
                cached_page.pagination.model_copy(),
                cached_page.total_all,
            )

        if db_available:
            indexed_items, indexed_stats, indexed_pagination, indexed_total_all, _ = (
                self.upload_repo.query_file_index(
                    folder_id=folder_id,
                    subdir=subdir,
                    scope=scope,
                    file_type=file_type,
                    status=status,
                    search=search,
                    page=page,
                    page_size=page_size,
                )
            )
            if indexed_total_all > 0:
                self._file_page_cache[cache_key] = FileListPageCacheEntry(
                    cached_at=now,
                    items=[item.model_copy() for item in indexed_items],
                    stats=indexed_stats.model_copy(),
                    pagination=indexed_pagination.model_copy(),
                    total_all=indexed_total_all,
                )
                return indexed_items, indexed_stats, indexed_pagination, indexed_total_all

        target_dir = root / normalized_subdir if normalized_subdir else root
        try:
            resolved_target = target_dir.resolve()
            resolved_root = root.resolve()
            resolved_target.relative_to(resolved_root)
        except Exception:
            empty_pagination = FileListPagination(
                page=1,
                page_size=page_size,
                total_pages=1,
                total_items=0,
                start=0,
                end=0,
            )
            return [], FileListStats(), empty_pagination, 0
        if not target_dir.exists() or not target_dir.is_dir():
            empty_pagination = FileListPagination(
                page=1,
                page_size=page_size,
                total_pages=1,
                total_items=0,
                start=0,
                end=0,
            )
            return [], FileListStats(), empty_pagination, 0

        def iter_file_paths():
            if normalized_scope == "direct":
                for item in sorted(target_dir.iterdir(), key=lambda p: p.name.casefold()):
                    if item.is_file():
                        yield item
                return
            for current_dir, dir_names, file_names in os.walk(target_dir):
                dir_names.sort(key=str.casefold)
                file_names.sort(key=str.casefold)
                current_path = Path(current_dir)
                for file_name in file_names:
                    yield current_path / file_name

        def build_entry(file_path: Path) -> FileEntry:
            stat = file_path.stat()
            relative = str(file_path.relative_to(root)).replace("\\", "/")
            uploaded = (
                self.upload_repo.is_uploaded(
                    folder_id, relative, stat.st_size, stat.st_mtime
                )
                if db_available
                else False
            )
            unavailable_reason = "" if uploaded else self.file_unavailable_reason(
                folder_id, path, file_path, min_stable_seconds
            )
            return FileEntry(
                relative_path=relative,
                absolute_path=str(file_path),
                file_type=classify_file(file_path),
                size=stat.st_size,
                modified_at=stat.st_mtime,
                status=derive_status(uploaded, unavailable_reason),
            )

        def matches(entry: FileEntry) -> bool:
            entry_type = (
                entry.file_type.value if hasattr(entry.file_type, "value") else str(entry.file_type)
            ).lower()
            if normalized_type != "all" and entry_type != normalized_type:
                return False
            entry_status = (
                entry.status.value if hasattr(entry.status, "value") else str(entry.status)
            ).lower()
            if normalized_status != "all" and entry_status != normalized_status:
                return False
            if normalized_search:
                haystack = f"{entry.relative_path} {entry.absolute_path}".lower()
                if normalized_search not in haystack:
                    return False
            return True

        total_in_scope = 0
        total_items = 0
        stats = FileListStats()
        page_entries: list[FileEntry] = []
        index_entries: list[FileEntry] = []
        start_index = (page - 1) * page_size
        end_index = start_index + page_size
        active_keys: set[tuple[str, str]] = set()
        for file_path in iter_file_paths():
            entry = build_entry(file_path)
            active_keys.add((folder_id, entry.relative_path))
            if normalized_scope == "direct":
                index_entries.append(entry)
            total_in_scope += 1
            if not matches(entry):
                continue
            total_items += 1
            stats.total += 1
            entry_status = (
                entry.status.value if hasattr(entry.status, "value") else str(entry.status)
            ).lower()
            if entry_status == "pending":
                stats.pending += 1
            elif entry_status == "uploaded":
                stats.uploaded += 1
            elif entry_status == "locked":
                stats.locked += 1
            elif entry_status == "stabilizing":
                stats.stabilizing += 1
            if start_index <= (total_items - 1) < end_index:
                page_entries.append(entry)

        if db_available and index_entries:
            self.upload_repo.upsert_file_index_entries(folder_id, index_entries)
        self._prune_stability_snapshots(folder_id, active_keys)
        total_pages = max(1, (total_items + page_size - 1) // page_size)
        safe_page = min(max(1, page), total_pages)
        if safe_page != page:
            # 若页码越界，则基于已知总量重新切页；只在极少数情况下触发一次轻量重扫。
            return self.list_files_paginated(
                folder_id,
                path,
                min_stable_seconds=min_stable_seconds,
                subdir=subdir,
                scope=scope,
                file_type=file_type,
                status=status,
                search=search,
                page=safe_page,
                page_size=page_size,
            )
        pagination = FileListPagination(
            page=safe_page,
            page_size=page_size,
            total_pages=total_pages,
            total_items=total_items,
            start=(start_index + 1) if total_items else 0,
            end=min(end_index, total_items),
        )
        self._file_page_cache[cache_key] = FileListPageCacheEntry(
            cached_at=now,
            items=[item.model_copy() for item in page_entries],
            stats=stats.model_copy(),
            pagination=pagination.model_copy(),
            total_all=total_in_scope,
        )
        return page_entries, stats, pagination, total_in_scope

    def filter_files(
        self,
        entries: list[FileEntry],
        *,
        subdir: str = "",
        scope: str = "direct",
        file_type: str = "all",
        status: str = "all",
        search: str = "",
    ) -> list[FileEntry]:
        normalized_subdir = str(subdir or "").strip().replace("\\", "/").strip("/")
        normalized_scope = scope if scope in {"direct", "recursive"} else "direct"
        normalized_type = str(file_type or "all").strip().lower()
        normalized_status = str(status or "all").strip().lower()
        normalized_search = str(search or "").strip().lower()

        def matches(entry: FileEntry) -> bool:
            file_dir = (
                entry.relative_path.split("/")[:-1]
                and "/".join(entry.relative_path.split("/")[:-1])
            ) or ""
            if normalized_subdir:
                prefix = f"{normalized_subdir}/"
                if normalized_scope == "direct":
                    if file_dir != normalized_subdir:
                        return False
                elif not (
                    entry.relative_path == normalized_subdir
                    or entry.relative_path.startswith(prefix)
                ):
                    return False
            elif normalized_scope == "direct" and file_dir:
                return False
            entry_type = (
                entry.file_type.value if hasattr(entry.file_type, "value") else str(entry.file_type)
            ).lower()
            if normalized_type != "all" and entry_type != normalized_type:
                return False
            entry_status = (
                entry.status.value if hasattr(entry.status, "value") else str(entry.status)
            ).lower()
            if normalized_status != "all" and entry_status != normalized_status:
                return False
            if normalized_search:
                haystack = f"{entry.relative_path} {entry.absolute_path}".lower()
                if normalized_search not in haystack:
                    return False
            return True

        return [entry for entry in entries if matches(entry)]

    def build_directory_tree(self, entries: list[FileEntry]) -> list[FileTreeNode]:
        mapping: dict[str, FileTreeNode] = {}
        for entry in entries:
            parts = entry.relative_path.split("/")[:-1]
            current = ""
            parent = ""
            for part in parts:
                current = f"{current}/{part}" if current else part
                if current not in mapping:
                    mapping[current] = FileTreeNode(
                        path=current,
                        name=part,
                        count=0,
                        depth=current.count("/"),
                        parent=parent,
                        children=[],
                    )
                mapping[current].count += 1
                if parent and current not in mapping[parent].children:
                    mapping[parent].children.append(current)
                parent = current
        return [
            mapping[key].model_copy(
                update={"children": sorted(mapping[key].children)}
            )
            for key in sorted(mapping)
        ]

    def build_directory_tree_for_root(self, path: str) -> list[FileTreeNode]:
        root = Path(path)
        cache_key = str(root.resolve()) if root.exists() else str(root)
        now = time.time()
        cached = self._directory_tree_cache.get(cache_key)
        if cached and (now - cached.cached_at) <= self.DIRECTORY_TREE_CACHE_SECONDS:
            return [item.model_copy() for item in cached.nodes]
        if not root.exists():
            return []
        mapping: dict[str, FileTreeNode] = {}
        for current_dir, dir_names, _ in os.walk(root):
            dir_names.sort(key=str.casefold)
            current_path = Path(current_dir)
            if current_path == root:
                parent_relative = ""
            else:
                parent_relative = str(current_path.relative_to(root)).replace("\\", "/")
            for dir_name in dir_names:
                child_relative = f"{parent_relative}/{dir_name}" if parent_relative else dir_name
                mapping[child_relative] = FileTreeNode(
                    path=child_relative,
                    name=dir_name,
                    count=0,
                    depth=child_relative.count("/"),
                    parent=parent_relative,
                    children=[],
                )
                if parent_relative and child_relative not in mapping[parent_relative].children:
                    mapping[parent_relative].children.append(child_relative)
        nodes = [
            mapping[key].model_copy(update={"children": sorted(mapping[key].children)})
            for key in sorted(mapping)
        ]
        self._directory_tree_cache[cache_key] = DirectoryTreeCacheEntry(
            cached_at=now,
            nodes=[item.model_copy() for item in nodes],
        )
        return [item.model_copy() for item in nodes]

    def build_directory_tree_for_view(
        self,
        path: str,
        subdir: str = "",
    ) -> list[FileTreeNode]:
        root = Path(path)
        if not root.exists() or not root.is_dir():
            return []

        normalized_subdir = str(subdir or "").strip().replace("\\", "/").strip("/")
        cache_key = (str(root.resolve()), normalized_subdir)
        now = time.time()
        cached = self._directory_tree_view_cache.get(cache_key)
        if cached and (now - cached.cached_at) <= self.DIRECTORY_TREE_VIEW_CACHE_SECONDS:
            return [item.model_copy() for item in cached.nodes]
        focus_parts = [part for part in normalized_subdir.split("/") if part] if normalized_subdir else []
        prefixes = []
        current = ""
        for part in focus_parts:
            current = f"{current}/{part}" if current else part
            prefixes.append(current)

        nodes: dict[str, FileTreeNode] = {}

        def ensure_node(relative_path: str) -> None:
            relative_path = str(relative_path or "").replace("\\", "/").strip("/")
            if not relative_path:
                return
            parts = relative_path.split("/")
            name = parts[-1]
            parent = "/".join(parts[:-1])
            if relative_path not in nodes:
                nodes[relative_path] = FileTreeNode(
                    path=relative_path,
                    name=name,
                    count=0,
                    depth=relative_path.count("/"),
                    parent=parent,
                    children=[],
                )
            if parent:
                ensure_node(parent)
                if relative_path not in nodes[parent].children:
                    nodes[parent].children.append(relative_path)

        def list_child_dirs(base_relative: str = "") -> None:
            base_dir = root / base_relative if base_relative else root
            try:
                entries = sorted(base_dir.iterdir(), key=lambda p: p.name.casefold())
            except Exception:
                return
            for item in entries:
                if not item.is_dir():
                    continue
                child_relative = f"{base_relative}/{item.name}" if base_relative else item.name
                ensure_node(child_relative)

        list_child_dirs("")
        for prefix in prefixes:
            ensure_node(prefix)
            list_child_dirs(prefix)

        nodes_list = [
            nodes[key].model_copy(update={"children": sorted(nodes[key].children)})
            for key in sorted(nodes)
        ]
        self._directory_tree_view_cache[cache_key] = DirectoryTreeCacheEntry(
            cached_at=now,
            nodes=[item.model_copy() for item in nodes_list],
        )
        return nodes_list

    def summarize_files(self, entries: list[FileEntry]) -> FileListStats:
        return FileListStats(
            total=len(entries),
            pending=sum(1 for item in entries if str(item.status) == "UploadStatus.PENDING" or getattr(item.status, "value", "") == "pending"),
            uploaded=sum(1 for item in entries if str(item.status) == "UploadStatus.UPLOADED" or getattr(item.status, "value", "") == "uploaded"),
            locked=sum(1 for item in entries if str(item.status) == "UploadStatus.LOCKED" or getattr(item.status, "value", "") == "locked"),
            stabilizing=sum(1 for item in entries if str(item.status) == "UploadStatus.STABILIZING" or getattr(item.status, "value", "") == "stabilizing"),
        )

    def paginate_files(
        self, entries: list[FileEntry], *, page: int = 1, page_size: int = 10
    ) -> tuple[list[FileEntry], FileListPagination]:
        page_size = page_size if page_size in {10, 20, 50, 100} else 10
        total_items = len(entries)
        total_pages = max(1, (total_items + page_size - 1) // page_size)
        page = min(max(1, page), total_pages)
        start_index = (page - 1) * page_size
        end_index = start_index + page_size
        return (
            entries[start_index:end_index],
            FileListPagination(
                page=page,
                page_size=page_size,
                total_pages=total_pages,
                total_items=total_items,
                start=(start_index + 1) if total_items else 0,
                end=min(end_index, total_items),
            ),
        )

    def is_file_unavailable(self, folder_id: str, folder_path: str, file_path: Path, min_stable_seconds: int = 0) -> bool:
        return self.file_unavailable_reason(folder_id, folder_path, file_path, min_stable_seconds) != ""

    def file_unavailable_reason(self, folder_id: str, folder_path: str, file_path: Path, min_stable_seconds: int = 0) -> str:
        if file_is_locked(file_path):
            return "locked"
        if not self.is_file_stable(folder_id, folder_path, file_path, min_stable_seconds):
            return "stabilizing"
        return ""

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

    def invalidate_file_list_cache(self, folder_id: str | None = None) -> None:
        if folder_id is None:
            self._file_list_cache.clear()
            self._file_page_cache.clear()
            self._directory_tree_cache.clear()
            self._directory_tree_view_cache.clear()
            return
        stale_keys = [key for key in self._file_list_cache if key[0] == folder_id]
        for key in stale_keys:
            self._file_list_cache.pop(key, None)
        stale_page_keys = [key for key in self._file_page_cache if key[0] == folder_id]
        for key in stale_page_keys:
            self._file_page_cache.pop(key, None)
        self._directory_tree_cache.clear()
        self._directory_tree_view_cache.clear()
