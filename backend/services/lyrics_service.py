import asyncio
import base64
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import httpx

from backend.config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    SP_DC_COOKIE,
    SPOTIFY_LYRICS_API_URL,
    create_resilient_async_client,
)
from backend.models import LyricLine, LyricsResult, LyricsSourceTier
from backend.services.alignment_service import (
    _clean_lyrics_lines,
    _extract_asr_windows_sync,
    align_lyrics_with_audio,
    select_and_align_best_lyrics_candidate,
    verify_and_enrich_lrc_lines,
)
from backend.services.media_service import (
    identify_song_with_shazam,
    identify_song_with_shazam_detailed,
)
from backend.services.sri_lanka_lyrics_service import (
    SRI_LANKAN_SITE_ORDER,
    fetch_lyrics_from_single_site,
    fetch_sri_lankan_lyrics_by_title,
)

logger = logging.getLogger(__name__)

LRC_LINE_REGEX = re.compile(r"\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\](.*)")


def format_lrc_timestamp(seconds: float) -> str:
    """Convert float seconds into standard LRC timestamp [mm:ss.xx]."""
    seconds = max(0.0, seconds)
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"[{minutes:02d}:{secs:05.2f}]"


def lines_to_lrc(lines: List[LyricLine]) -> str:
    """Serialize a list of LyricLine objects into standard .lrc file content."""
    return "\n".join(f"{format_lrc_timestamp(line.start)}{line.text}" for line in lines)


def parse_lrc_to_lines(lrc_text: str, total_duration: float = 180.0) -> List[LyricLine]:
    """
    Parse raw .lrc text into a sorted list of LyricLine objects with start and end times.
    """
    raw_entries: List[Tuple[float, str]] = []

    for raw_line in lrc_text.splitlines():
        match = LRC_LINE_REGEX.match(raw_line.strip())
        if not match:
            continue
        minutes = int(match.group(1))
        seconds = int(match.group(2))
        fraction_str = match.group(3) or "0"
        # Normalize 2-digit centiseconds vs 3-digit milliseconds
        fraction = int(fraction_str) / (1000.0 if len(fraction_str) == 3 else 100.0)
        timestamp = minutes * 60.0 + seconds + fraction
        text = match.group(4).strip()
        if text:
            raw_entries.append((timestamp, text))

    raw_entries.sort(key=lambda item: item[0])

    lines: List[LyricLine] = []
    for idx, (start_time, text) in enumerate(raw_entries):
        if idx + 1 < len(raw_entries):
            next_start = raw_entries[idx + 1][0]
            # Cap single line display duration to 6.5s so long instrumental gaps don't freeze text
            end_time = min(next_start, start_time + 6.5)
        else:
            end_time = min(total_duration, start_time + 5.0)

        if end_time <= start_time:
            end_time = start_time + 2.5

        lines.append(
            LyricLine(
                start=round(start_time, 3),
                end=round(end_time, 3),
                text=text,
            )
        )

    return lines


_GENERIC_TITLES = {
    "",
    "fetched song",
    "tiktok song",
    "uploaded song",
    "audio",
    "video",
    "original sound",
    "origina sound",
    "unknown",
    "song",
}

_NOISE_PAREN_RE = re.compile(
    r"(?i)[\(\[\{]\s*(?:official\s*(?:music\s*)?(?:video|audio|lyric(?:s)?|visualizer|mv)|"
    r"lyrics?\s*video|hd|4k|8d|slowed(?:\s*\+\s*reverb)?|reverb|sped\s*up|remix|"
    r"cover|live|karaoke|instrumental|full\s*song|status|whatsapp\s*status|tiktok)\s*[\)\]\}]"
)


def _clean_song_title_for_search(raw_title: str) -> str:
    """
    Strip TikTok/YouTube noise (#hashtags, @handles, 'original sound - ...',
    '[Official Video]', emojis/pipes, generic placeholders) so LRCLIB & Sri Lankan
    lyrics sites only search real song titles.
    """
    if not raw_title:
        return ""
    cleaned = re.sub(r"#\S+", " ", raw_title)
    cleaned = re.sub(r"@\S+", " ", cleaned)
    cleaned = re.sub(r"(?i)\boriginal\s+sound\b.*", " ", cleaned)
    cleaned = _NOISE_PAREN_RE.sub(" ", cleaned)
    # Keep first pipe segment if title has "Song Title | Artist | Extra tags"
    if "|" in cleaned:
        first_seg = cleaned.split("|", 1)[0].strip()
        if len(first_seg) >= 3:
            cleaned = first_seg
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_|•—–~")
    lower = cleaned.lower()
    if lower in _GENERIC_TITLES:
        return ""
    return cleaned


def extract_song_search_candidates(
    raw_title: str,
    raw_artist: str = "",
    alt_title: str = "",
) -> List[Tuple[str, str]]:
    """
    Build prioritized `(track_query, artist_query)` pairs from noisy TikTok / YouTube titles.
    Handles forms like:
      - "Sanda Mithuri (සඳ මිතුරි) - Kasun Kalhara [Official Audio] #fyp"
      - "Kasun Kalhara - Sanda Mithuri"
    """
    pairs: List[Tuple[str, str]] = []
    seen = set()

    def _add(t: str, a: str) -> None:
        t_c = _clean_song_title_for_search(t)
        a_c = _clean_song_title_for_search(a)
        if not t_c:
            return
        key = (t_c.lower(), a_c.lower())
        if key not in seen:
            seen.add(key)
            pairs.append((t_c, a_c))

    clean_artist = _clean_song_title_for_search(raw_artist)
    for src in (raw_title, alt_title):
        base = _clean_song_title_for_search(src)
        if not base:
            continue

        # Also extract parenthetical inner text if bilingual e.g. "Sanda Mithuri (සඳ මිතුරි)"
        paren_matches = re.findall(r"[\(\[]([^\)\]]{2,60})[\)\]]", base)
        no_paren = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", " ", base).strip(" -–—")

        # Handle separator "Left - Right" -> try both orders + no_paren + base
        sep_match = re.split(r"\s+[-–—]\s+", no_paren or base, maxsplit=1)
        if len(sep_match) == 2:
            left, right = sep_match[0].strip(), sep_match[1].strip()
            if left and right:
                _add(left, clean_artist or right)
                _add(right, clean_artist or left)

        if no_paren and no_paren != base:
            _add(no_paren, clean_artist)
            if clean_artist:
                _add(no_paren, "")
        for p in paren_matches:
            _add(p.strip(), clean_artist)

        _add(base, clean_artist)
        if clean_artist:
            _add(base, "")

    return pairs[:6]


async def fetch_lrclib_lyrics(
    track_name: str,
    artist_name: str = "",
    total_duration: float = 180.0,
) -> Tuple[Optional[LyricsResult], Optional[str]]:
    """
    Tier 1: Fetch synced lyrics from LRCLIB API (https://lrclib.net).
    Returns:
        Tuple[Optional[LyricsResult], Optional[str]]:
            - LyricsResult if syncedLyrics (.lrc) were found
            - Plain unsynced lyrics string (if available) to assist Tier 3 fallback
    """
    clean_track = _clean_song_title_for_search(track_name)
    if not clean_track:
        return None, None
    track_name = clean_track

    headers = {
        "User-Agent": "TikTokAutoLyricsCreator/1.0 (https://github.com/auto-lyrics)"
    }
    plain_lyrics_fallback: Optional[str] = None

    async with create_resilient_async_client(timeout=15.0, headers=headers) as client:
        # 1. Try exact /api/get if both track_name and artist_name are provided
        if artist_name.strip():
            try:
                resp = await client.get(
                    "https://lrclib.net/api/get",
                    params={
                        "track_name": track_name.strip(),
                        "artist_name": artist_name.strip(),
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    synced_lrc = data.get("syncedLyrics")
                    plain_lyrics_fallback = data.get("plainLyrics")
                    if synced_lrc:
                        lines = parse_lrc_to_lines(synced_lrc, total_duration)
                        if lines:
                            logger.info("Tier 1 (LRCLIB /api/get) succeeded with %d lines", len(lines))
                            return (
                                LyricsResult(
                                    tier=LyricsSourceTier.TIER_1_LRCLIB,
                                    lines=lines,
                                    raw_lrc=synced_lrc,
                                ),
                                plain_lyrics_fallback,
                            )
            except httpx.HTTPError as exc:
                logger.warning("LRCLIB /api/get failed: %s", exc)

        # 2. Fallback to /api/search
        try:
            search_params = {"track_name": track_name.strip()}
            if artist_name.strip():
                search_params["artist_name"] = artist_name.strip()
            else:
                search_params = {"q": track_name.strip()}

            resp = await client.get("https://lrclib.net/api/search", params=search_params)
            if resp.status_code == 200:
                results = resp.json()
                if isinstance(results, list):
                    for entry in results:
                        if not plain_lyrics_fallback and entry.get("plainLyrics"):
                            plain_lyrics_fallback = entry.get("plainLyrics")
                        synced_lrc = entry.get("syncedLyrics")
                        if synced_lrc:
                            lines = parse_lrc_to_lines(synced_lrc, total_duration)
                            if lines:
                                logger.info("Tier 1 (LRCLIB /api/search) succeeded with %d lines", len(lines))
                                return (
                                    LyricsResult(
                                        tier=LyricsSourceTier.TIER_1_LRCLIB,
                                        lines=lines,
                                        raw_lrc=synced_lrc,
                                    ),
                                    plain_lyrics_fallback,
                                )
        except httpx.HTTPError as exc:
            logger.warning("LRCLIB /api/search failed: %s", exc)

    return None, plain_lyrics_fallback


async def fetch_spotify_lyrics(
    track_name: str,
    artist_name: str = "",
    total_duration: float = 180.0,
) -> Optional[LyricsResult]:
    """
    Tier 2: Unofficial Spotify Lyrics API fallback.
    Uses the `SP_DC_COOKIE` environment variable to authenticate with Spotify's
    web player token endpoint, searches for the track ID, and fetches time-synced
    JSON lines from Spotify's color-lyrics endpoint (or an external SPOTIFY_LYRICS_API_URL),
    converting the response to .lrc format.
    """
    if not track_name.strip():
        return None

    if not SP_DC_COOKIE or SP_DC_COOKIE == "your_spotify_sp_dc_cookie_here":
        logger.info("Tier 2 skipped: SP_DC_COOKIE is not configured in environment.")
        return None

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "App-Platform": "WebPlayer",
    }

    async with create_resilient_async_client(timeout=15.0) as client:
        try:
            # Step 1: Exchange sp_dc cookie for a temporary WebPlayer Bearer accessToken
            token_resp = await client.get(
                "https://open.spotify.com/get_access_token",
                params={"reason": "transport", "productType": "web_player"},
                headers=headers,
                cookies={"sp_dc": SP_DC_COOKIE},
            )
            if token_resp.status_code != 200:
                logger.warning("Spotify token exchange returned HTTP %d", token_resp.status_code)
                return None

            token_data = token_resp.json()
            access_token = token_data.get("accessToken")
            if not access_token:
                logger.warning("Spotify token response did not include accessToken.")
                return None

            auth_headers = {
                **headers,
                "Authorization": f"Bearer {access_token}",
            }

            # Step 2: Search Spotify catalog for the track ID
            query = f"track:{track_name} artist:{artist_name}".strip() if artist_name else track_name
            search_resp = await client.get(
                "https://api.spotify.com/v1/search",
                params={"q": query, "type": "track", "limit": 1},
                headers=auth_headers,
            )
            if search_resp.status_code != 200:
                logger.warning("Spotify track search failed with HTTP %d", search_resp.status_code)
                return None

            items = search_resp.json().get("tracks", {}).get("items", [])
            if not items:
                logger.info("Spotify track search returned no tracks for query=%r", query)
                return None

            track_id = items[0]["id"]

            # Step 3: Fetch time-synced JSON lyrics from Spotify color-lyrics v2
            lyrics_resp = await client.get(
                f"https://spclient.wg.spotify.com/color-lyrics/v2/track/{track_id}",
                params={"format": "json", "market": "from_token"},
                headers=auth_headers,
            )

            raw_spotify_lines = []
            if lyrics_resp.status_code == 200:
                lyrics_json = lyrics_resp.json()
                raw_spotify_lines = lyrics_json.get("lyrics", {}).get("lines", [])
            elif SPOTIFY_LYRICS_API_URL:
                # Secondary fallback to self-hosted / Vercel spotify-lyrics-api bridge
                bridge_resp = await client.get(
                    SPOTIFY_LYRICS_API_URL,
                    params={"trackid": track_id, "format": "lrc"},
                )
                if bridge_resp.status_code == 200:
                    bridge_json = bridge_resp.json()
                    raw_spotify_lines = bridge_json.get("lines", [])

            if not raw_spotify_lines:
                return None

            # Convert Spotify JSON timing lines into LyricLine list & .lrc text
            parsed_entries: List[Tuple[float, str]] = []
            for item in raw_spotify_lines:
                words = (item.get("words") or "").strip()
                if not words or words == "♪":
                    continue
                if "startTimeMs" in item:
                    start_sec = float(item["startTimeMs"]) / 1000.0
                elif "timeTag" in item:
                    # Format "mm:ss.xx"
                    parts = item["timeTag"].split(":")
                    start_sec = int(parts[0]) * 60.0 + float(parts[1])
                else:
                    continue
                parsed_entries.append((start_sec, words))

            if not parsed_entries:
                return None

            lrc_str = "\n".join(
                f"{format_lrc_timestamp(ts)}{txt}" for ts, txt in parsed_entries
            )
            lines = parse_lrc_to_lines(lrc_str, total_duration)
            if lines:
                logger.info("Tier 2 (Spotify API) succeeded with %d synced lines", len(lines))
                return LyricsResult(
                    tier=LyricsSourceTier.TIER_2_SPOTIFY,
                    lines=lines,
                    raw_lrc=lrc_str,
                )

        except Exception as exc:
            logger.warning("Tier 2 Spotify lyrics fetch error: %s", exc)

    return None


def _is_sinhala_lang(language: str) -> bool:
    return (language or "si").strip().lower().startswith("si")


def _lrc_fits_clip(synced_res: LyricsResult, total_duration: float, language: str) -> bool:
    if not synced_res.lines:
        return False
    first_s = synced_res.lines[0].start
    last_e = synced_res.lines[-1].end
    if last_e > total_duration + 6.0 or first_s > max(14.0, total_duration * 0.35):
        return False
    if _is_sinhala_lang(language) and total_duration < 120.0:
        return False
    return True


def _slice_lrc_with_shazam_offset(
    synced_res: LyricsResult,
    clip_offset_sec: float,
    time_skew: float,
    total_duration: float,
) -> Optional[LyricsResult]:
    """
    When Shazam identifies the exact timestamp offset (`clip_offset_sec`) where a short
    TikTok clip starts inside the full studio track, shift and slice full-song LRC lines
    from `[clip_offset_sec, clip_offset_sec + total_duration]` into `[0.0, total_duration]`.
    """
    if not synced_res or not synced_res.lines or clip_offset_sec <= 0.5:
        return None
    rate = max(0.85, min(1.15, 1.0 + time_skew))
    sliced: List[LyricLine] = []
    for ln in synced_res.lines:
        rel_start = (ln.start - clip_offset_sec) / rate
        rel_end = (ln.end - clip_offset_sec) / rate
        if rel_end < 0.25 or rel_start > total_duration - 0.35:
            continue
        s_clamped = round(max(0.0, rel_start), 2)
        e_clamped = round(min(total_duration, max(s_clamped + 0.8, rel_end)), 2)
        sliced.append(LyricLine(start=s_clamped, end=e_clamped, text=ln.text))
    if len(sliced) < 2:
        return None
    return LyricsResult(
        tier=synced_res.tier,
        lines=sliced,
        raw_lrc=lines_to_lrc(sliced),
        detected_title=synced_res.detected_title,
        detected_artist=synced_res.detected_artist,
        lyrics_source=synced_res.lyrics_source,
    )


async def _fetch_and_verify_sequential_sites(
    search_pairs: List[Tuple[str, str]],
    audio_path: Path,
    job_temp_dir: Path,
    total_duration: float,
    language: str,
    shazam_clip_offset_sec: float = 0.0,
    shazam_time_skew: float = 0.0,
    shazam_lyrics_text: str = "",
    hint_texts: Optional[List[str]] = None,
) -> Tuple[Optional[LyricsResult], List[Tuple[str, str]]]:
    """
    Walk through lyrics sources ONE BY ONE in priority order until valid lyrics are
    both fetched AND verified against sung vocals:
      1. `lyrihub.lk` (for `si`)
      2. `lyrics-lk.com` (for `si`)
      3. `geemansala.com` (for `si`)
      4. Shazam embedded lyrics (if returned by Shazam audio fingerprint)
      5. LRCLIB (`lrclib.net`)
      6. Spotify (`SP_DC_COOKIE` if active — checked last)
    Stops immediately once a site yields an audio-verified match, saving unnecessary
    requests while guaranteeing every site gets tried if earlier ones fail or mismatch.
    """
    unverified_pool: List[Tuple[str, str]] = []
    seen_texts = set()

    def _dedup_candidates(items: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        fresh: List[Tuple[str, str]] = []
        for src_label, lyrics_body in items:
            cleaned_lines = [l.strip() for l in lyrics_body.splitlines() if l.strip()]
            if len(cleaned_lines) < 2:
                continue
            normalized_body = "\n".join(cleaned_lines)
            sig = re.sub(r"\s+", " ", normalized_body).lower()[:240]
            if sig in seen_texts:
                continue
            seen_texts.add(sig)
            fresh.append((src_label, normalized_body))
            unverified_pool.append((src_label, normalized_body))
        return fresh

    async def _try_verify_site(site_label: str, items: List[Tuple[str, str]]) -> Optional[LyricsResult]:
        fresh = _dedup_candidates(items)
        if not fresh:
            return None
        logger.info("Sequential site check [%s]: testing %d candidate(s) against audio...", site_label, len(fresh))
        matched_src, aligned = await select_and_align_best_lyrics_candidate(
            audio_path=audio_path,
            candidates=fresh,
            job_temp_dir=job_temp_dir,
            total_duration=total_duration,
            language=language,
            fallback_to_auto=False,
            clip_offset_sec=shazam_clip_offset_sec,
            hint_texts=hint_texts,
        )
        if matched_src and aligned:
            logger.info("Sequential lyrics fetch succeeded at site '%s' (%d aligned lines)", matched_src, len(aligned))
            matched_raw = next((body for s, body in fresh if s == matched_src), fresh[0][1])
            clean_full = "\n".join(_clean_lyrics_lines(matched_raw))
            return LyricsResult(
                tier=LyricsSourceTier.TIER_3_ALIGNMENT,
                lines=aligned,
                raw_lrc=lines_to_lrc(aligned),
                full_lyrics_text=clean_full or "\n".join(line.text for line in aligned),
                lyrics_source=matched_src,
            )
        logger.info("Site [%s] did not match sung vocals; moving to next site...", site_label)
        return None

    # --- Sites 1, 2, 3 (Sinhala priority): lyrihub.lk -> lyrics-lk.com -> geemansala.com (one by one) ---
    if _is_sinhala_lang(language):
        for site in SRI_LANKAN_SITE_ORDER:
            site_items: List[Tuple[str, str]] = []
            for track_q, artist_q in search_pairs[:4]:
                fetched_list = await fetch_lyrics_from_single_site(
                    site=site,
                    query=track_q,
                    artist_name=artist_q,
                    max_pages=2,
                )
                for item in fetched_list:
                    if item.get("lyrics_text"):
                        site_items.append((item.get("source") or site, item["lyrics_text"]))
                if site_items:
                    break  # Found pages on this site for query permutation
            verified = await _try_verify_site(site, site_items)
            if verified:
                return verified, unverified_pool

    # --- Site 4: Shazam Embedded Lyrics (if returned by Shazam audio fingerprint) ---
    if shazam_lyrics_text and shazam_lyrics_text.strip():
        verified = await _try_verify_site("Shazam Lyrics", [("Shazam Lyrics", shazam_lyrics_text)])
        if verified:
            return verified, unverified_pool

    async def _accept_lrc_if_verified(
        cand_res: Optional[LyricsResult],
        title_fallback: str,
        artist_fallback: str,
        src_label: str,
        full_plain: str,
    ) -> Optional[LyricsResult]:
        if not cand_res or not cand_res.lines:
            return None
        v_lines, v_status = await verify_and_enrich_lrc_lines(
            audio_path=audio_path,
            lrc_lines=cand_res.lines,
            job_temp_dir=job_temp_dir,
            total_duration=total_duration,
            language=language,
        )
        if not v_lines:
            logger.info("Skipped mismatched %s LRC slice; checking full-text alignment.", src_label)
            return None
        cand_res.lines = v_lines
        cand_res.raw_lrc = lines_to_lrc(v_lines)
        cand_res.detected_title = title_fallback
        cand_res.detected_artist = artist_fallback or None
        cand_res.lyrics_source = f"{src_label} + Auto-Mix" if v_status == "hybrid" else src_label
        cand_res.full_lyrics_text = full_plain
        return cand_res

    # --- Site 5: LRCLIB ---
    lrclib_items: List[Tuple[str, str]] = []
    for track_q, artist_q in search_pairs[:3]:
        lrclib_res, plain_lrc = await fetch_lrclib_lyrics(
            track_name=track_q,
            artist_name=artist_q,
            total_duration=total_duration,
        )
        if lrclib_res:
            full_lrc_plain = (
                "\n".join(_clean_lyrics_lines(plain_lrc))
                if plain_lrc
                else "\n".join(line.text for line in lrclib_res.lines)
            )
            if _lrc_fits_clip(lrclib_res, total_duration, language):
                accepted = await _accept_lrc_if_verified(
                    lrclib_res, track_q, artist_q, "LRCLIB Synced", full_lrc_plain
                )
                if accepted:
                    return accepted, unverified_pool
            sliced_lrc = _slice_lrc_with_shazam_offset(
                lrclib_res,
                clip_offset_sec=shazam_clip_offset_sec,
                time_skew=shazam_time_skew,
                total_duration=total_duration,
            )
            if sliced_lrc:
                accepted = await _accept_lrc_if_verified(
                    sliced_lrc, track_q, artist_q, "LRCLIB Synced (Shazam Offset)", full_lrc_plain
                )
                if accepted:
                    return accepted, unverified_pool
            lrclib_items.append(("LRCLIB Synced Text", full_lrc_plain))
        if plain_lrc:
            lrclib_items.append(("LRCLIB Plain", plain_lrc))
    verified = await _try_verify_site("LRCLIB", lrclib_items)
    if verified:
        return verified, unverified_pool

    # --- Site 6 (Last Fallback): Spotify ---
    spotify_items: List[Tuple[str, str]] = []
    for track_q, artist_q in search_pairs[:2]:
        spotify_res = await fetch_spotify_lyrics(
            track_name=track_q,
            artist_name=artist_q,
            total_duration=total_duration,
        )
        if spotify_res:
            full_sp_plain = "\n".join(line.text for line in spotify_res.lines)
            if _lrc_fits_clip(spotify_res, total_duration, language):
                accepted = await _accept_lrc_if_verified(
                    spotify_res, track_q, artist_q, "Spotify Synced", full_sp_plain
                )
                if accepted:
                    return accepted, unverified_pool
            sliced_sp = _slice_lrc_with_shazam_offset(
                spotify_res,
                clip_offset_sec=shazam_clip_offset_sec,
                time_skew=shazam_time_skew,
                total_duration=total_duration,
            )
            if sliced_sp:
                accepted = await _accept_lrc_if_verified(
                    sliced_sp, track_q, artist_q, "Spotify Synced (Shazam Offset)", full_sp_plain
                )
                if accepted:
                    return accepted, unverified_pool
            spotify_items.append(("Spotify Synced Text", full_sp_plain))
    verified = await _try_verify_site("Spotify", spotify_items)
    if verified:
        return verified, unverified_pool

    return None, unverified_pool


async def identify_and_transcribe_with_gemini(
    audio_path: Path,
    job_temp_dir: Path,
    language: str = "si",
) -> Optional[Dict[str, object]]:
    """
    Use Google Gemini AI (`GEMINI_API_KEY`) to listen to the audio clip, identify the
    song title/artist, and transcribe the sung lyrics line-by-line.
    Caches results per `job_temp_dir` to conserve free-tier RPM quota.
    """
    if not GEMINI_API_KEY or not audio_path.exists():
        return None

    cache_file = job_temp_dir / "gemini_lyrics_cache.json"
    audio_size = audio_path.stat().st_size
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached.get("audio_size") == audio_size and cached.get("result"):
                return cached["result"]
        except Exception:
            pass

    if audio_size > 18 * 1024 * 1024:
        return None

    ext = audio_path.suffix.lower()
    mime_type = (
        "audio/mp4" if ext in (".m4a", ".mp4") else "audio/mpeg" if ext == ".mp3" else "audio/wav"
    )
    b64_data = base64.b64encode(audio_path.read_bytes()).decode("ascii")
    lang_hint = "Sinhala Unicode" if _is_sinhala_lang(language) else "the sung language"
    prompt = (
        f"Listen carefully to this song clip. Identify the song title and artist if known, "
        f"and transcribe the exact lyrics sung in this audio clip line by line in {lang_hint}. "
        f'Return ONLY valid JSON: {{"title": "...", "artist": "...", "lines": ["..."]}}'
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {"inline_data": {"mime_type": mime_type, "data": b64_data}},
                    {"text": prompt},
                ]
            }
        ],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
    }

    models_to_try = [GEMINI_MODEL]
    if GEMINI_MODEL != "gemini-2.5-flash":
        models_to_try.append("gemini-2.5-flash")
    models_to_try.append("gemini-3.1-flash-lite-preview")

    async with create_resilient_async_client(timeout=28.0) as client:
        for mdl in models_to_try:
            try:
                resp = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{mdl}:generateContent",
                    params={"key": GEMINI_API_KEY},
                    json=payload,
                )
                if resp.status_code == 429 or resp.status_code == 404:
                    logger.info("Gemini model %s returned HTTP %d; trying fallback if available.", mdl, resp.status_code)
                    continue
                if resp.status_code != 200:
                    logger.warning("Gemini API returned HTTP %d: %s", resp.status_code, resp.text[:200])
                    return None
                data = resp.json()
                raw_txt = (
                    ((data.get("candidates") or [{}])[0].get("content") or {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                )
                if not raw_txt:
                    continue
                parsed = json.loads(raw_txt)
                if not isinstance(parsed, dict):
                    continue
                title = str(parsed.get("title") or "").strip()
                artist = str(parsed.get("artist") or "").strip()
                lines = [
                    str(ln).strip()
                    for ln in (parsed.get("lines") or [])
                    if str(ln).strip()
                ]
                result = {"title": title, "artist": artist, "lines": lines}
                try:
                    cache_file.write_text(
                        json.dumps({"audio_size": audio_size, "result": result}, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
                logger.info(
                    "Gemini AI (%s) identified song title=%r artist=%r (%d transcribed lines)",
                    mdl,
                    title,
                    artist,
                    len(lines),
                )
                return result
            except Exception as exc:
                logger.warning("Gemini AI request failed on %s: %s", mdl, exc)
                continue
    return None


async def search_full_song_with_gemini_grounding(
    sung_lines: List[str],
    job_temp_dir: Path,
    hint_title: str = "",
    hint_artist: str = "",
    language: str = "si",
) -> Optional[Dict[str, str]]:
    """
    Use Gemini + Google Search Grounding (`tools: [{"google_search": {}}]`) from actual
    heard Sinhala lines to identify the true song title, original artist, and complete full song lyrics.
    """
    if not GEMINI_API_KEY or not sung_lines:
        return None

    cache_file = job_temp_dir / "gemini_grounded_song.json"
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached.get("lyrics_text"):
                return cached
        except Exception:
            pass

    snippet = " ".join(ln.strip() for ln in sung_lines[:6] if ln.strip())
    if not snippet:
        return None

    lang_label = "Sinhala Unicode" if _is_sinhala_lang(language) else "the original song language"
    prompt = (
        f'Search Google for the song that contains these exact sung lyrics: "{snippet}".\n'
        f"Identify the true original Song Title and Artist, and provide the COMPLETE full song lyrics "
        f"(every verse and chorus from start to finish) in {lang_label}.\n"
        f"Format your response strictly as:\n"
        f"TITLE: <song title>\n"
        f"ARTIST: <artist name>\n"
        f"LYRICS:\n"
        f"<line 1>\n"
        f"<line 2>\n..."
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.1},
    }

    models_to_try = [GEMINI_MODEL]
    if GEMINI_MODEL != "gemini-2.5-flash":
        models_to_try.append("gemini-2.5-flash")

    async with create_resilient_async_client(timeout=30.0) as client:
        for mdl in models_to_try:
            try:
                resp = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{mdl}:generateContent",
                    params={"key": GEMINI_API_KEY},
                    json=payload,
                )
                if resp.status_code in (400, 404, 429):
                    continue
                if resp.status_code != 200:
                    return None
                data = resp.json()
                parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
                raw_txt = "\n".join(str(p.get("text") or "") for p in parts if p.get("text")).strip()
                if not raw_txt:
                    continue

                title_val = ""
                artist_val = ""
                lyrics_lines: List[str] = []
                in_lyrics = False
                for raw_ln in raw_txt.splitlines():
                    s = raw_ln.strip()
                    s_clean = re.sub(r"^[#*\-\s]+", "", s).replace("**", "").strip()
                    if not in_lyrics:
                        if s_clean.upper().startswith("TITLE:"):
                            title_val = s_clean.split(":", 1)[1].strip().strip("*\"'")
                            continue
                        if s_clean.upper().startswith("ARTIST:"):
                            artist_val = s_clean.split(":", 1)[1].strip().strip("*\"'")
                            continue
                        if s_clean.upper().startswith("LYRICS:"):
                            in_lyrics = True
                            continue
                    else:
                        if s.startswith("```"):
                            continue
                        if s and not s.startswith(("[", "(")) or (s.startswith("(") and len(s) > 12):
                            lyrics_lines.append(s)

                clean_lyrics = "\n".join(_clean_lyrics_lines("\n".join(lyrics_lines)))
                if len(clean_lyrics.splitlines()) < 4:
                    continue

                result = {
                    "title": title_val,
                    "artist": artist_val,
                    "lyrics_text": clean_lyrics,
                }
                try:
                    cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
                logger.info(
                    "Gemini Google Search Grounding found '%s' by '%s' (%d full lines)",
                    title_val,
                    artist_val,
                    len(clean_lyrics.splitlines()),
                )
                return result
            except Exception as exc:
                logger.warning("Gemini grounding search failed on %s: %s", mdl, exc)
                continue
    return None


async def resolve_synced_lyrics(
    audio_path: Path,
    job_temp_dir: Path,
    total_duration: float,
    song_name: str = "",
    artist_name: str = "",
    manual_lyrics: str = "",
    language: str = "si",
    raw_audio_path: Optional[Path] = None,
    alt_title: str = "",
    start_line_index: int = -1,
    force_refresh_asr: bool = False,
) -> LyricsResult:
    """
    Master 3-Tier Lyrics & Timing Engine:
    1. If manual lyrics are provided, sync them directly against vocals (using Shazam
       clip offset / user-selected `start_line_index` to support mid-song clips).
    2. Otherwise fingerprint the audio with Shazam (`shazamio`) FIRST to detect the
       true Song Title, Artist, exact Clip Start Offset inside the original song,
       and any embedded Shazam lyrics (combining with link metadata candidates).
    3. Sequentially try EVERY site one-by-one (`lyrihub.lk` -> `lyrics-lk.com` ->
       `geemansala.com` -> Shazam -> LRCLIB -> Spotify last) until clean lyrics are
       fetched and audio-verified against sung ASR anchors before falling back to Auto-Captions.
    """
    clean_manual = manual_lyrics.strip()
    caught_title = _clean_song_title_for_search(song_name) or _clean_song_title_for_search(alt_title)
    caught_artist = _clean_song_title_for_search(artist_name)

    # Fingerprint the active trimmed audio clip (`extracted_audio.m4a` or `audio_path`) FIRST
    # so `shazam_clip_offset_sec` reflects where the active clip begins inside the full song!
    trimmed_m4a = job_temp_dir / "extracted_audio.m4a"
    fingerprint_source = (
        trimmed_m4a
        if trimmed_m4a.exists()
        else (audio_path if audio_path.exists() else raw_audio_path)
    )
    shazam_clip_offset_sec = 0.0
    shazam_time_skew = 0.0
    shazam_lyrics_text = ""

    search_pairs: List[Tuple[str, str]] = []
    if fingerprint_source and fingerprint_source.exists():
        shazam_info = await identify_song_with_shazam_detailed(fingerprint_source)
        if not shazam_info and raw_audio_path and raw_audio_path.exists() and raw_audio_path != fingerprint_source:
            shazam_info = await identify_song_with_shazam_detailed(raw_audio_path)
        if shazam_info and shazam_info.get("title"):
            shazam_title = shazam_info["title"]
            shazam_artist = shazam_info.get("artist") or ""
            shazam_clip_offset_sec = float(shazam_info.get("clip_offset_sec") or 0.0)
            shazam_time_skew = float(shazam_info.get("time_skew") or 0.0)
            shazam_lyrics_text = str(shazam_info.get("lyrics_text") or "")
            caught_title = shazam_title
            caught_artist = shazam_artist or caught_artist
            search_pairs.extend(extract_song_search_candidates(shazam_title, shazam_artist))

    hint_texts = [
        t
        for t in (
            _clean_song_title_for_search(song_name),
            _clean_song_title_for_search(alt_title),
        )
        if t
    ]

    if clean_manual:
        existing_lrc_lines = parse_lrc_to_lines(clean_manual, total_duration)
        if existing_lrc_lines and start_line_index < 0:
            logger.info("Manual input already contains valid .lrc timestamps (%d lines)", len(existing_lrc_lines))
            return LyricsResult(
                tier=LyricsSourceTier.TIER_1_LRCLIB,
                lines=existing_lrc_lines,
                raw_lrc=clean_manual,
                full_lyrics_text="\n".join(line.text for line in existing_lrc_lines),
                detected_title=caught_title or None,
                detected_artist=caught_artist or None,
                lyrics_source="Manual LRC",
            )

        clean_full_manual = "\n".join(_clean_lyrics_lines(clean_manual))
        logger.info(
            "Manual/Fetched lyrics provided (%d chars, start_line_index=%d); running Vocal Forced Alignment...",
            len(clean_manual),
            start_line_index,
        )
        aligned_lines = await align_lyrics_with_audio(
            audio_path=audio_path,
            lyrics_text=clean_manual,
            job_temp_dir=job_temp_dir,
            total_duration=total_duration,
            language=language,
            clip_offset_sec=shazam_clip_offset_sec,
            hint_texts=hint_texts,
            start_line_index=start_line_index,
            force_refresh_asr=force_refresh_asr,
        )
        return LyricsResult(
            tier=LyricsSourceTier.TIER_3_ALIGNMENT,
            lines=aligned_lines,
            raw_lrc=lines_to_lrc(aligned_lines),
            full_lyrics_text=clean_full_manual or "\n".join(line.text for line in aligned_lines),
            detected_title=caught_title or None,
            detected_artist=caught_artist or None,
            lyrics_source=(
                f"Vocal Sync (From Line #{start_line_index + 1})"
                if start_line_index >= 0
                else "Vocal Sync (Auto-Detected Position)"
            ),
        )

    # Merge any link metadata / user-provided search pairs after Shazam's exact match
    meta_pairs = extract_song_search_candidates(song_name, artist_name, alt_title)
    seen_pair_keys = {(t.lower(), a.lower()) for t, a in search_pairs}
    for pair in meta_pairs:
        key = (pair[0].lower(), pair[1].lower())
        if key not in seen_pair_keys:
            seen_pair_keys.add(key)
            search_pairs.append(pair)

    unverified_pool: List[Tuple[str, str]] = []
    if search_pairs or shazam_lyrics_text:
        res, pool = await _fetch_and_verify_sequential_sites(
            search_pairs=search_pairs,
            audio_path=audio_path,
            job_temp_dir=job_temp_dir,
            total_duration=total_duration,
            language=language,
            shazam_clip_offset_sec=shazam_clip_offset_sec,
            shazam_time_skew=shazam_time_skew,
            shazam_lyrics_text=shazam_lyrics_text,
            hint_texts=hint_texts,
        )
        unverified_pool.extend(pool)
        if res:
            res.detected_title = res.detected_title or caught_title or None
            res.detected_artist = res.detected_artist or caught_artist or None
            return res

    # If Shazam/metadata did not yield a verified web match, ask Gemini AI (`GEMINI_API_KEY`)
    # to listen to the audio & identify the song, then ground via Google Search for FULL song lyrics!
    gemini_src = fingerprint_source if (fingerprint_source and fingerprint_source.exists()) else audio_path
    gem_info = await identify_and_transcribe_with_gemini(
        audio_path=gemini_src,
        job_temp_dir=job_temp_dir,
        language=language,
    )
    gem_lines_list: List[str] = []
    if gem_info:
        gem_title = re.sub(r"\([^)]*\)", "", str(gem_info.get("title") or "")).strip()
        gem_artist = str(gem_info.get("artist") or "").strip()
        gem_lines_list = [str(l).strip() for l in (gem_info.get("lines") or []) if str(l).strip()]
        if gem_title:
            caught_title = gem_title
        if gem_artist:
            caught_artist = gem_artist
        if gem_title:
            gem_pairs = [
                p for p in extract_song_search_candidates(gem_title, gem_artist)
                if (p[0].lower(), p[1].lower()) not in seen_pair_keys
            ]
            if gem_pairs:
                for p in gem_pairs:
                    seen_pair_keys.add((p[0].lower(), p[1].lower()))
                    search_pairs.append(p)
                res_gem, pool_gem = await _fetch_and_verify_sequential_sites(
                    search_pairs=gem_pairs,
                    audio_path=audio_path,
                    job_temp_dir=job_temp_dir,
                    total_duration=total_duration,
                    language=language,
                    shazam_clip_offset_sec=shazam_clip_offset_sec,
                    shazam_time_skew=shazam_time_skew,
                    shazam_lyrics_text=shazam_lyrics_text,
                    hint_texts=hint_texts,
                )
                unverified_pool.extend(pool_gem)
                if res_gem:
                    res_gem.detected_title = res_gem.detected_title or caught_title or None
                    res_gem.detected_artist = res_gem.detected_artist or caught_artist or None
                    return res_gem

    # Use Google Search grounding via Gemini on the heard Sinhala lines to retrieve the true
    # song title, artist, and COMPLETE full-song lyrics even when Shazam/local hubs miss it.
    has_grounded_full = False
    if gem_lines_list:
        grounded = await search_full_song_with_gemini_grounding(
            sung_lines=gem_lines_list,
            job_temp_dir=job_temp_dir,
            hint_title=caught_title,
            hint_artist=caught_artist,
            language=language,
        )
        if grounded and grounded.get("lyrics_text"):
            has_grounded_full = True
            if grounded.get("title"):
                caught_title = grounded["title"]
            if grounded.get("artist"):
                caught_artist = grounded["artist"]
            g_src, g_aligned = await select_and_align_best_lyrics_candidate(
                audio_path=audio_path,
                candidates=[("Gemini Google Search (Full Song)", grounded["lyrics_text"])],
                job_temp_dir=job_temp_dir,
                total_duration=total_duration,
                language=language,
                fallback_to_auto=False,
                clip_offset_sec=shazam_clip_offset_sec,
                hint_texts=hint_texts,
            )
            if g_src and g_aligned:
                return LyricsResult(
                    tier=LyricsSourceTier.TIER_3_ALIGNMENT,
                    lines=g_aligned,
                    raw_lrc=lines_to_lrc(g_aligned),
                    full_lyrics_text=grounded["lyrics_text"],
                    detected_title=caught_title or None,
                    detected_artist=caught_artist or None,
                    lyrics_source=g_src,
                )
            unverified_pool.insert(0, ("Gemini Google Search (Full Song)", grounded["lyrics_text"]))
    if not has_grounded_full and gem_lines_list and len(gem_lines_list) >= 2:
        unverified_pool.append(("Gemini AI (Audio Lyrics)", "\n".join(gem_lines_list)))

    # If Google ASR was offline (`not asr_windows`) yet a web site DID return lyrics, align acoustically;
    # otherwise fall back cleanly to Mode A Auto-Captions.
    # Also ensure that if we detected a song title (via Shazam or metadata) and `unverified_pool` is still empty,
    # we fetch the full song lyrics from Sri Lankan sites / LRCLIB / Shazam so `full_lyrics_text` always has
    # the entire song lyrics available in the "Full Text" tab for user line selection!
    if not unverified_pool and search_pairs and _is_sinhala_lang(language):
        for track_q, artist_q in search_pairs[:3]:
            for site in SRI_LANKAN_SITE_ORDER:
                try:
                    fetched_list = await fetch_lyrics_from_single_site(
                        site=site,
                        query=track_q,
                        artist_name=artist_q,
                        max_pages=1,
                    )
                    for item in fetched_list:
                        if item.get("lyrics_text"):
                            unverified_pool.append((item.get("source") or site, item["lyrics_text"]))
                    if unverified_pool:
                        break
                except Exception:
                    continue
            if unverified_pool:
                break

    # Fallback: If Shazam/metadata found no web lyrics yet (`not unverified_pool`),
    # use Sinhala ASR phrases heard in the clip to search Sri Lankan lyrics hubs
    # (`geemansala.com` full-text WP search + `lyrihub.lk` / `lyrics-lk.com`).
    if not unverified_pool and _is_sinhala_lang(language) and audio_path.exists():
        try:
            asr_windows = await asyncio.to_thread(
                _extract_asr_windows_sync,
                audio_path,
                job_temp_dir,
                total_duration,
                language,
            )
            phrase_candidates: List[str] = []
            seen_phrases = set()
            for _, _, wt in sorted(asr_windows, key=lambda w: len(w[2]), reverse=True):
                words = [w for w in wt.strip().split() if w]
                if len(words) < 2:
                    continue
                phrase = " ".join(words[:5])
                si_chars = sum(1 for c in phrase if 0x0D80 <= ord(c) <= 0x0DFF)
                if si_chars >= 7 and phrase not in seen_phrases:
                    seen_phrases.add(phrase)
                    phrase_candidates.append(phrase)
                if len(phrase_candidates) >= 3:
                    break

            for phrase in phrase_candidates:
                for site in ("geemansala.com", "lyrihub.lk", "lyrics-lk.com"):
                    try:
                        hits = await fetch_lyrics_from_single_site(
                            site=site,
                            query=phrase,
                            artist_name="",
                            max_pages=2,
                        )
                        for item in hits:
                            if item.get("lyrics_text"):
                                src_tag = f"{item.get('source') or site} (Audio Match)"
                                unverified_pool.append((src_tag, item["lyrics_text"]))
                                if not caught_title and item.get("title"):
                                    caught_title = item["title"]
                                if not caught_artist and item.get("artist"):
                                    caught_artist = item["artist"]
                    except Exception:
                        continue
                if len(unverified_pool) >= 3:
                    break
        except Exception as exc:
            logger.debug("ASR-phrase Sri Lankan lyrics lookup skipped: %s", exc)

    matched_source, aligned_lines = await select_and_align_best_lyrics_candidate(
        audio_path=audio_path,
        candidates=unverified_pool,
        job_temp_dir=job_temp_dir,
        total_duration=total_duration,
        language=language,
        fallback_to_auto=True,
        clip_offset_sec=shazam_clip_offset_sec,
        hint_texts=hint_texts,
    )
    source_label = matched_source if matched_source != "auto_asr" else f"Auto-Captions ({language})"
    if matched_source == "auto_asr":
        # Even when timeline falls back to Auto-Captions for the trimmed clip,
        # keep the fullest fetched song lyrics (from Sri Lankan hubs / LRCLIB / Shazam)
        # so the Full Text tab displays the entire song rather than only the clip's time range.
        pool_texts = [txt for _, txt in unverified_pool if txt and txt.strip()]
        if shazam_lyrics_text and shazam_lyrics_text.strip():
            pool_texts.append(shazam_lyrics_text)
        best_full_raw = (
            max(pool_texts, key=lambda t: len(_clean_lyrics_lines(t)))
            if pool_texts
            else ""
        )
        clean_full_pool = "\n".join(_clean_lyrics_lines(best_full_raw)) if best_full_raw else ""
    else:
        matched_full_raw = next(
            (txt for s, txt in unverified_pool if s == matched_source and txt.strip()),
            unverified_pool[0][1] if unverified_pool else shazam_lyrics_text,
        )
        clean_full_pool = "\n".join(_clean_lyrics_lines(matched_full_raw)) if matched_full_raw else ""

    return LyricsResult(
        tier=LyricsSourceTier.TIER_3_ALIGNMENT,
        lines=aligned_lines,
        raw_lrc=lines_to_lrc(aligned_lines),
        full_lyrics_text=clean_full_pool or "\n".join(line.text for line in aligned_lines),
        detected_title=caught_title or None,
        detected_artist=caught_artist or None,
        lyrics_source=source_label,
    )

