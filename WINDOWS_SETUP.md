# Running on Windows (10 / 11)

## Prerequisites
1. **Python 3.10 – 3.14** (64-bit) from https://www.python.org/downloads/windows/
   - During installation, check **"Add Python to PATH"**.
2. **Node.js (LTS)** from https://nodejs.org/

> **Note when copying from macOS:** Do **not** copy the macOS `venv/` folder (or delete `venv/` on Windows before running setup). `setup_windows.bat` will create a fresh Windows `venv/` automatically.

## Quick Start (1-Click)
1. Double-click **`start_windows.bat`** (or run `setup_windows.bat` first).
2. On first run, it automatically:
   - Creates a Windows Python `venv` and installs `requirements.txt`
   - Downloads bundled Windows `ffmpeg.exe` & `ffprobe.exe` via `static-ffmpeg` (with `imageio-ffmpeg` fallback)
   - Installs frontend dependencies (`npm install` inside `frontend/`)
3. Opens two console windows (Backend on `:8000`, Frontend on `:3000`) and launches `http://localhost:3000` in your browser.

## Fonts & Rendering on Windows
- Bundled Sinhala `.ttf` fonts in `fonts/` (`Abhaya Libre Bold`, `Noto Sans Sinhala`, `Noto Serif Sinhala`, `Gemunu Libre`, `Yaldevi`, etc.) work identically on Windows and macOS.
- Windows system Sinhala fonts (**Iskoola Pota** and **Nirmala UI**) and **Segoe UI Emoji** (`seguiemj.ttf`) are automatically detected for subtitles and emoji watermarks.
- Rendered videos are saved to `outputs/` and copied to `%USERPROFILE%\Downloads\Titok video meker`.
