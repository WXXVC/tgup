from __future__ import annotations
import mimetypes
import json
import re
import shutil
import subprocess
import os
from pathlib import Path

from .models import UploadStatus


VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
ALBUM_SAFE_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}
ALBUM_SAFE_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
STREAMABLE_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
PREVIEWABLE_AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".ogg", ".flac", ".wav"}
ALBUM_MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024
MUSIC_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"}
DOCUMENT_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".txt",
    ".md",
    ".zip",
    ".rar",
    ".7z",
}


def classify_file(path: Path) -> str:
    extension = path.suffix.lower()
    if extension in VIDEO_EXTENSIONS:
        return "video"
    if extension in IMAGE_EXTENSIONS:
        return "image"
    if extension in MUSIC_EXTENSIONS:
        return "music"
    if extension in DOCUMENT_EXTENSIONS:
        return "document"
    return "other"


def is_album_eligible(path: Path, max_file_size_bytes: int = ALBUM_MAX_FILE_SIZE) -> bool:
    extension = path.suffix.lower()
    if extension not in ALBUM_SAFE_VIDEO_EXTENSIONS | ALBUM_SAFE_IMAGE_EXTENSIONS:
        return False
    try:
        return path.stat().st_size < max_file_size_bytes
    except OSError:
        return False


def is_streamable_video(path: Path) -> bool:
    return path.suffix.lower() in STREAMABLE_VIDEO_EXTENSIONS


def probe_video_stream(path: Path) -> dict | None:
    if not shutil.which("ffprobe"):
        return None
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=format_name:stream=codec_type,codec_name",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None
    video_stream = next(
        (
            stream
            for stream in payload.get("streams", [])
            if stream.get("codec_type") == "video"
        ),
        None,
    )
    if not video_stream:
        return None
    return {
        "format_name": str(payload.get("format", {}).get("format_name") or "").lower(),
        "codec_name": str(video_stream.get("codec_name") or "").lower(),
    }


def is_telegram_previewable_video(path: Path) -> bool:
    if path.suffix.lower() != ".mp4":
        return False
    stream = probe_video_stream(path)
    if not stream:
        return False
    codec_name = stream.get("codec_name", "")
    return codec_name in {"h264", "mpeg4"}


def is_previewable_audio(path: Path) -> bool:
    return path.suffix.lower() in PREVIEWABLE_AUDIO_EXTENSIONS


def guess_media_mime_type(path: Path) -> str | None:
    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type:
        return mime_type
    if is_streamable_video(path):
        return "video/mp4"
    if path.suffix.lower() == ".m4a":
        return "audio/mp4"
    if path.suffix.lower() == ".flac":
        return "audio/flac"
    return None


def build_caption(root: Path, file_path: Path) -> str:
    relative = file_path.relative_to(root)
    tags = []
    for part in relative.parts[:-1]:
        tag = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff]+", "_", part).strip("_")
        if tag:
            tags.append(f"#{tag}")
    name = file_path.stem
    return " ".join([*tags, name]).strip()


def file_is_locked(path: Path) -> bool:
    try:
        if os.name == "nt":
            with open(path, "rb+"):
                return False
        else:
            with open(path, "rb"):
                return False
    except OSError:
        return True


def derive_status(is_uploaded: bool, unavailable_reason: str = "") -> UploadStatus:
    if unavailable_reason == "locked":
        return UploadStatus.LOCKED
    if unavailable_reason == "stabilizing":
        return UploadStatus.STABILIZING
    if is_uploaded:
        return UploadStatus.UPLOADED
    return UploadStatus.PENDING
