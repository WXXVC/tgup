from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .db import init_db
from .models import (
    AccessPasswordRequest,
    ChannelPayload,
    FolderPayload,
    LoginCodeRequest,
    UploadDeleteBatchRequest,
    LoginPasswordRequest,
    LoginStartRequest,
    ManualUploadRequest,
    UploadRetryBatchRequest,
    UploadStatus,
)
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
scanner = FolderScanner(upload_repo)
upload_manager = UploadManager(settings_service, upload_repo, scanner, telegram)
ACCESS_COOKIE_NAME = "tgup_access"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    await telegram.restore(settings_service.settings.api.api_id, settings_service.settings.api.api_hash)
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
    return FileResponse(frontend_dir / "index.html")


@app.get("/api/settings")
async def get_settings():
    return {
        "settings": settings_service.public_settings(),
        "login": telegram.status(),
    }


@app.get("/api/access/status")
async def access_status(request: Request):
    return {
        "enabled": settings_service.has_access_password(),
        "authorized": settings_service.is_access_token_valid(request.cookies.get(ACCESS_COOKIE_NAME)),
    }


@app.post("/api/access/login")
async def access_login(payload: AccessPasswordRequest):
    if settings_service.has_access_password() and not settings_service.verify_access_password(payload.password):
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
    return {"ok": True}


@app.post("/api/auth/start")
async def auth_start(payload: LoginStartRequest):
    api_id, api_hash, phone_number = settings_service.resolve_api_payload(payload)
    stage = await telegram.start_login(api_id, api_hash, phone_number)
    settings_service.update_api(api_id, api_hash, phone_number)
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
async def folder_files(folder_id: str):
    folder = next((item for item in settings_service.settings.folders if item.id == folder_id), None)
    if not folder:
        raise HTTPException(status_code=404, detail="folder not found")
    return scanner.list_files(folder.id, folder.path)


@app.post("/api/folders/{folder_id}/scan")
async def scan_folder(folder_id: str):
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
async def uploads():
    return upload_repo.list_tasks()


@app.get("/api/uploads/stats")
async def upload_stats():
    return upload_repo.stats()


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
        if task.status in {UploadStatus.PENDING, UploadStatus.UPLOADING}:
            raise HTTPException(status_code=400, detail="cannot delete queued or running tasks")
        task_ids.append(task_id)
    deleted = upload_repo.delete_tasks(task_ids)
    return {"ok": True, "deleted": deleted}


@app.delete("/api/uploads/clear")
async def clear_uploads(scope: str = Query("finished")):
    if scope == "failed":
        count = upload_repo.clear_tasks([UploadStatus.FAILED, UploadStatus.LOCKED])
    elif scope == "finished":
        count = upload_repo.clear_tasks([UploadStatus.UPLOADED, UploadStatus.FAILED, UploadStatus.LOCKED])
    elif scope == "all":
        stats = upload_repo.stats()
        if stats.pending or stats.uploading:
            raise HTTPException(status_code=400, detail="cannot clear all while queued or running tasks exist")
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
    folder = next((item for item in settings_service.settings.folders if item.id == folder_id), None)
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
