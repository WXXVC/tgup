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


class PostUploadAction(str, Enum):
    KEEP = "keep"
    DELETE = "delete"
    MOVE = "move"


class ApiSettings(BaseModel):
    api_id: int | None = None
    api_hash: str = ""
    phone_number: str = ""


class ChannelConfig(BaseModel):
    id: str
    name: str
    target: str
    enabled: bool = True


class FolderConfig(BaseModel):
    id: str
    name: str
    path: str
    channel_id: str
    auto_upload: bool = True
    scan_interval_seconds: int = Field(default=30, ge=5, le=3600)
    post_upload_action: PostUploadAction = PostUploadAction.KEEP
    move_target_path: str = ""
    enabled: bool = True


class AppSettings(BaseModel):
    api: ApiSettings = Field(default_factory=ApiSettings)
    channels: list[ChannelConfig] = Field(default_factory=list)
    folders: list[FolderConfig] = Field(default_factory=list)
    access_password_hash: str = ""


class LoginStartRequest(BaseModel):
    api_id: int
    api_hash: str
    phone_number: str


class LoginCodeRequest(BaseModel):
    code: str


class LoginPasswordRequest(BaseModel):
    password: str


class AccessPasswordRequest(BaseModel):
    password: str


class ChannelPayload(BaseModel):
    name: str
    target: str
    enabled: bool = True


class FolderPayload(BaseModel):
    name: str
    path: str
    channel_id: str
    auto_upload: bool = True
    scan_interval_seconds: int = Field(default=30, ge=5, le=3600)
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


class UploadTask(BaseModel):
    id: str
    folder_id: str
    channel_id: str
    relative_path: str
    absolute_path: str
    batch_paths: list[str] = Field(default_factory=list)
    status: UploadStatus
    progress: float = 0.0
    error_message: str = ""
    caption: str = ""
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
