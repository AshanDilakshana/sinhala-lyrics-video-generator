import asyncio
import json
import logging
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from fastapi import UploadFile
import httpx
import yt_dlp

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
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True, verify=False, headers=headers) as client:
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
            video_url = data.get("play") or data.get("wmplay")

            # Choose audio stream if audio_only and music_url is available, else video stream
            download_target = (music_url or video_url) if audio_only else (video_url or music_url)
            if not download_target:
                return None

            if download_target.startswith("/"):
                download_target = f"https://www.tikwm.com{download_target}"

            ext = ".mp3" if (audio_only and music_url) else ".mp4"
            out_path = job_temp_dir / f"{prefix}{ext}"

            media_resp = await client.get(download_target)
            if media_resp.status_code != 200 or len(media_resp.content) < 1024:
                return None

            out_path.write_bytes(media_resp.content)

            music_title = (music_info.get("title") or "").strip()
            post_caption = (data.get("title") or "").strip()

            if _is_generic_tiktok_sound(music_title) and post_caption:
                title = post_caption
                alt_title = music_title
            else:
                title = music_title or post_caption or "TikTok Song"
                alt_title = post_caption if post_caption != title else ""

            artist = (
                music_info.get("author")
                or (data.get("author") or {}).get("nickname")
                or ""
            )
            duration = float(music_info.get("duration") or data.get("duration") or 0.0)

            logger.info("Downloaded TikTok song/media via API fallback: %s (title=%s, alt=%s)", out_path, title, alt_title)
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


async def download_media_from_url(
    url: str,
    job_temp_dir: Path,
    prefix: str = "downloaded_media",
    audio_only: bool = False,
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

    # For TikTok URLs, try our fast TikTok Music/Video extractor first so even TikTok
    # photo-mode posts and short vt.tiktok.com links work without challenge blocks!
    if is_tiktok:
        tk_result = await _fetch_tiktok_via_api_fallback(
            tiktok_url=clean_target,
            job_temp_dir=job_temp_dir,
            prefix=prefix,
            audio_only=audio_only,
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

    fmt_selector = (
        "bestaudio[ext=m4a]/bestaudio/best[ext=mp4]/best"
        if audio_only
        else "best[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best"
    )

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

                    metadata = {
                        "title": info.get("track") or info.get("title") or "",
                        "alt_title": info.get("alt_title") or info.get("title") or "",
                        "artist": (
                            info.get("artist")
                            or info.get("creator")
                            or info.get("uploader")
                            or info.get("channel")
                            or ""
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
    timeout_sec: float = 12.0,
) -> Optional[dict]:
    """
    Identify the exact Song Title, Artist, Full-Song Time Offset, Speed Skew, and embedded
    Shazam Lyrics directly from the audio waveform using Shazam audio fingerprinting.
    Uses `httpx` (with `verify=False`) so macOS/Windows SSL certificate quirks never block Shazam.
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
                return resp.json()

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
            async with httpx.AsyncClient(timeout=8.0, verify=False, follow_redirects=True) as hx_client:
                shazam = Shazam(http_client=_HttpxShazamClient(hx_client))
                res = await shazam.recognize(str(audio_path), max_skew=0.12)
                parsed = _parse_shazam_response(res, slice_start_sec=0.0)
                if parsed:
                    return parsed

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
