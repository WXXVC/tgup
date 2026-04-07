@echo off
setlocal

cd /d "%~dp0"
title TG Upload Manager - Local Start

set "APP_URL=http://127.0.0.1:8000"
set "VENV_DIR=.venv"
set "VENV_PYTHON=.venv\Scripts\python.exe"
set "TOOLS_DIR=%CD%\tools"
set "FFMPEG_DIR=%TOOLS_DIR%\ffmpeg"
set "FFMPEG_BIN_DIR=%FFMPEG_DIR%\bin"
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

echo [3/6] Installing dependencies...
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
echo [4/6] Checking ffmpeg / ffprobe...
call :ensure_ffmpeg
if errorlevel 1 goto :fail

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [ERROR] ffmpeg was not found in PATH.
  echo This project requires ffmpeg and ffprobe for large video segment uploads.
  echo Please install FFmpeg and ensure both ffmpeg.exe and ffprobe.exe are available in PATH.
  goto :fail
)

where ffprobe >nul 2>nul
if errorlevel 1 (
  echo [ERROR] ffprobe was not found in PATH.
  echo This project requires ffmpeg and ffprobe for large video segment uploads.
  echo Please install FFmpeg and ensure both ffmpeg.exe and ffprobe.exe are available in PATH.
  goto :fail
)

if not exist "data" (
  echo [5/6] Creating data directory...
  mkdir data
  if errorlevel 1 (
    echo [ERROR] Failed to create data directory.
    goto :fail
  )
) else (
  echo [5/6] data directory already exists.
)

echo [6/6] Starting service...
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
echo [1/6] Creating virtual environment...
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

:ensure_ffmpeg
if exist "%FFMPEG_BIN_DIR%\ffmpeg.exe" if exist "%FFMPEG_BIN_DIR%\ffprobe.exe" (
  set "PATH=%FFMPEG_BIN_DIR%;%PATH%"
  exit /b 0
)

where ffmpeg >nul 2>nul
if not errorlevel 1 (
  where ffprobe >nul 2>nul
  if not errorlevel 1 exit /b 0
)

echo [INFO] ffmpeg / ffprobe not found. Downloading local FFmpeg package...
if not exist "%TOOLS_DIR%" mkdir "%TOOLS_DIR%"

set "FFMPEG_ZIP=%TEMP%\tgup_ffmpeg.zip"
set "FFMPEG_EXTRACT=%TEMP%\tgup_ffmpeg_extract"
if exist "%FFMPEG_ZIP%" del /f /q "%FFMPEG_ZIP%" >nul 2>nul
if exist "%FFMPEG_EXTRACT%" rmdir /s /q "%FFMPEG_EXTRACT%"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ProgressPreference='SilentlyContinue';" ^
  "Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile '%FFMPEG_ZIP%'"
if errorlevel 1 (
  echo [ERROR] Failed to download FFmpeg automatically.
  echo Please check your network connection, or install FFmpeg manually and add it to PATH.
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ProgressPreference='SilentlyContinue';" ^
  "Expand-Archive -LiteralPath '%FFMPEG_ZIP%' -DestinationPath '%FFMPEG_EXTRACT%' -Force"
if errorlevel 1 (
  echo [ERROR] Failed to extract FFmpeg package.
  echo Please install FFmpeg manually and add it to PATH.
  exit /b 1
)

for /d %%D in ("%FFMPEG_EXTRACT%\*") do (
  if exist "%%~fD\bin\ffmpeg.exe" (
    if exist "%FFMPEG_DIR%" rmdir /s /q "%FFMPEG_DIR%"
    move "%%~fD" "%FFMPEG_DIR%" >nul
    goto :ffmpeg_ready
  )
)

echo [ERROR] FFmpeg package was downloaded, but the bin directory was not found.
echo Please install FFmpeg manually and add it to PATH.
exit /b 1

:ffmpeg_ready
set "PATH=%FFMPEG_BIN_DIR%;%PATH%"
if not exist "%FFMPEG_BIN_DIR%\ffmpeg.exe" (
  echo [ERROR] ffmpeg.exe is still missing after download.
  exit /b 1
)
if not exist "%FFMPEG_BIN_DIR%\ffprobe.exe" (
  echo [ERROR] ffprobe.exe is still missing after download.
  exit /b 1
)
echo [INFO] Local FFmpeg is ready: %FFMPEG_BIN_DIR%
exit /b 0
