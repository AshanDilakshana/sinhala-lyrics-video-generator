@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo   TikTok AI Lyrics Video Editor - Windows Setup
echo ============================================================
echo.

REM 1. Locate Python 3.10+
set "PYTHON_CMD="
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 --version >nul 2>&1
    if !errorlevel!==0 set "PYTHON_CMD=py -3"
)
if not defined PYTHON_CMD (
    where python >nul 2>&1
    if !errorlevel!==0 (
        python --version >nul 2>&1
        if !errorlevel!==0 set "PYTHON_CMD=python"
    )
)
if not defined PYTHON_CMD (
    echo [ERROR] Python 3.10+ not found!
    echo Please install Python from https://www.python.org/downloads/windows/
    echo IMPORTANT: Check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('%PYTHON_CMD% --version') do echo [OK] Found %%v

REM 2. Verify Node.js & npm
where node >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Node.js not found!
    echo Please install Node.js LTS from https://nodejs.org/
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('node --version') do echo [OK] Found Node.js %%v

where npm >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] npm not found! Please reinstall Node.js LTS from https://nodejs.org/
    pause
    exit /b 1
)

REM 3. Create .env from .env.example if missing
if not exist ".env" (
    if exist ".env.example" (
        copy ".env.example" ".env" >nul
        echo [OK] Created .env from .env.example
    )
)

REM 4. Create Python Virtual Environment (venv)
if not exist "venv\Scripts\python.exe" (
    echo.
    echo [*] Creating Python virtual environment in venv\...
    %PYTHON_CMD% -m venv venv
    if !errorlevel! neq 0 (
        echo [ERROR] Failed to create Python virtual environment.
        pause
        exit /b 1
    )
)

echo.
echo [*] Upgrading pip and installing backend dependencies...
"venv\Scripts\python.exe" -m pip install --upgrade pip
"venv\Scripts\python.exe" -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install Python packages from requirements.txt.
    pause
    exit /b 1
)

echo.
echo [*] Checking / downloading FFmpeg + FFprobe for Windows...
"venv\Scripts\python.exe" -c "import shutil; import static_ffmpeg; static_ffmpeg.add_paths(weak=True); print('ffmpeg:', shutil.which('ffmpeg')); print('ffprobe:', shutil.which('ffprobe'))"

REM 5. Install Frontend dependencies
echo.
echo [*] Installing frontend npm dependencies...
pushd "%~dp0frontend"
call npm install
set "NPM_ERR=%errorlevel%"
popd
if %NPM_ERR% neq 0 (
    echo [ERROR] npm install failed in frontend\.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Setup Complete! Double-click start_windows.bat to run.
echo ============================================================
pause
