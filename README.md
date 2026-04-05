# TG Upload Manager

Telegram file upload manager built with FastAPI, Telethon, SQLite and a lightweight web UI.

## Features

- Telegram login flow with code and 2FA password support
- Session persistence based on Telethon session files
- Channel management and monitored folder management
- Folder scan, file browser, manual upload, auto upload and upload task list
- Cross-platform path handling for Windows local run and Linux Docker deployment
- Post-upload keep, delete and move strategies

## Local Run

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload
```

Open `http://127.0.0.1:8000`.

## Docker Run

```bash
docker compose up --build
```

Default mapping:

- `./data` persists app settings, sqlite db and Telethon sessions
- `./sample-media` is mounted to `/media/inbox` as an example upload root

You can mount your own media directories in `docker-compose.yml`.

## Project Layout

- `backend/app/main.py`: FastAPI entry and API routes
- `backend/app/telegram_client.py`: Telethon login and upload wrapper
- `backend/app/upload_manager.py`: scan queue and upload orchestration
- `frontend/`: lightweight browser UI
