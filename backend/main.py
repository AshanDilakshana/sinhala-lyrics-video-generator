import asyncio
import json
import logging
import os
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

from backend.config import (
    DOWNLOADS_EXPORT_DIR,
    DRAFTS_EXPORT_DIR,
    FONTS_DIR,
    FRONTEND_ORIGIN,
    OUTPUT_DIR,
    SONGS_CACHE_DIR,
    TEMP_DIR,
)
from backend.models import (
    ColorGradePreset,
    FrameLayout,
    JobStage,
    JobStatusResponse,
    LoopMode,
    LyricLine,
    LyricsResult,
    LyricsSourceTier,
    StylePreset,
    VideoEditConfig,
)
from backend.services.font_service import (
    delete_custom_font,
    download_font_from_internet,
    extract_system_font_ttf,
    list_available_fonts,
    render_ffmpeg_font_preview_png,
    save_uploaded_font,
)
from backend.services.lyrics_service import (
    fetch_lrclib_lyrics,
    fetch_spotify_lyrics,
    lines_to_lrc,
    resolve_synced_lyrics,
)
from backend.services.sri_lanka_lyrics_service import fetch_sri_lankan_lyrics_by_title
from backend.services.media_service import (
    download_media_from_url,
    extract_audio_from_video,
    get_media_duration,
    save_uploaded_file,
    save_uploaded_visual_clips,
)
from backend.services.subtitle_service import generate_ass_subtitles
from backend.services.video_service import (
    cleanup_temp_directory,
    render_aesthetic_lyrics_video,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("backend.main")

app = FastAPI(
    title="TikTok Aesthetic AI Lyrics Video Editor API",
    description=(
        "Full-stack TikTok / Musical.ly Video Editor with Sinhala Vocal-Formant DP Aligner, "
        "Live Audio-to-Caption Sync Studio, Custom Sinhala Font Manager, and FFmpeg Renderer."
    ),
    version="3.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN, "http://localhost:3000", "http://127.0.0.1:3000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

JOBS: Dict[str, JobStatusResponse] = {}
JOBS_STATE_FILE = TEMP_DIR / "jobs_state.json"


def _persist_jobs_state() -> None:
    try:
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        # Keep the 40 most recent jobs
        recent_items = list(JOBS.items())[-40:]
        serializable = {jid: job.model_dump(mode="json") for jid, job in recent_items}
        JOBS_STATE_FILE.write_text(json.dumps(serializable, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.debug("Could not persist jobs state: %s", exc)


def _load_jobs_state() -> None:
    if not JOBS_STATE_FILE.exists():
        return
    try:
        raw = json.loads(JOBS_STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return
        changed = False
        for jid, data in raw.items():
            if not isinstance(data, dict):
                continue
            job = JobStatusResponse(**data)
            if job.stage not in (JobStage.COMPLETED, JobStage.FAILED):
                job.stage = JobStage.FAILED
                job.progress = 100
                job.message = "Server reloaded while job was running."
                job.error = "Backend server restarted while job was running. Please click Render again."
                changed = True
            JOBS[jid] = job
        if changed:
            _persist_jobs_state()
    except Exception as exc:
        logger.debug("Could not load persisted jobs state: %s", exc)


_load_jobs_state()


def _update_job(
    job_id: str,
    stage: JobStage,
    progress: int,
    message: str,
    **kwargs,
) -> JobStatusResponse:
    current = JOBS.get(job_id)
    payload = current.model_dump() if current else {"job_id": job_id}
    payload.update(
        {
            "stage": stage,
            "progress": progress,
            "message": message,
            **kwargs,
        }
    )
    updated = JobStatusResponse(**payload)
    JOBS[job_id] = updated
    _persist_jobs_state()
    return updated


# ============================================================================
# INSTANT SONG FETCHER, AUDIO PREVIEW & SINHALA CAPTION AUTO-SYNC ENDPOINTS
# ============================================================================

@app.post("/api/fetch-song")
async def fetch_song_endpoint(
    request: Request,
    song_url: Optional[str] = Form(default=None),
    song_file: Optional[UploadFile] = File(default=None),
    song_name: str = Form(default=""),
    artist_name: str = Form(default=""),
    manual_lyrics: str = Form(default=""),
    language: str = Form(default="si"),
    audio_start_sec: float = Form(default=0.0),
    audio_max_duration_sec: float = Form(default=0.0),
):
    """
    Fetch a song from a TikTok URL, YouTube URL, uploaded audio file, or Song Title search query.
    Extracts playable M4A preview audio AND 16kHz WAV waveform, detects title/artist/duration,
    and immediately runs Sinhala Vocal-Formant + Breath-Dip Forced Alignment on `manual_lyrics`
    so the user gets frame-accurate synced captions right in the browser preview!
    """
    target_query = (song_url or "").strip()
    if not target_query and song_name.strip():
        target_query = f"{song_name.strip()} {artist_name.strip()}".strip()

    has_upload = bool(song_file and song_file.filename)
    if not target_query and not has_upload:
        raise HTTPException(
            status_code=400,
            detail="Please enter a TikTok link, YouTube link, upload an audio file, or enter a Song Title.",
        )

    song_id = uuid.uuid4().hex[:10]
    song_dir = SONGS_CACHE_DIR / song_id
    song_dir.mkdir(parents=True, exist_ok=True)

    try:
        meta = {}
        if has_upload and song_file:
            downloaded_path = await save_uploaded_file(song_file, song_dir, prefix="raw_song")
            meta = {"title": Path(song_file.filename or "Uploaded Song").stem, "artist": ""}
        else:
            downloaded_path, meta = await download_media_from_url(
                url=target_query,
                job_temp_dir=song_dir,
                prefix="raw_song",
                audio_only=True,
            )

        if await request.is_disconnected():
            shutil.rmtree(song_dir, ignore_errors=True)
            raise HTTPException(status_code=499, detail="Song fetching stopped by user.")

        raw_duration = await get_media_duration(downloaded_path)
        safe_start_sec = (
            max(0.0, min(audio_start_sec, max(0.0, raw_duration - 1.5)))
            if raw_duration > 1.5
            else 0.0
        )

        m4a_path, duration = await extract_audio_from_video(
            video_path=downloaded_path,
            job_temp_dir=song_dir,
            output_format="m4a",
            start_sec=safe_start_sec,
            max_duration_sec=audio_max_duration_sec,
        )
        wav_path, _ = await extract_audio_from_video(
            video_path=downloaded_path,
            job_temp_dir=song_dir,
            output_format="wav",
            sample_rate=16000,
            start_sec=safe_start_sec,
            max_duration_sec=audio_max_duration_sec,
        )

        if await request.is_disconnected():
            shutil.rmtree(song_dir, ignore_errors=True)
            raise HTTPException(status_code=499, detail="Song fetching stopped by user.")

        resolved_title = song_name.strip() or meta.get("title") or "Fetched Song"
        resolved_artist = artist_name.strip() or meta.get("artist") or ""
        alt_title = meta.get("alt_title") or ""

        # Run 3-Tier Lyrics (with Shazam song catching & vocal-anchored alignment)
        lyrics_res = await resolve_synced_lyrics(
            audio_path=wav_path,
            job_temp_dir=song_dir,
            total_duration=duration,
            song_name=resolved_title,
            artist_name=resolved_artist,
            manual_lyrics=manual_lyrics,
            language=language,
            raw_audio_path=downloaded_path,
            alt_title=alt_title,
        )

        if await request.is_disconnected():
            shutil.rmtree(song_dir, ignore_errors=True)
            raise HTTPException(status_code=499, detail="Song fetching stopped by user.")

        final_title = lyrics_res.detected_title or resolved_title
        final_artist = lyrics_res.detected_artist or resolved_artist

        # Prune stale song cache folders when count exceeds 20 (preserve draft_* bundles)
        try:
            cached_dirs = [
                d for d in SONGS_CACHE_DIR.iterdir()
                if d.is_dir() and d.name != song_id and not d.name.startswith("draft_")
            ]
            if len(cached_dirs) > 20:
                cached_dirs.sort(key=lambda d: d.stat().st_mtime)
                for stale_dir in cached_dirs[: len(cached_dirs) - 20]:
                    shutil.rmtree(stale_dir, ignore_errors=True)
        except Exception:
            pass

        return {
            "status": "ok",
            "song_id": song_id,
            "title": final_title,
            "artist": final_artist,
            "duration": round(duration, 2),
            "raw_duration": round(raw_duration, 2),
            "audio_start_sec": round(safe_start_sec, 2),
            "audio_preview_url": f"/api/song-preview/{song_id}",
            "fetched_lyrics": lyrics_res.full_lyrics_text or "\n".join(line.text for line in lyrics_res.lines),
            "aligned_lines": [line.model_dump() for line in lyrics_res.lines],
            "lyrics_tier": lyrics_res.tier,
            "lyrics_source": lyrics_res.lyrics_source,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to fetch song for %r", target_query)
        shutil.rmtree(song_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=f"Song fetch failed: {exc}")


@app.post("/api/sync-lyrics")
async def sync_lyrics_to_cached_song_endpoint(
    cached_song_id: str = Form(...),
    manual_lyrics: str = Form(default=""),
    song_name: str = Form(default=""),
    artist_name: str = Form(default=""),
    language: str = Form(default="si"),
    start_line_index: int = Form(default=-1),
):
    """
    Re-run the Sinhala Vocal-Formant + Breath-Dip Forced Aligner whenever the user edits
    their Sinhala lyrics text or selects the starting lyric line, without re-downloading the song!
    """
    safe_id = Path(cached_song_id).name
    song_dir = SONGS_CACHE_DIR / safe_id
    wav_path = song_dir / "extracted_audio.wav"
    if not wav_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Cached song audio not found. Please click 'Fetch Song Audio' first.",
        )

    from backend.services.media_service import get_media_duration

    raw_candidates = sorted(song_dir.glob("raw_song*"))
    raw_audio_path = raw_candidates[0] if raw_candidates else wav_path

    duration = await get_media_duration(wav_path)
    lyrics_res = await resolve_synced_lyrics(
        audio_path=wav_path,
        job_temp_dir=song_dir,
        total_duration=duration,
        song_name=song_name,
        artist_name=artist_name,
        manual_lyrics=manual_lyrics,
        language=language,
        raw_audio_path=raw_audio_path,
        start_line_index=start_line_index,
        force_refresh_asr=True,
    )
    return {
        "status": "ok",
        "duration": round(duration, 2),
        "title": lyrics_res.detected_title or song_name,
        "artist": lyrics_res.detected_artist or artist_name,
        "fetched_lyrics": lyrics_res.full_lyrics_text or "\n".join(line.text for line in lyrics_res.lines),
        "aligned_lines": [line.model_dump() for line in lyrics_res.lines],
        "lrc_content": lyrics_res.raw_lrc,
        "lyrics_tier": lyrics_res.tier,
        "lyrics_source": lyrics_res.lyrics_source,
    }


@app.post("/api/trim-cached-song")
async def trim_cached_song_endpoint(
    cached_song_id: str = Form(...),
    audio_start_sec: float = Form(default=0.0),
    audio_max_duration_sec: float = Form(default=0.0),
    manual_lyrics: str = Form(default=""),
    song_name: str = Form(default=""),
    artist_name: str = Form(default=""),
    language: str = Form(default="si"),
):
    """
    Cut any custom start/duration range out of an already-downloaded cached song (e.g. a
    1.5-minute song started from 0:32) WITHOUT re-downloading the URL, then re-sync captions.
    """
    safe_id = Path(cached_song_id).name
    song_dir = SONGS_CACHE_DIR / safe_id
    if not song_dir.exists():
        raise HTTPException(
            status_code=404,
            detail="Cached song not found. Please click 'Fetch & Auto-Sync Song' first.",
        )

    raw_candidates = sorted(song_dir.glob("raw_song*"))
    raw_source = raw_candidates[0] if raw_candidates else (song_dir / "extracted_audio.m4a")
    if not raw_source.exists():
        raise HTTPException(status_code=404, detail="Cached source audio missing.")

    raw_duration = await get_media_duration(raw_source)
    safe_start_sec = (
        max(0.0, min(audio_start_sec, max(0.0, raw_duration - 1.5)))
        if raw_duration > 1.5
        else 0.0
    )
    safe_max_dur = max(0.0, audio_max_duration_sec)

    # Clear cached ASR window JSON files and vocal-isolated WAVs because the audio slice boundaries changed
    for stale_file in (*song_dir.glob("asr_windows_*.json"), song_dir / "vocal_isolated_ctc.wav", song_dir / "vocal_filtered_asr.wav"):
        try:
            stale_file.unlink(missing_ok=True)
        except Exception:
            pass

    _, duration = await extract_audio_from_video(
        video_path=raw_source,
        job_temp_dir=song_dir,
        output_format="m4a",
        start_sec=safe_start_sec,
        max_duration_sec=safe_max_dur,
    )
    wav_path, _ = await extract_audio_from_video(
        video_path=raw_source,
        job_temp_dir=song_dir,
        output_format="wav",
        sample_rate=16000,
        start_sec=safe_start_sec,
        max_duration_sec=safe_max_dur,
    )

    lyrics_res = await resolve_synced_lyrics(
        audio_path=wav_path,
        job_temp_dir=song_dir,
        total_duration=duration,
        song_name=song_name.strip(),
        artist_name=artist_name.strip(),
        manual_lyrics=manual_lyrics,
        language=language,
        raw_audio_path=raw_source,
    )

    cache_bust = uuid.uuid4().hex[:6]
    return {
        "status": "ok",
        "song_id": safe_id,
        "title": lyrics_res.detected_title or song_name,
        "artist": lyrics_res.detected_artist or artist_name,
        "duration": round(duration, 2),
        "raw_duration": round(raw_duration, 2),
        "audio_start_sec": round(safe_start_sec, 2),
        "audio_preview_url": f"/api/song-preview/{safe_id}?v={cache_bust}",
        "fetched_lyrics": lyrics_res.full_lyrics_text or "\n".join(line.text for line in lyrics_res.lines),
        "aligned_lines": [line.model_dump() for line in lyrics_res.lines],
        "lyrics_tier": lyrics_res.tier,
        "lyrics_source": lyrics_res.lyrics_source,
    }


@app.post("/api/search-sinhala-lyrics")
async def search_sinhala_lyrics_endpoint(
    query: str = Form(...),
    artist_name: str = Form(default=""),
):
    """
    Search Sinhala song lyrics by Song Title (Sinhala Unicode or Singlish/English),
    Artist, or direct LyriHub / Lyrics-LK / Smule URL across lyrihub.lk & lyrics-lk.com.
    """
    clean_q = (query or "").strip()
    if not clean_q:
        raise HTTPException(status_code=400, detail="Please enter a song title or lyrics URL.")

    best, candidates = await fetch_sri_lankan_lyrics_by_title(
        query=clean_q,
        artist_name=artist_name.strip(),
        max_candidates=5,
    )
    if not best:
        raise HTTPException(
            status_code=404,
            detail=f"No Sinhala lyrics found on LyriHub.lk / Lyrics-LK.com for '{clean_q}'.",
        )

    return {
        "status": "ok",
        "best_match": best,
        "candidates": candidates,
    }


@app.get("/api/song-preview/{song_id}")
async def serve_song_preview(song_id: str):
    """Stream the extracted M4A audio for instant playback & live caption sync in Track 2."""
    safe_id = Path(song_id).name
    m4a_file = SONGS_CACHE_DIR / safe_id / "extracted_audio.m4a"
    if not m4a_file.exists():
        raise HTTPException(status_code=404, detail="Song preview audio not found.")
    return FileResponse(
        path=str(m4a_file),
        media_type="audio/mp4",
        filename=f"song_{safe_id}.m4a",
        headers={"Cache-Control": "no-store"},
    )


# ============================================================================
# SINHALA & CUSTOM FONT MANAGER ENDPOINTS
# ============================================================================

@app.get("/api/fonts")
async def get_fonts_endpoint():
    return list_available_fonts()


@app.post("/api/fonts/upload")
async def upload_font_endpoint(font_file: UploadFile = File(...)):
    try:
        saved_font = await save_uploaded_font(font_file)
        return {"status": "ok", "font": saved_font, "all_fonts": list_available_fonts()}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/fonts/download")
async def download_font_endpoint(
    catalog_id: Optional[str] = Form(default=None),
    font_url: Optional[str] = Form(default=None),
):
    try:
        downloaded = await download_font_from_internet(font_url=font_url, catalog_id=catalog_id)
        return {"status": "ok", "font": downloaded, "all_fonts": list_available_fonts()}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/fonts/{font_identifier}")
async def delete_font_endpoint(font_identifier: str):
    try:
        deleted_files = await asyncio.to_thread(delete_custom_font, font_identifier)
        return {
            "status": "ok",
            "deleted": deleted_files,
            "all_fonts": list_available_fonts(),
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/fonts/file/{filename}")
async def serve_font_file(filename: str):
    safe_path = (FONTS_DIR / Path(filename).name).resolve()
    if not safe_path.exists() or safe_path.parent != FONTS_DIR.resolve():
        raise HTTPException(status_code=404, detail="Font file not found.")
    media_type = "font/otf" if safe_path.suffix.lower() == ".otf" else "font/ttf"
    return FileResponse(path=str(safe_path), media_type=media_type, filename=safe_path.name)


@app.get("/api/fonts/system/{slug}")
async def serve_system_font_file(slug: str):
    ttf_bytes = await asyncio.to_thread(extract_system_font_ttf, Path(slug).name)
    if not ttf_bytes:
        raise HTTPException(status_code=404, detail="System font not found.")
    return Response(
        content=ttf_bytes,
        media_type="font/ttf",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/fonts/render-preview")
async def render_font_preview_endpoint(
    font_family: str = "Sinhala Sangam MN",
    sample_text: str = "මගේ අභියස ලෝකයක් තියා",
    font_size: int = 44,
    font_thickness: float = 0.0,
    font_brightness: float = 1.0,
    primary_color_hex: str = "#FFFFFF",
    outline_width: float = 1.8,
    shadow_depth: float = 2.8,
    bold_text: bool = True,
    italic_text: bool = False,
):
    """
    Renders a real 1-frame FFmpeg + libass + HarfBuzz PNG preview of the selected Sinhala/custom font.
    """
    try:
        png_bytes = await asyncio.to_thread(
            render_ffmpeg_font_preview_png,
            font_family=font_family,
            sample_text=sample_text,
            font_size=font_size,
            font_thickness=font_thickness,
            font_brightness=font_brightness,
            primary_color_hex=primary_color_hex,
            outline_width=outline_width,
            shadow_depth=shadow_depth,
            bold_text=bold_text,
            italic_text=italic_text,
        )
        return Response(content=png_bytes, media_type="image/png")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ============================================================================
# VIDEO EDITOR & LYRICS PIPELINE
# ============================================================================

async def execute_video_pipeline(
    job_id: str,
    job_temp_dir: Path,
    visual_clip_paths: List[Path],
    visual_url: Optional[str],
    song_url: Optional[str],
    cached_song_id: Optional[str],
    uploaded_song_path: Optional[Path],
    song_name: str,
    artist_name: str,
    manual_lyrics: str,
    custom_lines_json: str,
    language: str,
    edit_config: VideoEditConfig,
    is_draft: bool = False,
    source_draft_filename: str = "",
) -> JobStatusResponse:
    try:
        _update_job(
            job_id,
            stage=JobStage.EXTRACTING_MEDIA,
            progress=12,
            message="Step A: Preparing song audio & normalizing AI animated clips...",
        )

        inferred_title = song_name.strip()
        inferred_artist = artist_name.strip()
        inferred_alt_title = ""

        song_source_path: Optional[Path] = None
        if cached_song_id:
            cached_m4a = SONGS_CACHE_DIR / Path(cached_song_id).name / "extracted_audio.m4a"
            if cached_m4a.exists():
                song_source_path = cached_m4a

        if song_source_path is None and uploaded_song_path and uploaded_song_path.exists():
            song_source_path = uploaded_song_path
        elif song_source_path is None and song_url and song_url.strip():
            song_source_path, meta = await download_media_from_url(
                url=song_url.strip(),
                job_temp_dir=job_temp_dir,
                prefix="song_source",
                audio_only=True,
            )
            if not inferred_title and meta.get("title"):
                inferred_title = meta["title"]
            if not inferred_artist and meta.get("artist"):
                inferred_artist = meta["artist"]
            if meta.get("alt_title"):
                inferred_alt_title = meta["alt_title"]
        elif song_source_path is None and inferred_title:
            query = f"{inferred_title} {inferred_artist}".strip()
            song_source_path, _ = await download_media_from_url(
                url=query,
                job_temp_dir=job_temp_dir,
                prefix="song_search_source",
                audio_only=True,
            )

        resolved_visual_clips: List[Path] = list(visual_clip_paths)
        if not resolved_visual_clips and visual_url and visual_url.strip():
            dl_visual, v_meta = await download_media_from_url(
                url=visual_url.strip(),
                job_temp_dir=job_temp_dir,
                prefix="visual_source",
                audio_only=False,
            )
            resolved_visual_clips.append(dl_visual)
            if not inferred_title and v_meta.get("title"):
                inferred_title = v_meta["title"]
            if not inferred_alt_title and v_meta.get("alt_title"):
                inferred_alt_title = v_meta["alt_title"]

        if not resolved_visual_clips and song_url and song_url.strip():
            dl_video, _ = await download_media_from_url(
                url=song_url.strip(),
                job_temp_dir=job_temp_dir,
                prefix="fallback_visual",
                audio_only=False,
            )
            resolved_visual_clips.append(dl_video)

        if not resolved_visual_clips:
            raise ValueError("Please upload at least one AI animated video clip (or provide a video URL).")

        if song_source_path is None:
            song_source_path = resolved_visual_clips[0]

        # Avoid double-trimming if using already-trimmed cached song audio
        trim_start = 0.0 if cached_song_id else edit_config.audio_start_sec
        trim_max = 0.0 if cached_song_id else edit_config.audio_max_duration_sec

        wav_audio_path, total_duration = await extract_audio_from_video(
            video_path=song_source_path,
            job_temp_dir=job_temp_dir,
            output_format="wav",
            sample_rate=16000,
            start_sec=trim_start,
            max_duration_sec=trim_max,
        )
        master_audio_path, _ = await extract_audio_from_video(
            video_path=song_source_path,
            job_temp_dir=job_temp_dir,
            output_format="m4a",
            start_sec=trim_start,
            max_duration_sec=trim_max,
        )

        # STEP B: 3-Tier Lyrics & Timing Engine (or Custom Synced Timeline Edits)
        lyrics_result: Optional[LyricsResult] = None
        if custom_lines_json and custom_lines_json.strip():
            try:
                raw_list = json.loads(custom_lines_json)
                parsed_custom = [LyricLine(**item) for item in raw_list if item.get("text", "").strip()]
                # Guard against stale custom_lines_json only if manual_lyrics was completely replaced
                # with different lyrics text (mid-song slices & line edits stay valid!).
                clean_manual_lines = [
                    l.strip()
                    for l in manual_lyrics.splitlines()
                    if l.strip() and not (l.strip().startswith("[") and l.strip().endswith("]"))
                ]
                custom_texts = [c.text.strip() for c in parsed_custom]
                manual_set = set(clean_manual_lines)
                overlap_count = sum(1 for t in custom_texts if t in manual_set)
                is_stale = (
                    bool(clean_manual_lines)
                    and len(clean_manual_lines) >= 3
                    and len(custom_texts) >= 2
                    and overlap_count == 0
                )
                if parsed_custom and not is_stale:
                    lyrics_result = LyricsResult(
                        tier=LyricsSourceTier.TIER_3_ALIGNMENT,
                        lines=parsed_custom,
                        raw_lrc=lines_to_lrc(parsed_custom),
                    )
                elif is_stale:
                    logger.info(
                        "Ignoring stale custom_lines_json (%d lines) because manual_lyrics changed (%d lines).",
                        len(custom_texts),
                        len(clean_manual_lines),
                    )
            except Exception as exc:
                logger.warning("Could not parse custom_lines_json, falling back to engine: %s", exc)

        if lyrics_result is None:
            if manual_lyrics.strip():
                _update_job(
                    job_id,
                    stage=JobStage.ALIGNING_AUDIO,
                    progress=38,
                    message="Step B (Tier 3): Syncing Sinhala captions to vocal phrases & breath dips...",
                    song_duration=total_duration,
                )
            else:
                _update_job(
                    job_id,
                    stage=JobStage.FETCHING_LYRICS,
                    progress=35,
                    message=f"Step B: Searching synced lyrics (LRCLIB -> Spotify -> AI Alignment) for '{inferred_title or 'Song'}'...",
                    song_duration=total_duration,
                )

            lyrics_result = await resolve_synced_lyrics(
                audio_path=wav_audio_path,
                job_temp_dir=job_temp_dir,
                total_duration=total_duration,
                song_name=inferred_title,
                artist_name=inferred_artist,
                manual_lyrics=manual_lyrics,
                language=language,
                raw_audio_path=song_source_path,
                alt_title=inferred_alt_title,
            )

        # STEP C: Subtitle Generation with Custom Font & Per-Word Onset Timings (.ass)
        _update_job(
            job_id,
            stage=JobStage.GENERATING_SUBTITLES,
            progress=65,
            message=(
                f"Step C: Building .ass subtitles"
                f"{f' for {lyrics_result.detected_title}' if lyrics_result.detected_title else ''}"
                f" ({lyrics_result.lyrics_source or lyrics_result.tier.value}, {len(lyrics_result.lines)} lines)..."
            ),
            lyrics_tier=lyrics_result.tier,
            detected_title=lyrics_result.detected_title,
            detected_artist=lyrics_result.detected_artist,
            lyrics_source=lyrics_result.lyrics_source,
            lrc_content=lyrics_result.raw_lrc,
            lyrics_preview=lyrics_result.lines,
            song_duration=total_duration,
        )

        ass_path = job_temp_dir / "subtitles.ass"
        await asyncio.to_thread(
            generate_ass_subtitles,
            lines=lyrics_result.lines,
            output_ass_path=ass_path,
            preset=edit_config.style_preset,
            frame_layout=edit_config.frame_layout,
            watermark_text=edit_config.watermark_text,
            total_duration=total_duration,
            play_res_x=1080,
            play_res_y=1920,
            edit_config=edit_config,
        )

        # STEP D: Aesthetic Video Editing & Rendering (FFmpeg)
        _update_job(
            job_id,
            stage=JobStage.RENDERING_VIDEO,
            progress=82,
            message=(
                "Step D: Rendering Draft 9:16 video to draft folder..."
                if is_draft
                else "Step D: Rendering 9:16 card, custom Sinhala font, color grade, boomerang loop & audio..."
            ),
        )

        render_target_path = (
            job_temp_dir / f"draft_video_{job_id}.mp4"
            if is_draft
            else OUTPUT_DIR / f"lyrics_video_{job_id}.mp4"
        )
        await render_aesthetic_lyrics_video(
            visual_clips=resolved_visual_clips,
            song_audio_path=master_audio_path,
            ass_subtitle_path=ass_path,
            output_video_path=render_target_path,
            job_temp_dir=job_temp_dir,
            target_duration=total_duration,
            edit_config=edit_config,
        )

        effective_export_title = (
            song_name.strip()
            or (lyrics_result.detected_title if lyrics_result and lyrics_result.detected_title else "")
            or inferred_title
        )

        if is_draft:
            draft_saved = _save_render_to_dir(
                render_target_path,
                DRAFTS_EXPORT_DIR,
                effective_export_title,
                job_id,
                move=True,
            )
            if not draft_saved:
                raise RuntimeError(f"Could not save draft video into '{DRAFTS_EXPORT_DIR}'.")

            _save_draft_project_bundle(
                job_id=job_id,
                draft_mp4_path=draft_saved,
                visual_clips=resolved_visual_clips,
                master_audio_path=master_audio_path,
                wav_audio_path=wav_audio_path,
                song_source_path=song_source_path,
                cached_song_id=cached_song_id,
                song_url=song_url,
                song_name=effective_export_title,
                artist_name=artist_name or (lyrics_result.detected_artist if lyrics_result else "") or inferred_artist,
                language=language,
                manual_lyrics=(
                    lyrics_result.full_lyrics_text
                    if (lyrics_result and lyrics_result.full_lyrics_text)
                    else manual_lyrics
                ),
                lyrics_result=lyrics_result,
                total_duration=total_duration,
                edit_config=edit_config,
            )
            if (
                source_draft_filename
                and source_draft_filename.strip()
                and Path(source_draft_filename).name != draft_saved.name
            ):
                _delete_draft_file_if_exists(source_draft_filename)

            done_msg = f"Draft video & project settings saved to Downloads/{DOWNLOADS_EXPORT_DIR.name}/draft/{draft_saved.name}"
            return _update_job(
                job_id,
                stage=JobStage.COMPLETED,
                progress=100,
                message=done_msg,
                is_draft=True,
                output_filename=draft_saved.name,
                video_url=f"/api/rendered-videos/file/draft/{quote(draft_saved.name)}",
            )

        downloads_copy = _copy_render_to_downloads(render_target_path, effective_export_title, job_id)
        removed_draft_name: Optional[str] = None
        if source_draft_filename and source_draft_filename.strip():
            removed_draft_name = _delete_draft_file_if_exists(source_draft_filename)

        done_msg = (
            f"TikTok video rendered & saved to Downloads/{DOWNLOADS_EXPORT_DIR.name}/{downloads_copy.name}"
            if downloads_copy
            else "TikTok aesthetic lyrics video rendered with synced Sinhala captions!"
        )
        if removed_draft_name:
            done_msg += " (Draft removed from draft folder)"

        return _update_job(
            job_id,
            stage=JobStage.COMPLETED,
            progress=100,
            message=done_msg,
            is_draft=False,
            output_filename=downloads_copy.name if downloads_copy else render_target_path.name,
            promoted_from_draft=removed_draft_name,
            video_url=f"/api/download/{job_id}",
        )

    except Exception as exc:
        logger.exception("Pipeline failed for job %s", job_id)
        return _update_job(
            job_id,
            stage=JobStage.FAILED,
            progress=100,
            message="Failed to process video.",
            error=str(exc),
        )
    finally:
        cleanup_temp_directory(job_temp_dir)


@app.post("/process-video", response_model=JobStatusResponse)
@app.post("/api/process-video", response_model=JobStatusResponse)
async def process_video_endpoint(
    background_tasks: BackgroundTasks,
    visual_files: Optional[List[UploadFile]] = File(default=None),
    video_file: Optional[UploadFile] = File(default=None),
    visual_url: Optional[str] = Form(default=None),
    song_url: Optional[str] = Form(default=None),
    video_url: Optional[str] = Form(default=None),
    cached_song_id: Optional[str] = Form(default=None),
    song_file: Optional[UploadFile] = File(default=None),
    song_name: str = Form(default=""),
    artist_name: str = Form(default=""),
    manual_lyrics: str = Form(default=""),
    custom_lines_json: str = Form(default=""),
    language: str = Form(default="si"),
    style_preset: StylePreset = Form(default=StylePreset.RW_AESTHETIC_SINHALA),
    frame_layout: FrameLayout = Form(default=FrameLayout.ROUNDED_SQUARE_CARD),
    color_grade: ColorGradePreset = Form(default=ColorGradePreset.GOLDEN_SUNSET),
    font_family: str = Form(default="Sinhala Sangam MN"),
    font_size: int = Form(default=40),
    font_thickness: float = Form(default=0.0),
    font_brightness: float = Form(default=1.35),
    primary_color_hex: str = Form(default="#FFFFFF"),
    outline_width: float = Form(default=1.8),
    shadow_depth: float = Form(default=2.8),
    text_pos_y_percent: int = Form(default=50),
    bold_text: bool = Form(default=True),
    italic_text: bool = Form(default=False),
    lyrics_offset_sec: float = Form(default=0.0),
    clip_trim_start: float = Form(default=0.0),
    clip_trim_end: float = Form(default=0.0),
    clip_segments_json: str = Form(default=""),
    loop_mode: LoopMode = Form(default=LoopMode.CROSSFADE),
    video_zoom: float = Form(default=1.0),
    mirror_flip: bool = Form(default=False),
    brightness: float = Form(default=-0.3),
    contrast: float = Form(default=1.0),
    saturation: float = Form(default=1.0),
    vignette: bool = Form(default=False),
    blur_transition_interval: float = Form(default=0.0),
    clip_speed: float = Form(default=1.0),
    corner_radius: int = Form(default=48),
    watermark_text: str = Form(default="The_Black Hart 🖤"),
    watermark_size: int = Form(default=15),
    watermark_pos_x_percent: int = Form(default=50),
    watermark_pos_y_percent: int = Form(default=98),
    audio_start_sec: float = Form(default=0.0),
    audio_max_duration_sec: float = Form(default=0.0),
    fade_in_out: bool = Form(default=True),
    audio_fade: bool = Form(default=True),
    is_draft: bool = Form(default=False),
    source_draft_filename: str = Form(default=""),
    async_mode: bool = Form(default=True),
):
    effective_song_url = (song_url or video_url or "").strip() or None
    effective_visual_url = (visual_url or "").strip() or None

    has_visual_uploads = bool(visual_files and any(f.filename for f in visual_files)) or bool(
        video_file and video_file.filename
    )

    if not has_visual_uploads and not effective_visual_url and not effective_song_url:
        raise HTTPException(
            status_code=400,
            detail="Please upload your AI animated video clip(s) and provide a TikTok/YouTube song link.",
        )

    job_id = uuid.uuid4().hex[:12]
    job_temp_dir = TEMP_DIR / job_id
    job_temp_dir.mkdir(parents=True, exist_ok=True)

    saved_visual_clips: List[Path] = []
    if visual_files:
        saved_visual_clips.extend(await save_uploaded_visual_clips(visual_files, job_temp_dir))
    if video_file and video_file.filename:
        saved_visual_clips.append(
            await save_uploaded_file(video_file, job_temp_dir, prefix="single_visual")
        )

    saved_song_path: Optional[Path] = None
    if song_file and song_file.filename:
        saved_song_path = await save_uploaded_file(song_file, job_temp_dir, prefix="uploaded_song")

    edit_config = VideoEditConfig(
        frame_layout=frame_layout,
        color_grade=color_grade,
        style_preset=style_preset,
        font_family=font_family,
        font_size=font_size,
        font_thickness=font_thickness,
        font_brightness=font_brightness,
        primary_color_hex=primary_color_hex,
        outline_width=outline_width,
        shadow_depth=shadow_depth,
        text_pos_y_percent=text_pos_y_percent,
        bold_text=bold_text,
        italic_text=italic_text,
        lyrics_offset_sec=lyrics_offset_sec,
        clip_trim_start=clip_trim_start,
        clip_trim_end=clip_trim_end,
        clip_segments_json=clip_segments_json,
        loop_mode=loop_mode,
        video_zoom=video_zoom,
        mirror_flip=mirror_flip,
        brightness=brightness,
        contrast=contrast,
        saturation=saturation,
        vignette=vignette,
        blur_transition_interval=blur_transition_interval,
        clip_speed=clip_speed,
        corner_radius=corner_radius,
        watermark_text=watermark_text,
        watermark_size=watermark_size,
        watermark_pos_x_percent=watermark_pos_x_percent,
        watermark_pos_y_percent=watermark_pos_y_percent,
        audio_start_sec=audio_start_sec,
        audio_max_duration_sec=audio_max_duration_sec,
        fade_in_out=fade_in_out,
        audio_fade=audio_fade,
    )

    initial_status = _update_job(
        job_id,
        stage=JobStage.QUEUED,
        progress=5,
        message=(
            "Draft job initialized and queued in the Video Editor pipeline..."
            if is_draft
            else "Job initialized and queued in the Video Editor pipeline..."
        ),
        is_draft=is_draft,
    )

    if async_mode:
        background_tasks.add_task(
            execute_video_pipeline,
            job_id=job_id,
            job_temp_dir=job_temp_dir,
            visual_clip_paths=saved_visual_clips,
            visual_url=effective_visual_url,
            song_url=effective_song_url,
            cached_song_id=cached_song_id,
            uploaded_song_path=saved_song_path,
            song_name=song_name,
            artist_name=artist_name,
            manual_lyrics=manual_lyrics,
            custom_lines_json=custom_lines_json,
            language=language,
            edit_config=edit_config,
            is_draft=is_draft,
            source_draft_filename=source_draft_filename,
        )
        return initial_status

    return await execute_video_pipeline(
        job_id=job_id,
        job_temp_dir=job_temp_dir,
        visual_clip_paths=saved_visual_clips,
        visual_url=effective_visual_url,
        song_url=effective_song_url,
        cached_song_id=cached_song_id,
        uploaded_song_path=saved_song_path,
        song_name=song_name,
        artist_name=artist_name,
        manual_lyrics=manual_lyrics,
        custom_lines_json=custom_lines_json,
        language=language,
        edit_config=edit_config,
        is_draft=is_draft,
        source_draft_filename=source_draft_filename,
    )


@app.get("/status/{job_id}", response_model=JobStatusResponse)
@app.get("/api/status/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    job = JOBS.get(job_id)
    if job:
        return job

    safe_job_id = Path(job_id).name
    # Check if the video already finished rendering on disk before restart
    if DRAFTS_EXPORT_DIR.exists():
        draft_matches = sorted(DRAFTS_EXPORT_DIR.glob(f"*({safe_job_id})*.mp4"))
        if draft_matches:
            found = draft_matches[0]
            return _update_job(
                safe_job_id,
                stage=JobStage.COMPLETED,
                progress=100,
                message=f"Draft video saved to Downloads/{DOWNLOADS_EXPORT_DIR.name}/draft/{found.name}",
                is_draft=True,
                output_filename=found.name,
                video_url=f"/api/rendered-videos/file/draft/{quote(found.name)}",
            )

    if DOWNLOADS_EXPORT_DIR.exists():
        dl_matches = sorted(DOWNLOADS_EXPORT_DIR.glob(f"*({safe_job_id})*.mp4"))
        if dl_matches:
            found = dl_matches[0]
            return _update_job(
                safe_job_id,
                stage=JobStage.COMPLETED,
                progress=100,
                message=f"TikTok video rendered & saved to Downloads/{DOWNLOADS_EXPORT_DIR.name}/{found.name}",
                is_draft=False,
                output_filename=found.name,
                video_url=f"/api/download/{safe_job_id}",
            )

    output_file = OUTPUT_DIR / f"lyrics_video_{safe_job_id}.mp4"
    if output_file.exists():
        return _update_job(
            safe_job_id,
            stage=JobStage.COMPLETED,
            progress=100,
            message="TikTok aesthetic lyrics video rendered!",
            is_draft=False,
            output_filename=output_file.name,
            video_url=f"/api/download/{safe_job_id}",
        )

    # Return a terminal FAILED status instead of 404 so active frontend pollers stop immediately
    return _update_job(
        safe_job_id,
        stage=JobStage.FAILED,
        progress=100,
        message="Server reloaded while job was running.",
        error="Backend server restarted while job was running. Please click Render again.",
    )


@app.get("/download/{job_id}")
@app.get("/api/download/{job_id}")
async def download_rendered_video(job_id: str, download: int = 0):
    safe_job_id = Path(job_id).name
    output_file = OUTPUT_DIR / f"lyrics_video_{safe_job_id}.mp4"
    if not output_file.exists() and DOWNLOADS_EXPORT_DIR.exists():
        # Fallback if moved or only present in Downloads folder
        matches = sorted(DOWNLOADS_EXPORT_DIR.glob(f"*({safe_job_id})*.mp4"))
        if matches:
            output_file = matches[0]
    if not output_file.exists() and DRAFTS_EXPORT_DIR.exists():
        draft_matches = sorted(DRAFTS_EXPORT_DIR.glob(f"*({safe_job_id})*.mp4"))
        if draft_matches:
            output_file = draft_matches[0]
    if not output_file.exists():
        raise HTTPException(status_code=404, detail="Rendered video file not found.")
    if download:
        return FileResponse(
            path=str(output_file),
            media_type="video/mp4",
            filename=f"tiktok_aesthetic_lyrics_{safe_job_id}.mp4",
        )
    return FileResponse(
        path=str(output_file),
        media_type="video/mp4",
        headers={"Accept-Ranges": "bytes", "Cache-Control": "no-cache"},
    )


def _build_export_filename(song_name: str, job_id: str) -> str:
    raw_title = (song_name or "").strip()
    safe_title = re.sub(r'[\\/:*?"<>|]+', "", raw_title).strip()
    safe_title = re.sub(r"\s+", " ", safe_title)[:60].strip()
    return f"{safe_title} ({job_id}).mp4" if safe_title else f"tiktok_lyrics_{job_id}.mp4"


def _save_render_to_dir(
    src_path: Path,
    target_dir: Path,
    song_name: str,
    job_id: str,
    move: bool = False,
) -> Optional[Path]:
    """Save or move a finished render into `target_dir` with a clean filename."""
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        dest_name = _build_export_filename(song_name, job_id)
        dest_path = target_dir / dest_name
        if move:
            shutil.move(str(src_path), str(dest_path))
        else:
            shutil.copy2(src_path, dest_path)
        logger.info("Saved rendered MP4 to %s", dest_path)
        return dest_path
    except Exception as exc:
        logger.warning("Could not save rendered video to '%s': %s", target_dir, exc)
        return None


def _copy_render_to_downloads(src_path: Path, song_name: str, job_id: str) -> Optional[Path]:
    """Copy a finished render into `~/Downloads/Titok video meker` with a clean filename."""
    return _save_render_to_dir(src_path, DOWNLOADS_EXPORT_DIR, song_name, job_id, move=False)


def _get_draft_projects_root() -> Path:
    return (DRAFTS_EXPORT_DIR / ".draft_projects").resolve()


def _save_draft_project_bundle(
    job_id: str,
    draft_mp4_path: Path,
    visual_clips: List[Path],
    master_audio_path: Path,
    wav_audio_path: Path,
    song_source_path: Optional[Path],
    cached_song_id: Optional[str],
    song_url: Optional[str],
    song_name: str,
    artist_name: str,
    language: str,
    manual_lyrics: str,
    lyrics_result: LyricsResult,
    total_duration: float,
    edit_config: VideoEditConfig,
) -> None:
    """
    Save all visual clips, song audio cache, synced lyrics timeline, and `VideoEditConfig`
    settings alongside a Draft MP4 so the user can load the draft back into the editor,
    re-edit any setting/lyrics/clips, and render to final!
    """
    try:
        proj_dir = _get_draft_projects_root() / job_id
        proj_dir.mkdir(parents=True, exist_ok=True)

        saved_clip_names: List[str] = []
        for idx, clip_p in enumerate(visual_clips):
            if clip_p and clip_p.exists():
                ext = clip_p.suffix or ".mp4"
                dest_clip = proj_dir / f"visual_clip_{idx:02d}{ext}"
                shutil.copy2(clip_p, dest_clip)
                saved_clip_names.append(dest_clip.name)

        # Always store a dedicated durable audio copy in SONGS_CACHE_DIR / draft_<job_id>
        # AND inside proj_dir so pruning can never break a saved Draft re-edit.
        effective_song_id = f"draft_{job_id}"
        song_cache_dir = SONGS_CACHE_DIR / effective_song_id
        song_cache_dir.mkdir(parents=True, exist_ok=True)
        raw_src = song_source_path
        if cached_song_id and cached_song_id.strip():
            prev_cache = SONGS_CACHE_DIR / Path(cached_song_id).name
            raw_cands = sorted(prev_cache.glob("raw_song*")) if prev_cache.is_dir() else []
            if raw_cands:
                raw_src = raw_cands[0]
        if master_audio_path and master_audio_path.exists():
            shutil.copy2(master_audio_path, song_cache_dir / "extracted_audio.m4a")
            shutil.copy2(master_audio_path, proj_dir / "extracted_audio.m4a")
        if wav_audio_path and wav_audio_path.exists():
            shutil.copy2(wav_audio_path, song_cache_dir / "extracted_audio.wav")
        if raw_src and raw_src.exists():
            raw_name = f"raw_song{raw_src.suffix or '.mp3'}"
            shutil.copy2(raw_src, song_cache_dir / raw_name)
            shutil.copy2(raw_src, proj_dir / raw_name)

        project_payload = {
            "job_id": job_id,
            "draft_filename": draft_mp4_path.name,
            "song_name": song_name,
            "artist_name": artist_name,
            "song_url": song_url or "",
            "cached_song_id": effective_song_id,
            "audio_preview_url": f"/api/song-preview/{effective_song_id}",
            "duration": round(total_duration, 2),
            "language": language,
            "manual_lyrics": manual_lyrics or "\n".join(line.text for line in lyrics_result.lines),
            "aligned_lines": [line.model_dump(mode="json") for line in lyrics_result.lines],
            "lyrics_source": lyrics_result.lyrics_source or "",
            "edit_config": edit_config.model_dump(mode="json"),
            "visual_clip_files": saved_clip_names,
        }
        (proj_dir / "project.json").write_text(
            json.dumps(project_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Saved draft project bundle for '%s' in %s", draft_mp4_path.name, proj_dir)
    except Exception as exc:
        logger.warning("Failed to save draft project bundle for %s: %s", job_id, exc)


def _delete_draft_project_bundle(draft_filename: str) -> None:
    try:
        jid = _extract_job_id_from_filename(draft_filename)
        if not jid:
            return
        proj_dir = (_get_draft_projects_root() / jid).resolve()
        if proj_dir.parent == _get_draft_projects_root() and proj_dir.is_dir():
            shutil.rmtree(proj_dir, ignore_errors=True)
            logger.info("Removed draft project bundle: %s", proj_dir)
        draft_song_dir = (SONGS_CACHE_DIR / f"draft_{jid}").resolve()
        if draft_song_dir.parent == SONGS_CACHE_DIR.resolve() and draft_song_dir.is_dir():
            shutil.rmtree(draft_song_dir, ignore_errors=True)
    except Exception as exc:
        logger.debug("Could not remove draft project bundle for %s: %s", draft_filename, exc)


def _delete_draft_file_if_exists(draft_filename: str) -> Optional[str]:
    """Remove a draft MP4 (and its saved draft project bundle) from `DRAFTS_EXPORT_DIR` after final render."""
    try:
        safe_name = Path(draft_filename).name
        if not safe_name:
            return None
        _delete_draft_project_bundle(safe_name)
        draft_root = DRAFTS_EXPORT_DIR.resolve()
        candidate = (draft_root / safe_name).resolve()
        if candidate.parent == draft_root and candidate.is_file():
            candidate.unlink(missing_ok=True)
            logger.info("Removed finalized draft from draft folder: %s", candidate.name)
            return candidate.name
    except Exception as exc:
        logger.warning("Failed to remove draft '%s': %s", draft_filename, exc)
    return None


def _extract_job_id_from_filename(name: str) -> str:
    m_paren = re.search(r"\(([a-f0-9]{12})\)\.mp4$", name, re.IGNORECASE)
    if m_paren:
        return m_paren.group(1)
    m_under = re.search(r"_([a-f0-9]{12})\.mp4$", name, re.IGNORECASE)
    return m_under.group(1) if m_under else ""


def _resolve_rendered_video_dir(location: str) -> Path:
    if location == "downloads":
        return DOWNLOADS_EXPORT_DIR.resolve()
    if location == "outputs":
        return OUTPUT_DIR.resolve()
    if location == "draft":
        return DRAFTS_EXPORT_DIR.resolve()
    raise HTTPException(status_code=400, detail="Invalid video location.")


def _list_rendered_videos_sync() -> List[Dict[str, Any]]:
    downloads_by_job: Dict[str, Path] = {}
    items: List[Dict[str, Any]] = []
    seen_job_ids: set = set()

    if DOWNLOADS_EXPORT_DIR.exists():
        for mp4 in sorted(DOWNLOADS_EXPORT_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True):
            jid = _extract_job_id_from_filename(mp4.name)
            if jid:
                downloads_by_job[jid] = mp4
                seen_job_ids.add(jid)
            st = mp4.stat()
            items.append(
                {
                    "id": f"downloads:{mp4.name}",
                    "filename": mp4.name,
                    "job_id": jid,
                    "location": "downloads",
                    "is_draft": False,
                    "size_mb": round(st.st_size / (1024 * 1024), 2),
                    "created_at": st.st_mtime,
                    "created_label": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "saved_in_downloads": True,
                    "downloads_path": str(mp4),
                    "stream_url": f"/api/rendered-videos/file/downloads/{quote(mp4.name)}",
                    "download_url": f"/api/rendered-videos/file/downloads/{quote(mp4.name)}?download=1",
                }
            )

    if OUTPUT_DIR.exists():
        for mp4 in sorted(OUTPUT_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True):
            jid = _extract_job_id_from_filename(mp4.name)
            if jid and jid in seen_job_ids:
                continue
            st = mp4.stat()
            dl_match = downloads_by_job.get(jid) if jid else None
            items.append(
                {
                    "id": f"outputs:{mp4.name}",
                    "filename": mp4.name,
                    "job_id": jid,
                    "location": "outputs",
                    "is_draft": False,
                    "size_mb": round(st.st_size / (1024 * 1024), 2),
                    "created_at": st.st_mtime,
                    "created_label": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "saved_in_downloads": bool(dl_match),
                    "downloads_path": str(dl_match) if dl_match else "",
                    "stream_url": f"/api/rendered-videos/file/outputs/{quote(mp4.name)}",
                    "download_url": f"/api/rendered-videos/file/outputs/{quote(mp4.name)}?download=1",
                }
            )

    if DRAFTS_EXPORT_DIR.exists():
        proj_root = _get_draft_projects_root()
        for mp4 in sorted(DRAFTS_EXPORT_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True):
            jid = _extract_job_id_from_filename(mp4.name)
            st = mp4.stat()
            has_proj = bool(jid and (proj_root / jid / "project.json").is_file())
            items.append(
                {
                    "id": f"draft:{mp4.name}",
                    "filename": mp4.name,
                    "job_id": jid,
                    "location": "draft",
                    "is_draft": True,
                    "has_project_state": has_proj,
                    "size_mb": round(st.st_size / (1024 * 1024), 2),
                    "created_at": st.st_mtime,
                    "created_label": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "saved_in_downloads": False,
                    "downloads_path": str(mp4),
                    "stream_url": f"/api/rendered-videos/file/draft/{quote(mp4.name)}",
                    "download_url": f"/api/rendered-videos/file/draft/{quote(mp4.name)}?download=1",
                }
            )

    items.sort(key=lambda x: x["created_at"], reverse=True)
    return items


@app.get("/api/rendered-videos")
async def list_rendered_videos_endpoint():
    videos = await asyncio.to_thread(_list_rendered_videos_sync)
    return {
        "folder": str(DOWNLOADS_EXPORT_DIR),
        "folder_label": f"Downloads/{DOWNLOADS_EXPORT_DIR.name}",
        "draft_folder": str(DRAFTS_EXPORT_DIR),
        "draft_folder_label": f"Downloads/{DOWNLOADS_EXPORT_DIR.name}/draft",
        "videos": videos,
    }


@app.get("/api/rendered-videos/file/{location}/{filename}")
async def serve_rendered_video_file(location: str, filename: str, download: int = 0):
    base_dir = _resolve_rendered_video_dir(location)
    safe_file = (base_dir / Path(filename).name).resolve()
    if not safe_file.exists() or safe_file.parent != base_dir:
        raise HTTPException(status_code=404, detail="Rendered video file not found.")
    if download:
        return FileResponse(path=str(safe_file), media_type="video/mp4", filename=safe_file.name)
    return FileResponse(
        path=str(safe_file),
        media_type="video/mp4",
        headers={"Accept-Ranges": "bytes", "Cache-Control": "no-cache"},
    )


@app.get("/api/rendered-videos/draft/{filename}/project")
async def get_draft_project_state(filename: str):
    """
    Load the saved project settings, synced lyrics timeline, song audio preview, and
    visual clip URLs for a draft video so the user can re-edit it and render to final.
    """
    draft_root = DRAFTS_EXPORT_DIR.resolve()
    safe_name = Path(filename).name
    safe_file = (draft_root / safe_name).resolve()
    if not safe_file.exists() or safe_file.parent != draft_root:
        raise HTTPException(status_code=404, detail="Draft video file not found.")

    jid = _extract_job_id_from_filename(safe_name)
    proj_file = (_get_draft_projects_root() / jid / "project.json") if jid else None

    if proj_file and proj_file.is_file():
        data = json.loads(proj_file.read_text(encoding="utf-8"))
        # Ensure cached song directory exists for previewing & trimming during re-edit
        c_song_id = Path(str(data.get("cached_song_id") or f"draft_{jid}")).name
        target_song_dir = SONGS_CACHE_DIR / c_song_id
        proj_parent = proj_file.parent
        if not (target_song_dir / "extracted_audio.m4a").is_file() and (proj_parent / "extracted_audio.m4a").is_file():
            target_song_dir.mkdir(parents=True, exist_ok=True)
            for fname in ("extracted_audio.m4a",):
                src_a = proj_parent / fname
                if src_a.is_file():
                    shutil.copy2(src_a, target_song_dir / fname)
            for raw_f in proj_parent.glob("raw_song*"):
                if raw_f.is_file():
                    shutil.copy2(raw_f, target_song_dir / raw_f.name)
            data["cached_song_id"] = c_song_id
            data["audio_preview_url"] = f"/api/song-preview/{c_song_id}"
        clip_files = data.get("visual_clip_files") or []
        data["visual_clip_urls"] = [
            {
                "url": f"/api/rendered-videos/draft/{quote(safe_name)}/clip/{idx}",
                "filename": cname,
            }
            for idx, cname in enumerate(clip_files)
        ]
        data["draft_filename"] = safe_name
        return data

    # Fallback for older draft MP4s saved before project bundles were introduced
    job_info = JOBS.get(jid) if jid else None
    clean_title = re.sub(r"\s*\([a-f0-9]{12}\)\.mp4$", "", safe_name, flags=re.IGNORECASE).strip()
    return {
        "job_id": jid,
        "draft_filename": safe_name,
        "song_name": (job_info.detected_title if job_info and job_info.detected_title else clean_title),
        "artist_name": (job_info.detected_artist if job_info and job_info.detected_artist else ""),
        "song_url": "",
        "cached_song_id": "",
        "audio_preview_url": "",
        "duration": (job_info.song_duration if job_info and job_info.song_duration else 0.0),
        "language": "si",
        "manual_lyrics": "\n".join(l.text for l in (job_info.lyrics_preview or [])) if job_info else "",
        "aligned_lines": [l.model_dump(mode="json") for l in (job_info.lyrics_preview or [])] if job_info else [],
        "lyrics_source": (job_info.lyrics_source if job_info and job_info.lyrics_source else ""),
        "edit_config": None,
        "visual_clip_urls": [],
    }


@app.get("/api/rendered-videos/draft/{filename}/clip/{clip_idx}")
async def serve_draft_project_visual_clip(filename: str, clip_idx: int):
    """Stream a saved visual clip from a draft project bundle for re-editing."""
    safe_name = Path(filename).name
    jid = _extract_job_id_from_filename(safe_name)
    if not jid:
        raise HTTPException(status_code=404, detail="Invalid draft filename.")
    proj_dir = (_get_draft_projects_root() / jid).resolve()
    proj_file = proj_dir / "project.json"
    if not proj_file.is_file():
        raise HTTPException(status_code=404, detail="Draft project bundle not found.")
    data = json.loads(proj_file.read_text(encoding="utf-8"))
    clip_files = data.get("visual_clip_files") or []
    if clip_idx < 0 or clip_idx >= len(clip_files):
        raise HTTPException(status_code=404, detail="Draft visual clip index out of range.")
    clip_path = (proj_dir / Path(clip_files[clip_idx]).name).resolve()
    if not clip_path.is_file() or clip_path.parent != proj_dir:
        raise HTTPException(status_code=404, detail="Draft visual clip file missing.")
    return FileResponse(path=str(clip_path), media_type="video/mp4", filename=clip_path.name)


@app.post("/api/rendered-videos/draft/{filename}/render")
async def render_draft_to_final_folder(filename: str):
    """
    Finalize/render a draft video: remove it from the `draft` folder and move it
    into the original rendered-videos save folder (`DOWNLOADS_EXPORT_DIR`).
    """
    draft_root = DRAFTS_EXPORT_DIR.resolve()
    safe_file = (draft_root / Path(filename).name).resolve()
    if not safe_file.exists() or not safe_file.is_file() or safe_file.parent != draft_root:
        raise HTTPException(status_code=404, detail="Draft video file not found.")

    DOWNLOADS_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    dest_path = DOWNLOADS_EXPORT_DIR / safe_file.name
    if dest_path.exists():
        stem = safe_file.stem
        suffix = safe_file.suffix or ".mp4"
        dest_path = DOWNLOADS_EXPORT_DIR / f"{stem}_{uuid.uuid4().hex[:6]}{suffix}"

    await asyncio.to_thread(shutil.move, str(safe_file), str(dest_path))
    _delete_draft_project_bundle(safe_file.name)
    logger.info("Promoted draft video '%s' -> '%s'", safe_file.name, dest_path)

    return {
        "status": "ok",
        "removed_from_draft": safe_file.name,
        "filename": dest_path.name,
        "location": "downloads",
        "folder": str(DOWNLOADS_EXPORT_DIR),
        "stream_url": f"/api/rendered-videos/file/downloads/{quote(dest_path.name)}",
        "download_url": f"/api/rendered-videos/file/downloads/{quote(dest_path.name)}?download=1",
    }


@app.delete("/api/rendered-videos/{location}/{filename}")
async def delete_rendered_video_file(location: str, filename: str):
    base_dir = _resolve_rendered_video_dir(location)
    safe_file = (base_dir / Path(filename).name).resolve()
    if not safe_file.exists() or safe_file.parent != base_dir:
        raise HTTPException(status_code=404, detail="Rendered video file not found.")
    jid = _extract_job_id_from_filename(safe_file.name)
    safe_file.unlink(missing_ok=True)
    if location == "draft":
        _delete_draft_project_bundle(safe_file.name)
    # Only clean matching copy across downloads/outputs for final videos (not draft deletes)
    if jid and location != "draft":
        for other_dir in (OUTPUT_DIR, DOWNLOADS_EXPORT_DIR):
            if other_dir.exists():
                for candidate in other_dir.glob(f"*{jid}*.mp4"):
                    candidate.unlink(missing_ok=True)
    return {"status": "ok", "deleted": safe_file.name}


@app.get("/api/health")
async def studio_health_endpoint():
    """Real-time studio diagnostics for the NLE top status bar."""
    from backend.services.media_service import get_ffmpeg_binary, get_ffprobe_binary

    ffmpeg_bin = get_ffmpeg_binary()
    ffprobe_bin = get_ffprobe_binary()
    custom_fonts = (
        [f.name for f in FONTS_DIR.iterdir() if f.is_file() and f.suffix.lower() in (".ttf", ".otf")]
        if FONTS_DIR.exists()
        else []
    )
    rendered_items = await asyncio.to_thread(_list_rendered_videos_sync)
    active_jobs = sum(
        1 for j in JOBS.values() if j.stage not in (JobStage.COMPLETED, JobStage.FAILED)
    )
    return {
        "status": "ok",
        "version": "3.0.0",
        "platform": sys.platform,
        "engine": "FFmpeg + libass HarfBuzz",
        "color_pipeline": "BT.709 / Full-Range Canvas Parity",
        "ffmpeg_ready": bool(ffmpeg_bin),
        "ffprobe_ready": bool(ffprobe_bin),
        "custom_fonts_count": len(custom_fonts),
        "rendered_videos_count": len(rendered_items),
        "active_jobs": active_jobs,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=os.getenv("BACKEND_HOST", "0.0.0.0"),
        port=int(os.getenv("BACKEND_PORT", "8000")),
        reload=False,
    )
