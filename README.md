# Sinhala Lyrics Video Generator (TikTok Aesthetic AI Studio)

A full-stack **9:16 TikTok / Reels Sinhala AI Lyrics Video Editor** built with **FastAPI + FFmpeg (libass + HarfBuzz)** and **Next.js (React + Tailwind CSS)**. Designed for Sinhala & English songs with Shazam audio identification, multi-site Sinhala lyrics discovery, Local AI phoneme forced alignment, live NLE studio preview, custom Sinhala font management, and draft/final export workflows on **macOS** and **Windows**.

---

## Key Features & Functions

### 1. Song Fetching & Audio Identification
- **Multi-Source Song Input**: Paste a TikTok video link (`tiktok.com`, `vt.tiktok.com`, `vm.tiktok.com`), TikTok Sound/Music page (`/music/...`), YouTube URL, song title search, or upload a local audio file (`.mp3`, `.wav`, `.m4a`).
- **TikTok `/music/` & API Fallback** (`backend/services/media_service.py`): Automatically extracts audio from TikTok `/music/...-<id>` sound pages and short links via `tikwm` API + TikTok page rehydration fallback when `yt-dlp` is blocked.
- **Stop Fetching Control**: Instant **Stop** button (`AbortController` + backend disconnect check) to cancel slow/wrong song fetches cleanly.
- **Shazam Audio Fingerprinting (`shazamio`)**: Identifies the real **Song Title**, **Artist**, **Clip Start Offset** inside the full studio track, **Speed/Time Skew**, and embedded Shazam lyrics directly from the waveform — even when TikTok captions say `"original sound"` or `#fyp`.

### 2. Sequential Sinhala Lyrics Discovery
- **Sri Lankan Lyrics Priority** (`backend/services/sri_lanka_lyrics_service.py`, `backend/services/lyrics_service.py`):
  1. `lyrihub.lk`
  2. `lyrics-lk.com`
  3. `geemansala.com`
  4. Shazam Embedded Lyrics
  5. `LRCLIB` (`lrclib.net`, with Shazam clip-offset slicing)
  6. `Spotify` (`SP_DC_COOKIE`, checked last)
- **Audio Verification**: Every candidate lyric sheet is verified against the sung vocals before acceptance so mismatched lyrics are rejected automatically.
- **Clean Text Normalization**: Strips site headers/footers, repairs broken Sinhala Rakaraansha ZWJ sequences, and splits compound two-phrase lines.

### 3. Word-Level Sinhala Lyrics Sync & Local AI Alignment
- **Stereo Center-Vocal Isolation** (`backend/services/alignment_service.py`): Uses FFmpeg `stereotools` center-channel isolation + vocal bandpass (`180Hz–4200Hz`) + `afftdn` + `dynaudnorm` to separate lead vocals from stereo instruments.
- **Local AI Phoneme Forced Aligner (Optional, Free, Offline)**: Integrates `ctc-forced-aligner` with Meta MMS (`MahmoudAshraf/mms-300m-1130-forced-aligner`, `sin` romanized) for millisecond-accurate word-level `WordTiming` sync.
- **ASR Anchor + Breath-Dip DP Fallback**: Uses Google Speech-to-Text (`si-LK` / `en-US`) anchors and STFT vocal-envelope breath-dip dynamic programming with syllable/mora (මාත්‍රා) weighting when offline or when `ctc-forced-aligner` is not installed.

### 4. Live NLE Video Studio & Customization (`frontend/src/app/page.tsx`)
- **Live 9:16 Canvas Preview**: Real-time preview of rounded square card / full-screen frames, color grades, zoom, mirror flip, vignette, blur transitions, and animated Sinhala captions with waveform scrubbing.
- **Interactive Lyrics Timeline Editor**: Edit line text, adjust individual start/end timestamps, shift global lyrics offset (`lyrics_offset_sec`), or paste manual `.lrc` / plain lyrics and re-sync.
- **Multi-Clip Visual Editing**: Upload multiple AI/animated video clips, trim start/end, configure split clip segments, set playback speed, and choose loop modes (`crossfade`, `boomerang`, `loop`).
- **Custom Sinhala Font Manager** (`backend/services/font_service.py`): Supports Unicode & FM/legacy Sinhala fonts (with automatic Unicode-to-FM glyph mapping), custom `.ttf`/`.otf` upload, URL font download, system font extraction, and real FFmpeg/libass preview PNGs.

### 5. Draft & Final Render Export (`backend/main.py`, `backend/services/video_service.py`)
- **FFmpeg + libass HarfBuzz Rendering**: Renders 9:16 MP4s with BT.709 color parity, word-by-word fade/highlight subtitles, and custom watermarks.
- **Draft Folder Workflow**:
  - **Save Draft**: Renders to `~/Downloads/Titok video meker/draft` (macOS) or `%USERPROFILE%\Downloads\Titok video meker\draft` (Windows).
  - **Finalize / Render Draft**: Moves/renders the video into `~/Downloads/Titok video meker` and automatically removes it from the `draft` folder.
- **Persistent Job Recovery**: Saves render job states to `temp/jobs_state.json` so server reloads don't lose completed renders.

---

## Project Structure

```text
.
├── backend/
│   ├── main.py                        # FastAPI app, endpoints, draft/render job manager
│   ├── config.py                      # Cross-platform paths, SSL, bundled FFmpeg setup
│   ├── models.py                      # Pydantic schemas (LyricLine, VideoEditConfig, JobStatusResponse)
│   └── services/
│       ├── alignment_service.py       # Stereo vocal isolation, MMS CTC aligner, ASR & breath-dip DP
│       ├── font_service.py            # Custom Sinhala font upload/preview & system font extraction
│       ├── lyrics_service.py          # Sequential lyrics resolver & Shazam offset slicer
│       ├── media_service.py           # TikTok/YouTube/audio downloader & Shazam fingerprinting
│       ├── sri_lanka_lyrics_service.py# lyrihub.lk, lyrics-lk.com, geemansala.com scrapers & cache
│       ├── subtitle_service.py        # ASS subtitle generator (word-fade, FM/Unicode Sinhala)
│       └── video_service.py           # FFmpeg 9:16 card/color-grade/loop/watermark renderer
├── frontend/
│   ├── src/app/page.tsx               # Next.js NLE Studio UI
│   └── package.json
├── fonts/                             # Bundled Sinhala .ttf fonts
├── setup_windows.bat                  # One-time Windows venv + npm + FFmpeg setup
├── start_windows.bat                  # One-click Windows launcher
├── START_COMMANDS.txt                 # Quick macOS & Windows start commands
├── WINDOWS_SETUP.md                   # Windows setup guide
├── requirements.txt                   # Python dependencies
└── .env.example                       # Environment variable template
```

---

## Setup & Running

### Prerequisites
- **Python 3.10+** (3.11–3.13 recommended if installing `ctc-forced-aligner`)
- **Node.js 18+** & `npm`
- **FFmpeg** (auto-resolved via `static-ffmpeg` / `imageio-ffmpeg` or system `ffmpeg`)

### macOS
1. **Backend**:
   ```bash
   python3 -m venv venv
   ./venv/bin/pip install -r requirements.txt
   cp .env.example .env
   ./venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
   ```
2. **Frontend** (new terminal):
   ```bash
   cd frontend
   npm install
   npm run dev
   ```
3. *(Optional)* Enable Local AI word-level Sinhala phoneme sync:
   ```bash
   ./venv/bin/pip install torch torchaudio ctc-forced-aligner
   ```

### Windows
- **One-click**: Run `setup_windows.bat` (first time), then double-click `start_windows.bat`.
- See [WINDOWS_SETUP.md](WINDOWS_SETUP.md) and [START_COMMANDS.txt](START_COMMANDS.txt) for manual commands.

### URLs
- **Studio UI**: `http://localhost:3000`
- **Backend API**: `http://localhost:8000`
- **Health Check**: `http://localhost:8000/api/health`
