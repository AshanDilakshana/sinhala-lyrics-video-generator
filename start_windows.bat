@echo off
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [*] First-time setup required. Running setup_windows.bat...
    call "%~dp0setup_windows.bat"
    if errorlevel 1 exit /b 1
)

if not exist "frontend\node_modules" (
    echo [*] Frontend dependencies missing. Running setup_windows.bat...
    call "%~dp0setup_windows.bat"
    if errorlevel 1 exit /b 1
)

if not exist ".env" (
    if exist ".env.example" copy ".env.example" ".env" >nul
)

echo ============================================================
echo   Starting TikTok AI Lyrics Video Editor (Windows)
echo ============================================================
echo.
echo   Backend API : http://localhost:8000
echo   Frontend UI : http://localhost:3000
echo.
echo   Keep both opened console windows running while using the app.
echo ============================================================

start "TikTok Lyrics Backend (Port 8000)" /D "%~dp0" cmd /k venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000

start "TikTok Lyrics Frontend (Port 3000)" /D "%~dp0frontend" cmd /k npm run dev

timeout /t 4 /nobreak >nul
start "" "http://localhost:3000"
