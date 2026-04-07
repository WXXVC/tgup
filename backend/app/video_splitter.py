from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import TEMP_SEGMENTS_DIR


@dataclass
class SplitResult:
    segment_paths: list[Path]


class VideoSplitter:
    def __init__(self, root: Path = TEMP_SEGMENTS_DIR) -> None:
        self.root = root

    def split(self, source_path: Path, task_id: str, upload_limit_mb: int, target_size_mb: int) -> SplitResult:
        self._ensure_ffmpeg()
        self.cleanup(task_id)
        task_dir = self.root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        file_size = source_path.stat().st_size
        target_bytes = max(1, target_size_mb * 1024 * 1024)
        limit_bytes = max(target_bytes, upload_limit_mb * 1024 * 1024)
        duration = self._probe_duration(source_path)
        segment_count = max(2, math.ceil(file_size / target_bytes))
        segment_seconds = max(1.0, duration / segment_count)
        output_pattern = task_dir / f"{source_path.stem}.%02d{source_path.suffix.lower() or '.mp4'}"
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source_path),
            "-map",
            "0",
            "-c",
            "copy",
            "-f",
            "segment",
            "-segment_time",
            str(segment_seconds),
            "-reset_timestamps",
            "1",
            "-segment_format",
            "mp4",
            "-segment_format_options",
            "movflags=+faststart",
            str(output_pattern),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "ffmpeg split failed")

        segments = sorted(path for path in task_dir.glob(f"{source_path.stem}.*{source_path.suffix.lower() or '.mp4'}") if path.is_file())
        if len(segments) < 2:
            raise RuntimeError("video split produced fewer than two segments")
        oversize = next((path for path in segments if path.stat().st_size > limit_bytes), None)
        if oversize:
            raise RuntimeError(f"generated segment still exceeds upload limit: {oversize.name}")
        return SplitResult(segment_paths=segments)

    def cleanup(self, task_id: str) -> None:
        shutil.rmtree(self.root / task_id, ignore_errors=True)

    def cleanup_orphans(self, active_task_ids: set[str]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for item in self.root.iterdir():
            if not item.is_dir():
                continue
            if item.name not in active_task_ids:
                shutil.rmtree(item, ignore_errors=True)

    def _ensure_ffmpeg(self) -> None:
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise RuntimeError("ffmpeg or ffprobe is not installed")

    def _probe_duration(self, source_path: Path) -> float:
        command = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(source_path),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "ffprobe failed")
        payload = json.loads(result.stdout or "{}")
        duration = float(payload.get("format", {}).get("duration") or 0)
        if duration <= 0:
            raise RuntimeError("unable to determine video duration")
        return duration
