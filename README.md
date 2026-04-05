# TG Upload Manager

一个基于 FastAPI、Telethon、SQLite 和轻量 Web UI 的 Telegram 文件上传管理器。

它主要用于本地或服务器场景下，对指定目录进行扫描、浏览、手动上传与自动上传，并在网页端集中查看上传任务状态。

## 功能特性

- 支持 Telegram 登录流程，包括验证码和二次验证密码
- 基于 Telethon session 文件持久化登录状态
- 支持频道管理与监控目录管理
- 支持目录扫描、文件浏览、手动上传、自动上传与任务状态追踪
- 支持上传后保留、删除、移动三种处理策略
- 兼容 Windows 本地运行与 Linux Docker 部署
- 提供前端任务筛选、状态统计、批量重试、批量清理与文件预览能力

## 本地运行

### Windows 一键启动

如果你是在 Windows 上本地运行，直接双击根目录下的 `start-local.bat` 即可。

这个脚本会自动完成：

- 检测 Python
- 创建 `.venv` 虚拟环境
- 安装 / 更新依赖
- 创建 `data` 目录
- 启动本地服务并打开浏览器
- 在异常情况下给出提示，避免直接闪退

默认访问地址：

```text
http://127.0.0.1:8000
```

## Docker 部署

### 一键启动示例

```bash
mkdir -p ./data ./sample-media
docker run -d \
  --name tg-upload-manager \
  -p 8000:8000 \
  -v $PWD/data:/app/data \
  -v $PWD/sample-media:/media/inbox \
  ghcr.io/wxxvc/tgup:latest
```

启动后访问：

```text
http://服务器IP:8000
```

### 参数说明

- `-d`
  后台运行容器。
- `--name tg-upload-manager`
  指定容器名称，后续查看日志或重启时更方便。
- `-p 8000:8000`
  将宿主机 `8000` 端口映射到容器内 `8000` 端口。
- `-v $PWD/data:/app/data`
  持久化应用数据目录，包含配置、数据库和 Telegram 会话文件。
- `-v $PWD/sample-media:/media/inbox`
  挂载宿主机媒体目录，容器会从这里扫描和上传文件。

## 项目结构

- `backend/app/main.py`：FastAPI 入口与 API 路由
- `backend/app/telegram_client.py`：Telegram 登录与上传封装
- `backend/app/upload_manager.py`：扫描队列与上传任务调度
- `backend/app/upload_repo.py`：上传任务数据存取
- `frontend/`：前端页面与静态资源
- `data/`：本地运行时数据目录，不建议提交到仓库
