@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

cd /d "%~dp0"
title TG Upload Manager - Local Start

echo.
echo ========================================
echo   TG Upload Manager 本地一键启动
echo ========================================
echo.

set "PYTHON_CMD="
set "APP_URL=http://127.0.0.1:8000"
set "VENV_PYTHON=.venv\Scripts\python.exe"

where py >nul 2>nul
if %errorlevel%==0 (
  set "PYTHON_CMD=py"
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (
    set "PYTHON_CMD=python"
  )
)

if not defined PYTHON_CMD (
  echo [错误] 未检测到 Python。
  echo 请先安装 Python 3.11+，并确保已加入 PATH。
  echo.
  goto :fail
)

if exist ".venv" if not exist "%VENV_PYTHON%" (
  echo [提示] 检测到损坏的虚拟环境，正在重新创建...
  rmdir /s /q ".venv"
)

if not exist "%VENV_PYTHON%" (
  echo [1/5] 正在创建虚拟环境...
  %PYTHON_CMD% -m venv .venv
  if errorlevel 1 (
    echo [错误] 虚拟环境创建失败。
    echo.
    echo 可能原因：Python 未完整安装，或系统缺少 venv 组件。
    goto :fail
  )
) else (
  echo [1/5] 已检测到虚拟环境，跳过创建。
)

echo [2/5] 正在升级 pip...
".venv\Scripts\python.exe" -m pip --version >nul 2>nul
if errorlevel 1 (
  echo [提示] 当前虚拟环境缺少 pip，正在尝试自动修复...
  "%VENV_PYTHON%" -m ensurepip --upgrade
)

"%VENV_PYTHON%" -m pip --version >nul 2>nul
if errorlevel 1 (
  echo [错误] pip 修复失败。
  echo.
  echo 请检查 Python 安装是否包含 ensurepip / venv。
  goto :fail
)

"%VENV_PYTHON%" -m pip install --upgrade pip
if errorlevel 1 (
  echo [警告] pip 升级失败，将继续尝试安装项目依赖。
)

echo [3/5] 正在安装依赖...
"%VENV_PYTHON%" -m pip install -r backend\requirements.txt
if errorlevel 1 (
  echo [错误] 依赖安装失败。
  echo.
  echo 可能原因：
  echo 1. 网络不可用或 PyPI 无法访问
  echo 2. Python 版本过低
  echo 3. 本机缺少编译环境或 SSL 证书异常
  goto :fail
)

if not exist "data" (
  echo [4/5] 正在创建 data 目录...
  mkdir data
  if errorlevel 1 (
    echo [错误] data 目录创建失败。
    goto :fail
  )
) else (
  echo [4/5] data 目录已存在。
)

echo [5/5] 正在启动服务...
echo.
echo 启动后访问: %APP_URL%
echo 按 Ctrl+C 可停止服务。
echo.

netstat -ano | findstr ":8000" >nul 2>nul
if %errorlevel%==0 (
  echo [提示] 检测到 8000 端口可能已被占用。
  echo 如果启动失败，请先关闭占用该端口的程序后重试。
  echo.
)

start "" %APP_URL%
"%VENV_PYTHON%" -m uvicorn backend.app.main:app --reload

echo.
echo 服务已退出。
pause
exit /b 0

:fail
echo.
echo 启动未完成，请根据上方提示处理后重试。
pause
exit /b 1
