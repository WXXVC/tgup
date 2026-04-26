from __future__ import annotations

from contextlib import asynccontextmanager
from functools import lru_cache
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .db import init_db
from .models import (
    AccessPasswordRequest,
    BotApiAccountPayload,
    BotDispatchSettingsPayload,
    ChannelBotSetupRequest,
    ChannelPayload,
    FileListResponse,
    FolderPayload,
    LoginCodeRequest,
    LoginStage,
    UploadDeleteBatchRequest,
    LoginPasswordRequest,
    LoginStartRequest,
    ManualUploadRequest,
    UploadListResponse,
    ProxySettingsPayload,
    UploadEngine,
    UploadEnginePayload,
    UploadRetryBatchRequest,
    UploadStatus,
)
from .bot_api_pool import BotApiClientPool
from .config import APP_LOG_PATH
from .scanner import FolderScanner
from .settings_service import SettingsService
from .storage import SettingsStore
from .telegram_client import TelegramSessionManager
from .upload_manager import UploadManager
from .upload_repo import UploadRepository


store = SettingsStore()
settings_service = SettingsService(store)
upload_repo = UploadRepository()
telegram = TelegramSessionManager()
bot_api_pool = BotApiClientPool()
scanner = FolderScanner(upload_repo)
upload_manager = UploadManager(
    settings_service, upload_repo, scanner, telegram, bot_api_pool
)
ACCESS_COOKIE_NAME = "tgup_access"
APP_DISPLAY_VERSION = "0.1.0"


def configure_logging() -> None:
    root_logger = logging.getLogger()
    if any(
        isinstance(handler, RotatingFileHandler)
        and Path(getattr(handler, "baseFilename", "")) == APP_LOG_PATH
        for handler in root_logger.handlers
    ):
        return
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    file_handler = RotatingFileHandler(
        APP_LOG_PATH,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    if not any(isinstance(handler, logging.StreamHandler) for handler in root_logger.handlers[:-1]):
        root_logger.addHandler(stream_handler)


configure_logging()
logger = logging.getLogger(__name__)


async def restore_telegram_session_or_raise() -> None:
    await telegram.restore(
        settings_service.settings.api.api_id,
        settings_service.settings.api.api_hash,
        settings_service.resolved_proxy_settings().model_dump(mode="json"),
    )
    if telegram.stage != LoginStage.AUTHORIZED:
        detail = telegram.last_error or "telethon session is not authorized"
        raise HTTPException(status_code=400, detail=detail)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if settings_service.settings.api.api_id and settings_service.settings.api.api_hash:
        await telegram.restore(
            settings_service.settings.api.api_id,
            settings_service.settings.api.api_hash,
            settings_service.resolved_proxy_settings().model_dump(mode="json"),
        )
        if telegram.stage != LoginStage.AUTHORIZED:
            logger.warning(
                "Telethon session restore on startup did not authorize: %s",
                telegram.last_error,
            )
    bot_api_pool.configure(
        settings_service.resolved_bot_api_accounts(),
        settings_service.resolved_proxy_settings().model_dump(mode="json"),
    )
    await upload_manager.start()
    yield
    await upload_manager.stop()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
static_dir = frontend_dir / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@lru_cache(maxsize=1)
def frontend_template() -> str:
    return (frontend_dir / "index.html").read_text(encoding="utf-8")


def frontend_asset_version() -> str:
    candidates = [frontend_dir / "index.html"]
    if static_dir.exists():
        candidates.extend(path for path in static_dir.rglob("*") if path.is_file())
    latest_mtime_ns = max(
        path.stat().st_mtime_ns for path in candidates if path.exists()
    )
    return str(latest_mtime_ns)


def is_public_path(path: str) -> bool:
    return path.startswith("/static/") or path in {
        "/",
        "/login",
        "/dir",
        "/setting",
        "/upload",
        "/api/access/status",
        "/api/access/login",
    }


@app.middleware("http")
async def access_password_guard(request: Request, call_next):
    if is_public_path(request.url.path):
        return await call_next(request)
    if settings_service.is_access_token_valid(request.cookies.get(ACCESS_COOKIE_NAME)):
        return await call_next(request)
    return JSONResponse(status_code=401, content={"detail": "access password required"})


@app.get("/")
@app.get("/login")
@app.get("/dir")
@app.get("/setting")
@app.get("/upload")
async def index():
    if not (frontend_dir / "index.html").exists():
        raise HTTPException(status_code=404, detail="frontend not found")
    content = (
        frontend_template()
        .replace("__APP_ASSET_VERSION__", frontend_asset_version())
        .replace("__APP_DISPLAY_VERSION__", APP_DISPLAY_VERSION)
    )
    return HTMLResponse(
        content=content,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/api/settings")
async def get_settings():
    payload = settings_service.public_settings()
    payload["bot_api_runtime_status"] = bot_api_pool.status()
    return {
        "settings": payload,
        "login": upload_manager.current_engine_status(),
    }


def build_bot_api_settings_payload() -> dict:
    settings_payload = settings_service.public_settings()
    return {
        "accounts": settings_payload.get("bot_api_accounts", []),
        "bot_api_runtime_status": bot_api_pool.status(),
        "bot_dispatch_mode": settings_payload.get("bot_dispatch_mode", "single"),
        "default_bot_api_account_id": settings_payload.get("default_bot_api_account_id", ""),
        "smart_queue_scheduling_enabled": settings_payload.get("smart_queue_scheduling_enabled", False),
    }


@app.get("/api/access/status")
async def access_status(request: Request):
    return {
        "enabled": settings_service.has_access_password(),
        "authorized": settings_service.is_access_token_valid(
            request.cookies.get(ACCESS_COOKIE_NAME)
        ),
    }


@app.post("/api/access/login")
async def access_login(payload: AccessPasswordRequest):
    if (
        settings_service.has_access_password()
        and not settings_service.verify_access_password(payload.password)
    ):
        raise HTTPException(status_code=401, detail="invalid access password")
    response = JSONResponse({"ok": True})
    if settings_service.has_access_password():
        response.set_cookie(
            ACCESS_COOKIE_NAME,
            settings_service.build_access_token(),
            httponly=True,
            samesite="lax",
            path="/",
        )
    return response


@app.post("/api/access/password")
async def save_access_password(payload: AccessPasswordRequest):
    try:
        settings_service.set_access_password(payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response = JSONResponse({"ok": True})
    response.set_cookie(
        ACCESS_COOKIE_NAME,
        settings_service.build_access_token(),
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


@app.delete("/api/access/password")
async def clear_access_password():
    settings_service.clear_access_password()
    response = JSONResponse({"ok": True})
    response.delete_cookie(ACCESS_COOKIE_NAME, path="/")
    return response


@app.post("/api/settings/api")
async def save_api_settings(payload: LoginStartRequest):
    api_id, api_hash, phone_number = settings_service.resolve_api_payload(payload)
    settings_service.update_api(api_id, api_hash, phone_number)
    await upload_manager.apply_runtime_settings()
    return {"ok": True}


@app.post("/api/settings/proxy")
async def save_proxy_settings(payload: ProxySettingsPayload):
    try:
        settings_service.update_proxy(payload)
        if settings_service.settings.api.api_id and settings_service.settings.api.api_hash:
            await telegram.restore(
                settings_service.settings.api.api_id,
                settings_service.settings.api.api_hash,
                settings_service.resolved_proxy_settings().model_dump(mode="json"),
            )
        bot_api_pool.configure(
            settings_service.resolved_bot_api_accounts(),
            settings_service.resolved_proxy_settings().model_dump(mode="json"),
        )
        await upload_manager.apply_runtime_settings()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/settings/engine")
async def save_upload_engine(payload: UploadEnginePayload):
    try:
        settings_service.update_upload_engine(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "mode": "hybrid"}


@app.get("/api/settings/bot-api/accounts")
async def list_bot_api_accounts():
    return settings_service.public_settings()["bot_api_accounts"]


@app.post("/api/settings/bot-api/accounts")
async def create_bot_api_account(payload: BotApiAccountPayload):
    try:
        account = settings_service.add_bot_api_account(payload)
        bot_api_pool.configure(
            settings_service.resolved_bot_api_accounts(),
            settings_service.resolved_proxy_settings().model_dump(mode="json"),
        )
        await upload_manager.apply_runtime_settings()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "account": account,
        **build_bot_api_settings_payload(),
    }


@app.put("/api/settings/bot-api/accounts/{account_id}")
async def update_bot_api_account(account_id: str, payload: BotApiAccountPayload):
    try:
        account = settings_service.update_bot_api_account(account_id, payload)
        if not account:
            raise HTTPException(status_code=404, detail="bot api account not found")
        bot_api_pool.configure(
            settings_service.resolved_bot_api_accounts(),
            settings_service.resolved_proxy_settings().model_dump(mode="json"),
        )
        await upload_manager.apply_runtime_settings()
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "account": account,
        **build_bot_api_settings_payload(),
    }


@app.delete("/api/settings/bot-api/accounts/{account_id}")
async def delete_bot_api_account(account_id: str):
    try:
        deleted = settings_service.delete_bot_api_account(account_id)
        bot_api_pool.configure(
            settings_service.resolved_bot_api_accounts(),
            settings_service.resolved_proxy_settings().model_dump(mode="json"),
        )
        await upload_manager.apply_runtime_settings()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="bot api account not found")
    return {"ok": True, **build_bot_api_settings_payload()}


@app.post("/api/settings/bot-api/accounts/{account_id}/test")
async def test_bot_api_account(account_id: str):
    try:
        bot_api_pool.configure(
            settings_service.resolved_bot_api_accounts(),
            settings_service.resolved_proxy_settings().model_dump(mode="json"),
        )
        result = await bot_api_pool.test_connection(account_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@app.post("/api/settings/bot-api/dispatch")
async def save_bot_dispatch_settings(payload: BotDispatchSettingsPayload):
    try:
        settings_service.update_bot_dispatch_settings(payload)
        await upload_manager.apply_runtime_settings()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/settings/folders/normalize-limits")
async def normalize_folder_limits_for_engine():
    result = settings_service.normalize_folder_limits_for_current_engine()
    return {"ok": True, **result}


@app.post("/api/auth/start")
async def auth_start(payload: LoginStartRequest):
    api_id, api_hash, phone_number = settings_service.resolve_api_payload(payload)
    stage = await telegram.start_login(
        api_id,
        api_hash,
        phone_number,
        settings_service.resolved_proxy_settings().model_dump(mode="json"),
    )
    settings_service.update_api(api_id, api_hash, phone_number)
    await upload_manager.apply_runtime_settings()
    return {"stage": stage.value, "last_error": telegram.last_error}


@app.post("/api/auth/code")
async def auth_code(payload: LoginCodeRequest):
    stage = await telegram.verify_code(payload.code)
    return {"stage": stage.value, "last_error": telegram.last_error}


@app.post("/api/auth/password")
async def auth_password(payload: LoginPasswordRequest):
    stage = await telegram.verify_password(payload.password)
    return {"stage": stage.value, "last_error": telegram.last_error}


@app.post("/api/channels")
async def create_channel(payload: ChannelPayload):
    return settings_service.add_channel(payload)


@app.put("/api/channels/{channel_id}")
async def update_channel(channel_id: str, payload: ChannelPayload):
    channel = settings_service.update_channel(channel_id, payload)
    if not channel:
        raise HTTPException(status_code=404, detail="channel not found")
    return channel


@app.post("/api/channels/{channel_id}/setup-bot")
async def setup_channel_bot(channel_id: str, payload: ChannelBotSetupRequest):
    channel = next(
        (item for item in settings_service.settings.channels if item.id == channel_id),
        None,
    )
    if not channel:
        raise HTTPException(status_code=404, detail="channel not found")
    if not settings_service.settings.api.api_id or not settings_service.settings.api.api_hash:
        raise HTTPException(status_code=400, detail="telethon api credentials are incomplete")
    await restore_telegram_session_or_raise()
    account = settings_service.resolve_bot_api_account_for_channel(
        channel_id, payload.bot_api_account_id
    )
    if not account:
        raise HTTPException(status_code=400, detail="no enabled bot api account available for this channel")
    try:
        bot_api_pool.configure(
            settings_service.resolved_bot_api_accounts(),
            settings_service.resolved_proxy_settings().model_dump(mode="json"),
        )
        bot_info = await bot_api_pool.test_connection(account.id)
        username = str(bot_info.get("username") or "").strip()
        if not username:
            raise ValueError("bot username is unavailable")
        result = await telegram.setup_bot_for_channel(
            channel.target,
            f"@{username}",
            payload.admin_title,
        )
    except Exception as exc:
        logger.exception(
            "setup_channel_bot failed (channel_id=%s, channel_target=%s, account_id=%s)",
            channel.id,
            channel.target,
            account.id,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        **result,
        "channel_id": channel.id,
        "channel_name": channel.name,
        "bot_api_account_id": account.id,
        "bot_name": account.name,
    }


@app.post("/api/channels/{channel_id}/setup-all-bots")
async def setup_channel_all_bots(channel_id: str, payload: ChannelBotSetupRequest):
    channel = next(
        (item for item in settings_service.settings.channels if item.id == channel_id),
        None,
    )
    if not channel:
        raise HTTPException(status_code=404, detail="channel not found")
    if not settings_service.settings.api.api_id or not settings_service.settings.api.api_hash:
        raise HTTPException(status_code=400, detail="telethon api credentials are incomplete")
    accounts = [item for item in settings_service.resolved_bot_api_accounts() if item.enabled]
    if not accounts:
        raise HTTPException(status_code=400, detail="no enabled bot api account available")
    await restore_telegram_session_or_raise()
    bot_api_pool.configure(
        settings_service.resolved_bot_api_accounts(),
        settings_service.resolved_proxy_settings().model_dump(mode="json"),
    )

    results = []
    for account in accounts:
        try:
            bot_info = await bot_api_pool.test_connection(account.id)
            username = str(bot_info.get("username") or "").strip()
            if not username:
                raise ValueError("bot username is unavailable")
            result = await telegram.setup_bot_for_channel(
                channel.target,
                f"@{username}",
                payload.admin_title,
            )
            results.append({
                "ok": True,
                "bot_api_account_id": account.id,
                "bot_name": account.name,
                "bot_username": username,
                **result,
            })
        except Exception as exc:
            logger.exception(
                "setup_channel_all_bots item failed (channel_id=%s, channel_target=%s, account_id=%s)",
                channel.id,
                channel.target,
                account.id,
            )
            results.append({
                "ok": False,
                "bot_api_account_id": account.id,
                "bot_name": account.name,
                "error": str(exc),
            })

    success_count = sum(1 for item in results if item.get("ok"))
    return {
        "ok": success_count > 0,
        "channel_id": channel.id,
        "channel_name": channel.name,
        "total": len(accounts),
        "success_count": success_count,
        "results": results,
    }


@app.get("/api/auth/telethon/check")
async def telethon_connectivity_check():
    if not settings_service.settings.api.api_id or not settings_service.settings.api.api_hash:
        raise HTTPException(status_code=400, detail="telethon api credentials are incomplete")
    await telegram.restore(
        settings_service.settings.api.api_id,
        settings_service.settings.api.api_hash,
        settings_service.resolved_proxy_settings().model_dump(mode="json"),
    )
    return {
        "ok": telegram.stage == LoginStage.AUTHORIZED,
        "stage": telegram.stage.value,
        "last_error": telegram.last_error,
        "log_path": str(APP_LOG_PATH),
    }


@app.get("/api/auth/telethon/self-check")
async def telethon_session_self_check():
    if not settings_service.settings.api.api_id or not settings_service.settings.api.api_hash:
        raise HTTPException(status_code=400, detail="telethon api credentials are incomplete")
    await telegram.restore(
        settings_service.settings.api.api_id,
        settings_service.settings.api.api_hash,
        settings_service.resolved_proxy_settings().model_dump(mode="json"),
    )
    result = await telegram.self_check()
    result["log_path"] = str(APP_LOG_PATH)
    return result


@app.delete("/api/channels/{channel_id}")
async def delete_channel(channel_id: str):
    try:
        deleted = settings_service.delete_channel(channel_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="channel not found")
    return {"ok": True}


@app.post("/api/folders")
async def create_folder(payload: FolderPayload):
    try:
        return settings_service.add_folder(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/folders/{folder_id}")
async def update_folder(folder_id: str, payload: FolderPayload):
    try:
        folder = settings_service.update_folder(folder_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not folder:
        raise HTTPException(status_code=404, detail="folder not found")
    return folder


@app.delete("/api/folders/{folder_id}")
async def delete_folder(folder_id: str):
    if not settings_service.delete_folder(folder_id):
        raise HTTPException(status_code=404, detail="folder not found")
    return {"ok": True}


@app.get("/api/folders/{folder_id}/files")
async def folder_files(
    folder_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    subdir: str = Query(default=""),
    scope: str = Query(default="direct"),
    file_type: str = Query(default="all"),
    status: str = Query(default="all"),
    search: str = Query(default=""),
):
    folder = next(
        (item for item in settings_service.settings.folders if item.id == folder_id),
        None,
    )
    if not folder:
        raise HTTPException(status_code=404, detail="folder not found")
    items, stats, pagination, total_all = scanner.list_files_paginated(
        folder.id,
        folder.path,
        min_stable_seconds=folder.min_stable_seconds,
        subdir=subdir,
        scope=scope,
        file_type=file_type,
        status=status,
        search=search,
        page=page,
        page_size=page_size,
    )
    return FileListResponse(
        items=items,
        tree=scanner.build_directory_tree_for_root(folder.path),
        stats=stats,
        pagination=pagination,
        total_all=total_all,
    )


@app.post("/api/folders/{folder_id}/scan")
async def scan_folder(folder_id: str):
    scanner.invalidate_file_list_cache(folder_id)
    await upload_manager.trigger_scan(folder_id)
    return {"ok": True}


@app.post("/api/uploads/manual")
async def manual_upload(payload: ManualUploadRequest):
    try:
        await upload_manager.enqueue_manual(payload.folder_id, payload.relative_paths)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.get("/api/uploads")
async def uploads(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    folder_id: str = Query(default="all"),
    status: str = Query(default="all"),
    error_category: str = Query(default="all"),
    scheduling: str = Query(default="all"),
    search: str = Query(default=""),
    sort: str = Query(default="updated_desc"),
):
    return upload_repo.list_tasks_paginated(
        page=page,
        page_size=page_size,
        folder_id=folder_id,
        status=status,
        error_category=error_category,
        scheduling=scheduling,
        search=search,
        sort=sort,
    )


@app.get("/api/uploads/stats")
async def upload_stats():
    stats = upload_repo.stats()
    stats.upload_speed_bytes = upload_manager.current_upload_speed_bytes
    return stats


@app.post("/api/uploads/{task_id}/retry")
async def retry_upload(task_id: str):
    try:
        task = await upload_manager.retry_task(task_id)
    except ValueError as exc:
        message = str(exc)
        if message == "task not found":
            raise HTTPException(status_code=404, detail=message) from exc
        raise HTTPException(status_code=400, detail=message) from exc
    return task


@app.post("/api/uploads/retry-batch")
async def retry_upload_batch(payload: UploadRetryBatchRequest):
    try:
        return await upload_manager.retry_tasks(payload.task_ids)
    except ValueError as exc:
        message = str(exc)
        if message == "task not found":
            raise HTTPException(status_code=404, detail=message) from exc
        raise HTTPException(status_code=400, detail=message) from exc


@app.delete("/api/uploads/delete-batch")
async def delete_upload_batch(payload: UploadDeleteBatchRequest):
    task_ids = []
    for task_id in payload.task_ids:
        task = upload_repo.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
        task_ids.append(task_id)
    deleted = await upload_manager.delete_tasks(task_ids)
    return {"ok": True, "deleted": deleted}


@app.delete("/api/uploads/clear")
async def clear_uploads(scope: str = Query("finished")):
    if scope == "failed":
        count = upload_repo.clear_tasks([UploadStatus.FAILED, UploadStatus.LOCKED])
    elif scope == "finished":
        count = upload_repo.clear_tasks(
            [UploadStatus.UPLOADED, UploadStatus.FAILED, UploadStatus.LOCKED]
        )
    elif scope == "all":
        stats = upload_repo.stats()
        if stats.pending or stats.uploading:
            raise HTTPException(
                status_code=400,
                detail="cannot clear all while queued or running tasks exist",
            )
        count = upload_repo.clear_tasks(
            [
                UploadStatus.UPLOADED,
                UploadStatus.FAILED,
                UploadStatus.LOCKED,
            ]
        )
    else:
        raise HTTPException(status_code=400, detail="invalid clear scope")
    return {"ok": True, "deleted": count}


@app.get("/api/files/preview")
async def preview_file(folder_id: str = Query(...), relative_path: str = Query(...)):
    folder = next(
        (item for item in settings_service.settings.folders if item.id == folder_id),
        None,
    )
    if not folder:
        raise HTTPException(status_code=404, detail="folder not found")
    root = Path(folder.path).resolve()
    file_path = (root / relative_path).resolve()
    try:
        file_path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid relative_path") from exc
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(file_path)
