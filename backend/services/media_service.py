import asyncio
import json
import logging
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from fastapi import UploadFile
import httpx
import yt_dlp

from backend.config import create_resilient_async_client

logger = logging.getLogger(__name__)


def _resolve_venv_binary(binary_name: str) -> Optional[Path]:
    """Check local venv/Scripts (Windows) or venv/bin (POSIX) for an executable."""
    is_win = sys.platform == "win32"
    sub_dir = "Scripts" if is_win else "bin"
    exe_name = f"{binary_name}.exe" if is_win else binary_name
    root_dir = Path(__file__).resolve().parent.parent.parent
    for venv_name in ("venv", ".venv"):
        candidate = root_dir / venv_name / sub_dir / exe_name
        if candidate.is_file():
            return candidate
    return None


_STATIC_FFMPEG_TRIED = False


def _try_enable_static_ffmpeg() -> None:
    """Attempt once per process to add static-ffmpeg binaries to PATH if missing."""
    global _STATIC_FFMPEG_TRIED
    if _STATIC_FFMPEG_TRIED:
        return
    _STATIC_FFMPEG_TRIED = True
    try:
        import static_ffmpeg

        static_ffmpeg.add_paths(weak=True)
    except Exception:
        pass


def get_ffmpeg_binary() -> str:
    """Resolve the active ffmpeg executable path across Windows, macOS, and Linux."""
    venv_ffmpeg = _resolve_venv_binary("ffmpeg")
    if venv_ffmpeg:
        return str(venv_ffmpeg)
    found = shutil.which("ffmpeg")
    if found:
        return found
    _try_enable_static_ffmpeg()
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"


def get_ffprobe_binary() -> Optional[str]:
    """Resolve the active ffprobe executable path if available."""
    venv_ffprobe = _resolve_venv_binary("ffprobe")
    if venv_ffprobe:
        return str(venv_ffprobe)
    probe_name = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
    try:
        sibling = Path(get_ffmpeg_binary()).with_name(probe_name)
        if sibling.is_file():
            return str(sibling)
    except Exception:
        pass
    return shutil.which("ffprobe")


async def save_uploaded_visual_clips(
    upload_files: List[UploadFile],
    job_temp_dir: Path,
) -> List[Path]:
    """
    Save one or multiple uploaded AI-generated animated video clips
    into the job's temporary directory.
    """
    saved_paths: List[Path] = []

    def _write_all():
        for idx, up_file in enumerate(upload_files):
            if not up_file or not up_file.filename:
                continue
            suffix = Path(up_file.filename).suffix or ".mp4"
            clip_path = job_temp_dir / f"ai_clip_{idx:02d}{suffix}"
            with open(clip_path, "wb") as buffer:
                shutil.copyfileobj(up_file.file, buffer)
            saved_paths.append(clip_path)

    await asyncio.to_thread(_write_all)
    logger.info("Saved %d uploaded AI video clip(s) to %s", len(saved_paths), job_temp_dir)
    return saved_paths


async def save_uploaded_file(
    upload_file: UploadFile,
    job_temp_dir: Path,
    prefix: str = "uploaded_media",
) -> Path:
    """Save a single uploaded media file asynchronously."""
    suffix = Path(upload_file.filename or "input.mp4").suffix or ".mp4"
    dest_path = job_temp_dir / f"{prefix}{suffix}"

    def _write_sync():
        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(upload_file.file, buffer)

    await asyncio.to_thread(_write_sync)
    logger.info("Saved uploaded file to %s", dest_path)
    return dest_path


def _is_generic_tiktok_sound(t: str) -> bool:
    tl = (t or "").strip().lower()
    return (
        not tl
        or "original sound" in tl
        or "sonido original" in tl
        or "suono originale" in tl
        or "nhạc nền" in tl
        or "оригинальный звук" in tl
        or tl == "tiktok song"
    )


def _extract_tiktok_music_id(url: str) -> Optional[str]:
    """Extract numeric music_id from TikTok `/music/...-7521243310598458113` URLs."""
    m = re.search(r"/music/(?:[^/?#]*?-)?(\d{10,25})(?:[/?#]|$)", url)
    if m:
        return m.group(1)
    m2 = re.search(r"music_id=(\d{10,25})", url)
    if m2:
        return m2.group(1)
    return None


async def _fetch_tiktok_music_page_fallback(
    client: httpx.AsyncClient,
    tiktok_url: str,
    music_id: str,
    job_temp_dir: Path,
    prefix: str,
    audio_only: bool = True,
) -> Optional[Tuple[Path, Dict[str, Any]]]:
    """
    Download audio & metadata for TikTok `/music/<slug>-<music_id>` sound links
    (where yt-dlp's `TikTokSoundIE` has `_WORKING = False` and fails with
    `ERROR: No working app info is available`).
    """
    play_url = ""
    music_title = ""
    artist = ""
    post_caption = ""
    duration = 0.0

    # 1. Try tikwm `/api/music/info`
    for payload_Attempt in ({"url": tiktok_url}, {"music_id": music_id}):
        try:
            resp = await client.post("https://www.tikwm.com/api/music/info", data=payload_Attempt)
            if resp.status_code == 200:
                d = (resp.json() or {}).get("data") or {}
                if isinstance(d, dict) and (d.get("play") or d.get("music")):
                    play_url = d.get("play") or d.get("music") or ""
                    music_title = (d.get("title") or "").strip()
                    artist = (d.get("author") or "").strip()
                    duration = float(d.get("duration") or 0.0)
                    break
        except Exception as exc:
            logger.debug("tikwm /api/music/info attempt failed: %s", exc)

    # 2. Try tikwm `/api/music/posts` (also gives top video post caption when sound is 'original sound'!)
    if not play_url or _is_generic_tiktok_sound(music_title):
        try:
            resp_posts = await client.post(
                "https://www.tikwm.com/api/music/posts",
                data={"music_id": music_id, "count": 5, "cursor": 0},
            )
            if resp_posts.status_code == 200:
                d_posts = (resp_posts.json() or {}).get("data") or {}
                videos = d_posts.get("videos") if isinstance(d_posts, dict) else None
                if isinstance(videos, list) and videos:
                    for v in videos:
                        if not isinstance(v, dict):
                            continue
                        m_info = v.get("music_info") or {}
                        v_caption = (v.get("title") or "").strip()
                        if v_caption and not post_caption:
                            post_caption = v_caption
                        if not play_url:
                            cand_music = m_info.get("play") or v.get("music")
                            cand_video = v.get("play") or v.get("wmplay")
                            play_url = (cand_music or cand_video) if audio_only else (cand_video or cand_music)
                            if not music_title:
                                music_title = (m_info.get("title") or "").strip()
                            if not artist:
                                artist = (
                                    m_info.get("author")
                                    or (v.get("author") or {}).get("nickname")
                                    or ""
                                ).strip()
                            if not duration:
                                duration = float(m_info.get("duration") or v.get("duration") or 0.0)
                        if play_url and post_caption:
                            break
        except Exception as exc:
            logger.debug("tikwm /api/music/posts attempt failed: %s", exc)

    # 3. Direct TikTok `/music/...` HTML rehydration JSON (`__UNIVERSAL_DATA_FOR_REHYDRATION__`)
    if not play_url:
        try:
            html_headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.tiktok.com/",
            }
            page_resp = await client.get(tiktok_url, headers=html_headers)
            if page_resp.status_code == 200 and page_resp.text:
                html_text = page_resp.text
                m_json = re.search(
                    r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
                    html_text,
                    re.DOTALL,
                )
                if m_json:
                    rehyd = json.loads(m_json.group(1))
                    scope = (rehyd or {}).get("__DEFAULT_SCOPE__") or {}
                    music_detail = scope.get("webapp.music-detail") or {}
                    m_obj = ((music_detail.get("musicInfo") or {}).get("music")) or {}
                    if isinstance(m_obj, dict):
                        play_url = (m_obj.get("playUrl") or "").strip()
                        music_title = music_title or (m_obj.get("title") or "").strip()
                        artist = artist or (m_obj.get("authorName") or "").strip()
                        duration = duration or float(m_obj.get("duration") or 0.0)
                if not play_url:
                    m_play = re.search(r'"playUrl"\s*:\s*"([^"]+)"', html_text)
                    if m_play:
                        play_url = m_play.group(1).encode("utf-8").decode("unicode_escape")
        except Exception as exc:
            logger.debug("Direct TikTok music page HTML parse failed: %s", exc)

    if not play_url:
        return None

    if play_url.startswith("/"):
        play_url = f"https://www.tikwm.com{play_url}"

    media_resp = await client.get(
        play_url,
        headers={"Referer": "https://www.tiktok.com/"},
    )
    if media_resp.status_code != 200 or len(media_resp.content) < 1024:
        return None

    ctype = (media_resp.headers.get("content-type") or "").lower()
    ext = ".mp4" if ("video" in ctype and not audio_only) else ".mp3"
    out_path = job_temp_dir / f"{prefix}{ext}"
    out_path.write_bytes(media_resp.content)

    # Also extract any clean slug title from the URL itself if needed
    slug_match = re.search(r"/music/([^/?#]+)-\d{10,25}", tiktok_url)
    url_slug_title = ""
    if slug_match:
        from urllib.parse import unquote
        url_slug_title = unquote(slug_match.group(1)).replace("-", " ").strip()

    if _is_generic_tiktok_sound(music_title) and post_caption:
        title = post_caption
        alt_title = music_title or url_slug_title
    else:
        title = music_title or post_caption or url_slug_title or "TikTok Song"
        alt_title = post_caption if post_caption != title else url_slug_title

    logger.info(
        "Downloaded TikTok /music/ sound (id=%s): %s (title=%s, alt=%s, artist=%s)",
        music_id,
        out_path,
        title,
        alt_title,
        artist,
    )
    return out_path, {
        "title": title,
        "alt_title": alt_title,
        "artist": artist,
        "duration": duration,
        "webpage_url": tiktok_url,
    }


async def _fetch_tiktok_via_api_fallback(
    tiktok_url: str,
    job_temp_dir: Path,
    prefix: str,
    audio_only: bool = True,
    progress_cb: Optional[Callable[[int, str], None]] = None,
    max_height: Optional[int] = None,
) -> Optional[Tuple[Path, Dict[str, Any]]]:
    """
    Resilient fallback for TikTok links (including TikTok `/music/...` sound pages,
    vt.tiktok.com, vm.tiktok.com, TikTok photo/slideshow posts, and region-locked
    TikTok videos) when yt-dlp is blocked or unsupported.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
    }
    try:
        async with create_resilient_async_client(timeout=25.0, headers=headers) as client:
            # If this is directly a TikTok `/music/...-<id>` link, handle it via our music-page fetcher
            direct_music_id = _extract_tiktok_music_id(tiktok_url)
            if direct_music_id:
                music_res = await _fetch_tiktok_music_page_fallback(
                    client=client,
                    tiktok_url=tiktok_url,
                    music_id=direct_music_id,
                    job_temp_dir=job_temp_dir,
                    prefix=prefix,
                    audio_only=audio_only,
                )
                if music_res is not None:
                    return music_res

            resp = await client.post(
                "https://www.tikwm.com/api/",
                data={"url": tiktok_url, "hd": 1},
            )
            data = None
            if resp.status_code == 200:
                payload = resp.json()
                data = payload.get("data")

            # If a short vt.tiktok.com / vm.tiktok.com link redirected to a `/music/...` page,
            # resolve the redirect and run `_fetch_tiktok_music_page_fallback`!
            if not data:
                try:
                    head_resp = await client.get(tiktok_url)
                    resolved_url = str(head_resp.url)
                    resolved_music_id = _extract_tiktok_music_id(resolved_url)
                    if resolved_music_id:
                        music_res = await _fetch_tiktok_music_page_fallback(
                            client=client,
                            tiktok_url=resolved_url,
                            music_id=resolved_music_id,
                            job_temp_dir=job_temp_dir,
                            prefix=prefix,
                            audio_only=audio_only,
                        )
                        if music_res is not None:
                            return music_res
                except Exception:
                    pass
                return None

            music_info = data.get("music_info") or {}
            music_url = music_info.get("play") or data.get("music")
            hd_video_url = data.get("hdplay")
            sd_video_url = data.get("play") or data.get("wmplay")
            if max_height is not None and max_height <= 540:
                video_url = sd_video_url or hd_video_url
            else:
                video_url = hd_video_url or sd_video_url

            # Choose audio stream if audio_only and music_url is available, else video stream
            download_target = (music_url or video_url) if audio_only else (video_url or music_url)
            if not download_target:
                return None

            if download_target.startswith("/"):
                download_target = f"https://www.tikwm.com{download_target}"

            ext = ".mp3" if (audio_only and music_url) else ".mp4"
            out_path = job_temp_dir / f"{prefix}{ext}"

            if progress_cb:
                progress_cb(10, "Connecting to TikTok media stream...")
            chunks: List[bytes] = []
            downloaded_bytes = 0
            async with client.stream("GET", download_target) as media_resp:
                if media_resp.status_code != 200:
                    return None
                total_bytes = int(media_resp.headers.get("content-length") or 0)
                async for chunk in media_resp.aiter_bytes(chunk_size=65536):
                    if not chunk:
                        continue
                    chunks.append(chunk)
                    downloaded_bytes += len(chunk)
                    if progress_cb:
                        if total_bytes > 0:
                            pct = min(92, max(12, int((downloaded_bytes / total_bytes) * 85) + 10))
                            mb_done = downloaded_bytes / (1024 * 1024)
                            mb_tot = total_bytes / (1024 * 1024)
                            progress_cb(pct, f"Downloading TikTok video: {pct}% ({mb_done:.1f} / {mb_tot:.1f} MB)")
                        else:
                            mb_done = downloaded_bytes / (1024 * 1024)
                            progress_cb(45, f"Downloading TikTok video... ({mb_done:.1f} MB)")

            if downloaded_bytes < 1024:
                return None

            out_path.write_bytes(b"".join(chunks))
            if progress_cb:
                progress_cb(95, "Finalizing downloaded video...")

            music_title = (music_info.get("title") or "").strip()
            # Never use the TikTok video's post caption/description (`data.get("title")`)
            # for song/lyrics search — rely on real music_info or Shazam audio fingerprinting.
            if _is_generic_tiktok_sound(music_title):
                title = ""
                alt_title = ""
                artist = ""
            else:
                title = music_title
                alt_title = ""
                artist = (music_info.get("author") or "").strip()
            duration = float(music_info.get("duration") or data.get("duration") or 0.0)

            logger.info("Downloaded TikTok song/media via API fallback: %s (title=%r, artist=%r)", out_path, title, artist)
            return out_path, {
                "title": title,
                "alt_title": alt_title,
                "artist": artist,
                "duration": duration,
                "webpage_url": tiktok_url,
            }
    except Exception as exc:
        logger.warning("TikTok API fallback failed: %s", exc)
        return None


async def probe_video_qualities(url: str) -> Dict[str, Any]:
    """
    Fast metadata-only probe returning available video resolutions (without downloading)
    for TikTok / YouTube / direct video links so the UI can show a quality dropdown.
    """
    clean_target = (url or "").strip()
    if not clean_target.startswith(("http://", "https://")):
        return {
            "title": "",
            "qualities": [{"value": "best", "label": "✨ Best Available (Auto)"}],
        }

    qualities: List[Dict[str, str]] = [
        {"value": "best", "label": "✨ Best Available (Auto)"}
    ]
    title = ""

    if "tiktok.com" in clean_target.lower():
        try:
            async with create_resilient_async_client(timeout=12.0) as client:
                resp = await client.get(
                    "https://www.tikwm.com/api/",
                    params={"url": clean_target, "hd": 1},
                )
                if resp.status_code == 200:
                    payload = resp.json()
                    data = payload.get("data") if isinstance(payload, dict) else None
                    if isinstance(data, dict) and data:
                        title = str(data.get("title") or "").strip()
                        hd_size = int(data.get("hd_size") or 0)
                        sd_size = int(data.get("size") or 0)
                        if data.get("hdplay"):
                            sz_txt = f" (~{hd_size / (1024 * 1024):.1f} MB)" if hd_size > 0 else ""
                            qualities.append({"value": "1080", "label": f"🎬 HD Original{sz_txt}"})
                        if data.get("play"):
                            sz_txt = f" (~{sd_size / (1024 * 1024):.1f} MB)" if sd_size > 0 else ""
                            qualities.append({"value": "540", "label": f"📱 Standard SD{sz_txt}"})
                        if len(qualities) > 1:
                            return {"title": title, "qualities": qualities}
        except Exception:
            pass

    def _probe_ytdlp() -> Dict[str, Any]:
        ydl_opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "nocheckcertificate": True,
            "geo_bypass": True,
            "socket_timeout": 12,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_target, download=False)
            if "entries" in info and info["entries"]:
                info = info["entries"][0]
            return info or {}

    try:
        info = await asyncio.to_thread(_probe_ytdlp)
        title = str(info.get("title") or title or "").strip()
        formats = info.get("formats") or []
        best_audio_bytes = 0
        for f in formats:
            if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none"):
                sz = int(f.get("filesize") or f.get("filesize_approx") or 0)
                if sz > best_audio_bytes:
                    best_audio_bytes = sz

        height_map: Dict[int, int] = {}
        for f in formats:
            vcodec = f.get("vcodec")
            h = f.get("height")
            if not h or vcodec in (None, "none"):
                continue
            h_int = int(h)
            if h_int < 144:
                continue
            sz = int(f.get("filesize") or f.get("filesize_approx") or 0)
            if sz > 0 and f.get("acodec") in (None, "none") and best_audio_bytes > 0:
                sz += best_audio_bytes
            if h_int not in height_map or sz > height_map[h_int]:
                height_map[h_int] = sz

        tag_names = {
            2160: "4K UHD",
            1440: "2K QHD",
            1080: "Full HD",
            720: "HD",
            480: "SD",
            360: "Low",
            240: "240p",
        }
        for h_val in sorted(height_map.keys(), reverse=True):
            if h_val not in (2160, 1440, 1080, 720, 480, 360, 240) and h_val < 300:
                continue
            sz = height_map[h_val]
            sz_txt = f" (~{sz / (1024 * 1024):.1f} MB)" if sz > 0 else ""
            tag = tag_names.get(h_val, f"{h_val}p")
            label = f"{h_val}p {tag}{sz_txt}" if tag != f"{h_val}p" else f"{h_val}p{sz_txt}"
            qualities.append({"value": str(h_val), "label": label})
    except Exception as exc:
        logger.debug("Quality probe fallback for %s: %s", clean_target, exc)

    if len(qualities) == 1:
        qualities.extend([
            {"value": "1080", "label": "🎬 1080p Full HD"},
            {"value": "720", "label": "📺 720p HD"},
            {"value": "480", "label": "📱 480p SD"},
            {"value": "360", "label": "⚡ 360p Fast"},
        ])

    return {"title": title, "qualities": qualities}


async def download_media_from_url(
    url: str,
    job_temp_dir: Path,
    prefix: str = "downloaded_media",
    audio_only: bool = False,
    progress_cb: Optional[Callable[[int, str], None]] = None,
    max_height: Optional[int] = None,
) -> Tuple[Path, Dict[str, Any]]:
    """
    Download a TikTok/YouTube song or video using yt-dlp (with automatic TikTok API fallback).
    - Supports full TikTok URLs (`tiktok.com/@.../video/...`), short TikTok URLs (`vt.tiktok.com/...`, `vm.tiktok.com/...`),
      TikTok photo/music links, YouTube URLs (`youtube.com/watch?v=...`, `youtu.be/...`),
      and plain text song queries via `ytsearch1:`.
    """
    clean_target = url.strip()
    is_tiktok = "tiktok.com" in clean_target.lower()

    if not clean_target.startswith(("http://", "https://")):
        clean_target = f"ytsearch1:{clean_target}"

    if progress_cb:
        progress_cb(5, "Resolving video link...")

    # For TikTok URLs, try our fast TikTok Music/Video extractor first so even TikTok
    # photo-mode posts and short vt.tiktok.com links work without challenge blocks!
    if is_tiktok:
        tk_result = await _fetch_tiktok_via_api_fallback(
            tiktok_url=clean_target,
            job_temp_dir=job_temp_dir,
            prefix=prefix,
            audio_only=audio_only,
            progress_cb=progress_cb,
            max_height=max_height,
        )
        if tk_result is not None:
            return tk_result

    output_template = str(job_temp_dir / f"{prefix}.%(ext)s")
    ffmpeg_bin = get_ffmpeg_binary()

    base_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    if audio_only:
        fmt_selector = "bestaudio[ext=m4a]/bestaudio/best[ext=mp4]/best"
    elif max_height and max_height > 0:
        fmt_selector = (
            f"best[height<={max_height}][ext=mp4]/"
            f"bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={max_height}]+bestaudio/"
            f"best[height<={max_height}]/"
            f"best[ext=mp4]/best"
        )
    else:
        fmt_selector = "best[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best"

    def _ydl_progress_hook(d: Dict[str, Any]) -> None:
        if not progress_cb:
            return
        st = d.get("status")
        if st == "downloading":
            tot = float(d.get("total_bytes") or d.get("total_bytes_estimate") or 0.0)
            dl = float(d.get("downloaded_bytes") or 0.0)
            if tot > 0:
                pct = min(92, max(10, int((dl / tot) * 85) + 10))
                progress_cb(
                    pct,
                    f"Downloading video: {pct}% ({dl / (1024 * 1024):.1f} / {tot / (1024 * 1024):.1f} MB)",
                )
            else:
                progress_cb(45, f"Downloading video... ({dl / (1024 * 1024):.1f} MB)")
        elif st == "finished":
            progress_cb(94, "Processing downloaded video...")

    ydl_opts: Dict[str, Any] = {
        "format": fmt_selector,
        "outtmpl": output_template,
        "noplaylist": True,
        "nocheckcertificate": True,
        "geo_bypass": True,
        "quiet": True,
        "no_warnings": True,
        "http_headers": base_headers,
        "socket_timeout": 25,
        "retries": 3,
        "progress_hooks": [_ydl_progress_hook],
    }
    if ffmpeg_bin and ffmpeg_bin != "ffmpeg":
        ydl_opts["ffmpeg_location"] = ffmpeg_bin

    def _download() -> Tuple[Path, Dict[str, Any]]:
        last_err: Optional[Exception] = None
        for attempt_fmt in (fmt_selector, "best"):
            opts = {**ydl_opts, "format": attempt_fmt}
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(clean_target, download=True)
                    if "entries" in info and info["entries"]:
                        info = info["entries"][0]

                    filename = ydl.prepare_filename(info)
                    mp4_candidate = Path(filename).with_suffix(".mp4")
                    matches = [
                        p
                        for p in job_temp_dir.glob(f"{prefix}.*")
                        if not p.name.endswith(".part")
                    ]
                    if Path(filename).exists():
                        final_path = Path(filename)
                    elif mp4_candidate.exists():
                        final_path = mp4_candidate
                    elif matches:
                        final_path = matches[0]
                    else:
                        raise RuntimeError(f"yt-dlp finished for {clean_target} but no file was found.")

                    is_tiktok_src = "tiktok.com" in clean_target.lower()
                    raw_track = (info.get("track") or "").strip()
                    raw_title = "" if is_tiktok_src else (info.get("title") or "").strip()
                    if _is_generic_tiktok_sound(raw_track):
                        raw_track = ""
                    metadata = {
                        "title": raw_track or raw_title,
                        "alt_title": "" if is_tiktok_src else (info.get("alt_title") or info.get("title") or ""),
                        "artist": (
                            (info.get("artist") or "")
                            if is_tiktok_src
                            else (
                                info.get("artist")
                                or info.get("creator")
                                or info.get("uploader")
                                or info.get("channel")
                                or ""
                            )
                        ),
                        "duration": float(info.get("duration") or 0.0),
                        "webpage_url": info.get("webpage_url") or clean_target,
                    }
                    return final_path, metadata
            except Exception as exc:
                last_err = exc
                logger.warning("yt-dlp attempt with format='%s' failed: %s", attempt_fmt, exc)

        raise RuntimeError(f"Could not fetch song/video from '{url}': {last_err}")

    media_path, metadata = await asyncio.to_thread(_download)
    logger.info("Downloaded media from %s to %s (metadata=%s)", url, media_path, metadata)
    return media_path, metadata


async def extract_audio_from_video(
    video_path: Path,
    job_temp_dir: Path,
    output_format: str = "wav",
    sample_rate: int = 16000,
    start_sec: float = 0.0,
    max_duration_sec: float = 0.0,
) -> Tuple[Path, float]:
    """
    Extract (and optionally trim) the audio track from the song source to:
      - 16kHz mono .wav (for Tier 3 AI Forced Alignment)
      - or high-bitrate .m4a / .mp3 (for audio preview & final video render)
    """
    audio_path = job_temp_dir / f"extracted_audio.{output_format}"
    ffmpeg_bin = get_ffmpeg_binary()

    cmd: List[str] = [ffmpeg_bin, "-y"]
    if start_sec > 0:
        cmd.extend(["-ss", f"{start_sec:.2f}"])
    if max_duration_sec > 0:
        cmd.extend(["-t", f"{max_duration_sec:.2f}"])

    cmd.extend(["-i", str(video_path.resolve()), "-vn"])

    if output_format == "wav":
        cmd.extend(["-acodec", "pcm_s16le", "-ac", "1", "-ar", str(sample_rate)])
    elif output_format == "m4a":
        cmd.extend(["-acodec", "aac", "-b:a", "192k"])
    else:
        cmd.extend(["-acodec", "libmp3lame", "-b:a", "192k"])

    cmd.extend(["-loglevel", "error", str(audio_path.resolve())])

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()

    if process.returncode != 0:
        err_msg = stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"FFmpeg audio extraction failed: {err_msg}")

    duration = await get_media_duration(audio_path)
    logger.info("Extracted audio (%s) to %s (duration=%.2fs)", output_format, audio_path, duration)
    return audio_path, duration


async def get_media_duration(media_path: Path) -> float:
    """
    Probe the media file duration in seconds using Python wave module, ffprobe,
    or ffmpeg stderr parsing.
    """
    if media_path.suffix.lower() == ".wav" and media_path.exists():
        try:
            import wave

            with wave.open(str(media_path), "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                if rate > 0 and frames > 0:
                    return round(frames / float(rate), 2)
        except Exception:
            pass

    ffprobe_bin = get_ffprobe_binary()
    if ffprobe_bin:
        cmd = [
            ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(media_path),
        ]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        if process.returncode == 0 and stdout:
            try:
                data = json.loads(stdout.decode("utf-8"))
                return float(data.get("format", {}).get("duration", 30.0))
            except (ValueError, KeyError):
                pass

    import re

    ffmpeg_bin = get_ffmpeg_binary()
    proc = await asyncio.create_subprocess_exec(
        ffmpeg_bin,
        "-i",
        str(media_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    err_text = stderr.decode("utf-8", errors="replace")
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err_text)
    if match:
        hrs, mins, secs = int(match.group(1)), int(match.group(2)), float(match.group(3))
        return round(hrs * 3600 + mins * 60 + secs, 2)

    return 30.0


async def identify_song_with_shazam_detailed(
    audio_path: Path,
    timeout_sec: float = 14.0,
) -> Optional[dict]:
    """
    Identify the exact Song Title, Artist, Full-Song Time Offset, Speed Skew, and embedded
    Shazam Lyrics directly from the audio waveform using Shazam audio fingerprinting.
    Uses `httpx` with IPv4 preference + `verify=False` so macOS/Windows SSL certificate quirks
    or dead IPv6 routes never block Shazam.
    """
    if not audio_path or not audio_path.exists():
        return None
    try:
        from shazamio import Shazam
        from shazamio.interfaces.client import HTTPClientInterface

        class _HttpxShazamClient(HTTPClientInterface):
            def __init__(self, client: httpx.AsyncClient) -> None:
                self._client = client

            async def request(self, method: str, url: str, **kwargs):
                proxy = kwargs.pop("proxy", None)
                _ = proxy
                resp = await self._client.request(method.upper(), url, **kwargs)
                if not resp.text or not resp.text.strip():
                    return {}
                try:
                    return resp.json()
                except Exception:
                    return {}

            async def request_text(self, url: str, *, content_type: str, **kwargs) -> str:
                kwargs.pop("proxy", None)
                resp = await self._client.get(url, **kwargs)
                return resp.text

        def _parse_shazam_response(res: dict, slice_start_sec: float = 0.0) -> Optional[dict]:
            track = (res or {}).get("track") or {}
            title = (track.get("title") or "").strip()
            artist = (track.get("subtitle") or "").strip()
            if not title:
                return None

            matches = (res or {}).get("matches") or []
            raw_offset = 0.0
            time_skew = 0.0
            freq_skew = 0.0
            if matches and isinstance(matches[0], dict):
                raw_offset = float(matches[0].get("offset") or 0.0)
                time_skew = float(matches[0].get("timeskew") or 0.0)
                freq_skew = float(matches[0].get("frequencyskew") or 0.0)

            # `raw_offset` is where the sampled slice begins inside the original studio track.
            # Subtract `slice_start_sec` to get where t=0.0s of our TikTok clip begins in the full song!
            clip_offset_sec = max(0.0, raw_offset - slice_start_sec * (1.0 + time_skew))

            shazam_lyrics = ""
            for sec in track.get("sections") or []:
                if isinstance(sec, dict) and (sec.get("type") or "").upper() == "LYRICS":
                    lines = sec.get("text") or []
                    if isinstance(lines, list) and lines:
                        shazam_lyrics = "\n".join(str(x).strip() for x in lines if str(x).strip())
                        break

            logger.info(
                "Shazam audio ID caught song: %r by %r (clip_offset=%.2fs, timeskew=%.4f, has_lyrics=%s)",
                title,
                artist,
                clip_offset_sec,
                time_skew,
                bool(shazam_lyrics),
            )
            return {
                "title": title,
                "artist": artist,
                "clip_offset_sec": round(clip_offset_sec, 3),
                "time_skew": round(time_skew, 5),
                "freq_skew": round(freq_skew, 5),
                "lyrics_text": shazam_lyrics,
            }

        async def _run() -> Optional[dict]:
            async with create_resilient_async_client(timeout=8.0) as hx_client:
                shazam = Shazam(http_client=_HttpxShazamClient(hx_client))
                try:
                    res = await shazam.recognize(str(audio_path), max_skew=0.12)
                    parsed = _parse_shazam_response(res, slice_start_sec=0.0)
                    if parsed:
                        return parsed
                except Exception as exc:
                    logger.debug("Shazam initial clip attempt failed: %s", exc)

                # If intro was instrumental/noisy, sample middle slice(s) of the clip
                dur = await get_media_duration(audio_path)
                if dur >= 14.0:
                    ffmpeg_bin = get_ffmpeg_binary()
                    for ratio in (0.35, 0.60):
                        slice_start = round(dur * ratio, 2)
                        if dur - slice_start < 5.0:
                            continue
                        mid_wav = audio_path.parent / f"_shazam_slice_{int(ratio * 100)}.wav"
                        proc = await asyncio.create_subprocess_exec(
                            ffmpeg_bin,
                            "-y",
                            "-ss",
                            f"{slice_start:.2f}",
                            "-t",
                            "10",
                            "-i",
                            str(audio_path.resolve()),
                            "-ar",
                            "44100",
                            "-ac",
                            "1",
                            "-loglevel",
                            "error",
                            str(mid_wav),
                        )
                        await proc.communicate()
                        if mid_wav.exists():
                            try:
                                res2 = await shazam.recognize(str(mid_wav), max_skew=0.12)
                                parsed2 = _parse_shazam_response(res2, slice_start_sec=slice_start)
                                if parsed2:
                                    return parsed2
                            except Exception as exc:
                                logger.debug("Shazam slice attempt failed: %s", exc)
                            finally:
                                mid_wav.unlink(missing_ok=True)
            return None

        return await asyncio.wait_for(_run(), timeout=timeout_sec)
    except Exception as exc:
        logger.warning("Shazam song identification unavailable/failed: %s", exc)
        return None


async def identify_song_with_shazam(
    audio_path: Path,
    timeout_sec: float = 12.0,
) -> Optional[Tuple[str, str]]:
    """
    Identify the exact Song Title & Artist directly from the audio waveform using Shazam audio fingerprinting.
    Works even when a TikTok/YouTube link says 'original sound', '#fyp', or is an unnamed upload.
    """
    info = await identify_song_with_shazam_detailed(audio_path, timeout_sec=timeout_sec)
    if info and info.get("title"):
        return info["title"], info.get("artist") or ""
    return None
