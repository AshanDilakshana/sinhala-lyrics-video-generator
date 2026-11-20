import logging
import os
import shutil
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent

# 1. Ensure local venv site-packages are importable even if uvicorn was launched with global python
for venv_name in ("venv", ".venv"):
    for sp in (
        *sorted((BASE_DIR / venv_name / "lib").glob("python*/site-packages")),
        BASE_DIR / venv_name / "Lib" / "site-packages",
    ):
        if sp.is_dir():
            sp_str = str(sp.resolve())
            if sp_str not in sys.path:
                sys.path.insert(0, sp_str)

# 2. Ensure venv Scripts/bin, Homebrew, and local bin directories are on PATH (macOS, Linux & Windows)
_is_windows = sys.platform == "win32"
extra_paths = [
    str((BASE_DIR / "venv" / ("Scripts" if _is_windows else "bin")).resolve()),
    str((BASE_DIR / ".venv" / ("Scripts" if _is_windows else "bin")).resolve()),
]
if not _is_windows:
    extra_paths.extend(["/opt/homebrew/bin", "/usr/local/bin"])

current_path = os.environ.get("PATH", "")
path_parts = [p for p in current_path.split(os.pathsep) if p] if current_path else []
for p in reversed(extra_paths):
    if os.path.isdir(p) and p not in path_parts:
        path_parts.insert(0, p)
os.environ["PATH"] = os.pathsep.join(path_parts)

# 3. Ensure Python builds have valid SSL root certificates
try:
    import certifi

    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
except ImportError:
    pass

# 4. Ensure ffmpeg & ffprobe are available on PATH (via venv, static-ffmpeg, or bundled imageio-ffmpeg)
if not shutil.which("ffmpeg"):
    try:
        import static_ffmpeg

        static_ffmpeg.add_paths(weak=True)
    except Exception as exc:
        logger.debug("static_ffmpeg setup skipped: %s", exc)

if not shutil.which("ffmpeg"):
    try:
        import imageio_ffmpeg

        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        venv_bin_dir = BASE_DIR / "venv" / ("Scripts" if _is_windows else "bin")
        venv_ffmpeg = venv_bin_dir / ("ffmpeg.exe" if _is_windows else "ffmpeg")
        if not venv_ffmpeg.exists() and venv_bin_dir.exists() and not _is_windows:
            try:
                venv_ffmpeg.symlink_to(ffmpeg_exe)
            except Exception:
                pass
        ffmpeg_dir = str(Path(ffmpeg_exe).parent)
        existing_parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
        if ffmpeg_dir not in existing_parts:
            os.environ["PATH"] = os.pathsep.join([ffmpeg_dir, *existing_parts])
        logger.info("Activated bundled imageio-ffmpeg binary: %s", ffmpeg_exe)
    except Exception:
        pass

TEMP_DIR = Path(os.getenv("TEMP_DIR", str(BASE_DIR / "temp"))).resolve()
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(BASE_DIR / "outputs"))).resolve()
FONTS_DIR = Path(os.getenv("FONTS_DIR", str(BASE_DIR / "fonts"))).resolve()
SONGS_CACHE_DIR = (TEMP_DIR / "songs_cache").resolve()
DOWNLOADS_EXPORT_DIR = Path(
    os.getenv(
        "DOWNLOADS_EXPORT_DIR",
        str(Path.home() / "Downloads" / "Titok video meker"),
    )
).resolve()
DRAFTS_EXPORT_DIR = Path(
    os.getenv(
        "DRAFTS_EXPORT_DIR",
        str(DOWNLOADS_EXPORT_DIR / "draft"),
    )
).resolve()

# Ensure directories exist at startup
TEMP_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
FONTS_DIR.mkdir(parents=True, exist_ok=True)
SONGS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
try:
    DOWNLOADS_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    DRAFTS_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
except Exception as exc:
    logger.warning("Could not pre-create Downloads export folder '%s': %s", DOWNLOADS_EXPORT_DIR, exc)

# Spotify Tier 2 Settings
SP_DC_COOKIE = os.getenv("SP_DC_COOKIE", "")
SPOTIFY_LYRICS_API_URL = os.getenv(
    "SPOTIFY_LYRICS_API_URL", "https://spotify-lyrics-api-pi.vercel.app"
)

# Tier 3 Forced Alignment Settings
ALIGNMENT_ENGINE = os.getenv("ALIGNMENT_ENGINE", "auto")  # auto | aeneas | whisper
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "small")
DEFAULT_ALIGNMENT_LANG = os.getenv("DEFAULT_ALIGNMENT_LANG", "si")

FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")


def create_resilient_async_client(
    timeout: float = 15.0,
    headers=None,
    verify: bool = False,
    follow_redirects: bool = True,
):
    """
    Create an `httpx.AsyncClient` bound to IPv4 (`0.0.0.0`) so networks with broken or
    blackholed IPv6 routing never hang 20-30s on DNS AAAA addresses, with automatic fallback.
    """
    import httpx

    try:
        transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0", verify=verify)
        return httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=follow_redirects,
            transport=transport,
            headers=headers,
        )
    except Exception:
        return httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=follow_redirects,
            verify=verify,
            headers=headers,
        )


def create_resilient_sync_client(
    timeout: float = 10.0,
    headers=None,
    verify: bool = False,
    follow_redirects: bool = True,
):
    """Synchronous IPv4-first `httpx.Client` for STT & threadpool workers."""
    import httpx

    try:
        transport = httpx.HTTPTransport(local_address="0.0.0.0", verify=verify)
        return httpx.Client(
            timeout=timeout,
            follow_redirects=follow_redirects,
            transport=transport,
            headers=headers,
        )
    except Exception:
        return httpx.Client(
            timeout=timeout,
            follow_redirects=follow_redirects,
            verify=verify,
            headers=headers,
        )
