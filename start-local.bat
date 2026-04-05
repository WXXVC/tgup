@echo off
setlocal

cd /d "%~dp0"
title TG Upload Manager - Local Start

set "APP_URL=http://127.0.0.1:8000"
set "VENV_DIR=.venv"
set "VENV_PYTHON=.venv\Scripts\python.exe"
set "PYTHON_CMD="
set "REBUILT_VENV="

echo.
echo ========================================
echo   TG Upload Manager Local Starter
echo ========================================
echo.

where py >nul 2>nul
if not errorlevel 1 (
  set "PYTHON_CMD=py"
) else (
  where python >nul 2>nul
  if not errorlevel 1 (
    set "PYTHON_CMD=python"
  )
)

if not defined PYTHON_CMD (
  echo [ERROR] Python was not found.
  echo Please install Python 3.11+ and add it to PATH.
  goto :fail
)

if exist "%VENV_DIR%" if not exist "%VENV_PYTHON%" (
  echo [INFO] Broken virtual environment detected. Recreating...
  rmdir /s /q "%VENV_DIR%"
)

if not exist "%VENV_PYTHON%" (
  call :create_venv
  if errorlevel 1 goto :fail
) else (
  echo [1/5] Virtual environment already exists.
)

echo [2/5] Checking pip...
"%VENV_PYTHON%" -m pip --version >nul 2>nul
if errorlevel 1 (
  echo [INFO] pip is missing in the venv. Trying ensurepip...
  "%VENV_PYTHON%" -m ensurepip --upgrade
)

"%VENV_PYTHON%" -m pip --version >nul 2>nul
if errorlevel 1 (
  echo [ERROR] pip is still unavailable.
  echo Please check your Python installation.
  goto :fail
)

echo [3/5] Installing dependencies...
"%VENV_PYTHON%" -m pip install --upgrade pip > "%TEMP%\tgup_pip_upgrade.log" 2>&1
if errorlevel 1 (
  findstr /i /c:"no RECORD file was found for pip" "%TEMP%\tgup_pip_upgrade.log" >nul 2>nul
  if not errorlevel 1 (
    echo [WARN] Broken pip metadata detected. Rebuilding virtual environment...
    call :rebuild_venv
    if errorlevel 1 goto :fail
  ) else (
    echo [WARN] pip upgrade failed. Continuing anyway...
  )
)

"%VENV_PYTHON%" -m pip install -r backend\requirements.txt > "%TEMP%\tgup_requirements.log" 2>&1
if errorlevel 1 (
  findstr /i /c:"no RECORD file was found for pip" "%TEMP%\tgup_requirements.log" >nul 2>nul
  if not errorlevel 1 if not defined REBUILT_VENV (
    echo [WARN] Broken pip metadata detected while installing dependencies.
    echo [INFO] Rebuilding virtual environment and retrying once...
    call :rebuild_venv
    if errorlevel 1 goto :fail
    "%VENV_PYTHON%" -m pip install -r backend\requirements.txt
    if not errorlevel 1 goto :deps_ok
  )
  echo [ERROR] Failed to install dependencies.
  echo Possible causes:
  echo   - Network / PyPI access issue
  echo   - Unsupported Python version
  echo   - Local SSL or build environment issue
  goto :fail
)

:deps_ok
if not exist "data" (
  echo [4/5] Creating data directory...
  mkdir data
  if errorlevel 1 (
    echo [ERROR] Failed to create data directory.
    goto :fail
  )
) else (
  echo [4/5] data directory already exists.
)

echo [5/5] Starting service...
netstat -ano | findstr ":8000" >nul 2>nul
if not errorlevel 1 (
  echo [WARN] Port 8000 may already be in use.
  echo If startup fails, stop the process using port 8000 and try again.
)

echo.
echo App URL: %APP_URL%
echo Press Ctrl+C to stop the server.
echo.

start "" %APP_URL%
"%VENV_PYTHON%" -m uvicorn backend.app.main:app --reload

echo.
echo Service stopped.
pause
exit /b 0

:fail
echo.
echo Startup did not complete. See the messages above.
pause
exit /b 1

:create_venv
echo [1/5] Creating virtual environment...
%PYTHON_CMD% -m venv "%VENV_DIR%"
if errorlevel 1 (
  echo [WARN] Built-in venv creation failed. Trying virtualenv fallback...
  %PYTHON_CMD% -m pip install --upgrade virtualenv
  if errorlevel 1 (
    echo [ERROR] Failed to install virtualenv fallback.
    echo Check your network connection and Python installation.
    exit /b 1
  )
  %PYTHON_CMD% -m virtualenv "%VENV_DIR%"
  if errorlevel 1 (
    echo [ERROR] Failed to create virtual environment.
    echo Check whether Python includes venv support or can install virtualenv.
    exit /b 1
  )
)
exit /b 0

:rebuild_venv
set "REBUILT_VENV=1"
if exist "%VENV_DIR%" (
  rmdir /s /q "%VENV_DIR%"
)
call :create_venv
if errorlevel 1 exit /b 1
"%VENV_PYTHON%" -m ensurepip --upgrade >nul 2>nul
"%VENV_PYTHON%" -m pip install --upgrade pip >nul 2>nul
exit /b 0
