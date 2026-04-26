from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class LoginStage(str, Enum):
    LOGGED_OUT = "logged_out"
    CODE_REQUIRED = "code_required"
    PASSWORD_REQUIRED = "password_required"
    AUTHORIZED = "authorized"
    FAILED = "failed"


class UploadStatus(str, Enum):
    PENDING = "pending"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    FAILED = "failed"
    LOCKED = "locked"
    STABILIZING = "stabilizing"


class PostUploadAction(str, Enum):
    KEEP = "keep"
    DELETE = "delete"
    MOVE = "move"


class ApiSettings(BaseModel):
    api_id: int | None = None
    api_hash: str = ""
    phone_number: str = ""


class ProxyType(str, Enum):
    HTTP = "http"
    SOCKS5 = "socks5"


class UploadEngine(str, Enum):
    TELETHON = "telethon"
    BOT_API = "bot_api"


class BotDispatchMode(str, Enum):
    SINGLE = "single"
    CHANNEL_BOUND = "channel_bound"
    ROUND_ROBIN = "round_robin"


class ProxySettings(BaseModel):
    enabled: bool = False
    type: ProxyType = ProxyType.HTTP
    host: str = ""
    port: int = Field(default=1080, ge=1, le=65535)
    username: str = ""
    password: str = ""


class BotApiAccount(BaseModel):
    id: str
    name: str
    server_url: str = "https://api.telegram.org"
    bot_token: str = ""
    enabled: bool = True
    send_rate_limit_per_minute: int = Field(default=20, ge=1, le=600)
    send_rate_limit_per_channel_per_minute: int = Field(default=10, ge=1, le=600)
    send_jitter_min_ms: int = Field(default=300, ge=0, le=10000)
    send_jitter_max_ms: int = Field(default=1200, ge=0, le=10000)
    auto_slowdown_enabled: bool = True
    auto_slowdown_factor_percent: int = Field(default=50, ge=10, le=100)
    auto_slowdown_duration_seconds: int = Field(default=600, ge=30, le=86400)


class ChannelConfig(BaseModel):
    id: str
    name: str
    target: str
    enabled: bool = True
    bot_api_account_id: str = ""


class FolderConfig(BaseModel):
    id: str
    name: str
    path: str
    channel_id: str
    excluded_subdirs: list[str] = Field(default_factory=list)
    auto_upload: bool = True
    media_group_upload: bool = False
    media_group_filename_similarity: bool = False
    media_group_similarity_threshold: int = Field(default=80, ge=1, le=100)
    split_large_video_upload: bool = False
    upload_size_limit_mb: int = Field(default=2048, ge=100, le=4096)
    segment_target_size_mb: int = Field(default=1900, ge=100, le=4096)
    scan_interval_seconds: int = Field(default=30, ge=5, le=3600)
    min_stable_seconds: int = Field(default=30, ge=0, le=3600)
    post_upload_action: PostUploadAction = PostUploadAction.KEEP
    move_target_path: str = ""
    enabled: bool = True


class LoginStartRequest(BaseModel):
    api_id: int | None = None
    api_hash: str = ""
    phone_number: str = ""


class ProxySettingsPayload(BaseModel):
    enabled: bool = False
    type: ProxyType = ProxyType.HTTP
    host: str = ""
    port: int = Field(default=1080, ge=1, le=65535)
    username: str = ""
    password: str = ""


class UploadEnginePayload(BaseModel):
    engine: UploadEngine = UploadEngine.TELETHON


class BotApiAccountPayload(BaseModel):
    name: str = ""
    server_url: str = "https://api.telegram.org"
    bot_token: str = ""
    enabled: bool = True
    send_rate_limit_per_minute: int = Field(default=20, ge=1, le=600)
    send_rate_limit_per_channel_per_minute: int = Field(default=10, ge=1, le=600)
    send_jitter_min_ms: int = Field(default=300, ge=0, le=10000)
    send_jitter_max_ms: int = Field(default=1200, ge=0, le=10000)
    auto_slowdown_enabled: bool = True
    auto_slowdown_factor_percent: int = Field(default=50, ge=10, le=100)
    auto_slowdown_duration_seconds: int = Field(default=600, ge=30, le=86400)


class BotDispatchSettingsPayload(BaseModel):
    mode: BotDispatchMode = BotDispatchMode.SINGLE
    default_bot_api_account_id: str = ""
    smart_queue_scheduling_enabled: bool = False


class AppSettings(BaseModel):
    api: ApiSettings = Field(default_factory=ApiSettings)
    proxy: ProxySettings = Field(default_factory=ProxySettings)
    upload_engine: UploadEngine = UploadEngine.TELETHON
    bot_api_accounts: list[BotApiAccount] = Field(default_factory=list)
    bot_dispatch_mode: BotDispatchMode = BotDispatchMode.SINGLE
    default_bot_api_account_id: str = ""
    smart_queue_scheduling_enabled: bool = False
    channels: list[ChannelConfig] = Field(default_factory=list)
    folders: list[FolderConfig] = Field(default_factory=list)
    access_password_hash: str = ""


class LoginCodeRequest(BaseModel):
    code: str


class LoginPasswordRequest(BaseModel):
    password: str


class AccessPasswordRequest(BaseModel):
    password: str


class ChannelBotSetupRequest(BaseModel):
    bot_api_account_id: str = ""
    admin_title: str = "Uploader Bot"


class ChannelPayload(BaseModel):
    name: str
    target: str
    enabled: bool = True
    bot_api_account_id: str = ""


class FolderPayload(BaseModel):
    name: str
    path: str
    channel_id: str
    excluded_subdirs: list[str] = Field(default_factory=list)
    auto_upload: bool = True
    media_group_upload: bool = False
    media_group_filename_similarity: bool = False
    media_group_similarity_threshold: int = Field(default=80, ge=1, le=100)
    split_large_video_upload: bool = False
    upload_size_limit_mb: int = Field(default=2048, ge=100, le=4096)
    segment_target_size_mb: int = Field(default=1900, ge=100, le=4096)
    scan_interval_seconds: int = Field(default=30, ge=5, le=3600)
    min_stable_seconds: int = Field(default=30, ge=0, le=3600)
    post_upload_action: PostUploadAction = PostUploadAction.KEEP
    move_target_path: str = ""
    enabled: bool = True


class FileEntry(BaseModel):
    relative_path: str
    absolute_path: str
    file_type: Literal["video", "image", "music", "document", "other"]
    size: int
    modified_at: float
    status: UploadStatus


class FileTreeNode(BaseModel):
    path: str
    name: str
    count: int
    depth: int
    parent: str
    children: list[str] = Field(default_factory=list)


class FileListStats(BaseModel):
    total: int = 0
    pending: int = 0
    uploaded: int = 0
    locked: int = 0
    stabilizing: int = 0


class FileListPagination(BaseModel):
    page: int = 1
    page_size: int = 10
    total_pages: int = 1
    total_items: int = 0
    start: int = 0
    end: int = 0


class FileListResponse(BaseModel):
    items: list[FileEntry] = Field(default_factory=list)
    tree: list[FileTreeNode] = Field(default_factory=list)
    stats: FileListStats = Field(default_factory=FileListStats)
    pagination: FileListPagination = Field(default_factory=FileListPagination)
    total_all: int = 0


class UploadBatchItem(BaseModel):
    relative_path: str
    status: UploadStatus = UploadStatus.PENDING
    progress: float = 0.0
    error_message: str = ""


class UploadTask(BaseModel):
    id: str
    folder_id: str
    channel_id: str
    bot_api_account_id: str = ""
    uploader_engine: str = ""
    relative_path: str
    absolute_path: str
    source_relative_path: str = ""
    source_absolute_path: str = ""
    task_kind: str = "single"
    batch_paths: list[str] = Field(default_factory=list)
    batch_items: list[UploadBatchItem] = Field(default_factory=list)
    completed_count: int = 0
    status: UploadStatus
    progress: float = 0.0
    error_message: str = ""
    caption: str = ""
    group_debug: str = ""
    created_at: float
    updated_at: float


class ManualUploadRequest(BaseModel):
    folder_id: str
    relative_paths: list[str]


class UploadRetryBatchRequest(BaseModel):
    task_ids: list[str]


class UploadDeleteBatchRequest(BaseModel):
    task_ids: list[str]


class UploadStats(BaseModel):
    total: int = 0
    pending: int = 0
    uploading: int = 0
    uploaded: int = 0
    failed: int = 0
    locked: int = 0
    stabilizing: int = 0
    upload_speed_bytes: float = 0.0


class UploadListPagination(BaseModel):
    page: int = 1
    page_size: int = 10
    total_pages: int = 1
    total_items: int = 0
    start: int = 0
    end: int = 0


class UploadListResponse(BaseModel):
    items: list[UploadTask] = Field(default_factory=list)
    pagination: UploadListPagination = Field(default_factory=UploadListPagination)
    total_all: int = 0
