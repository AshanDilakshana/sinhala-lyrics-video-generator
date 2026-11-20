import asyncio
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
import difflib
import json
import logging
import math
import os
import re
import subprocess
import threading
import time
import wave
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import httpx
import numpy as np

from backend.config import (
    ALIGNMENT_ENGINE,
    DEFAULT_ALIGNMENT_LANG,
    WHISPER_MODEL_SIZE,
    create_resilient_sync_client,
)
from backend.models import LyricLine, WordTiming
from backend.services.media_service import get_ffmpeg_binary
from backend.services.sri_lanka_lyrics_service import (
    _canonical_singlish_word,
    _normalize_latin_tokens,
    sinhala_to_singlish,
)

logger = logging.getLogger(__name__)

# Sinhala Unicode code point sets for accurate syllable/mora (මාත්‍රා) acoustic weighting
SINHALA_INDEP_VOWELS = set(range(0x0D85, 0x0D97))
SINHALA_CONSONANTS = set(range(0x0D9A, 0x0DC7))
SINHALA_HAL_KIRIMA = 0x0DCA  # ් (suppresses inherent vowel -> merges with preceding syllable)
SINHALA_LONG_VOWEL_SIGNS = {
    0x0DCF,  # ා
    0x0DD1,  # ෑ
    0x0DD3,  # ී
    0x0DD6,  # ූ
    0x0DDA,  # ේ
    0x0DDB,  # ෛ
    0x0DDD,  # ෝ
    0x0DDE,  # ෞ
    0x0DDF,  # ෟ
}
SINHALA_NASALS = {0x0D82, 0x0D83}  # ං, ඃ

# Common decomposed Sinhala vowel combinations typed on web/mobile keyboards
SINHALA_DECOMPOSED_MAP = [
    ("\u0dd9\u0dcf\u0dca", "\u0ddd"),  # ෙ + ා + ් -> ෝ
    ("\u0dd9\u0dcf", "\u0ddc"),        # ෙ + ා -> ො
    ("\u0dd9\u0dca", "\u0dda"),        # ෙ + ් -> ේ
    ("\u0dd9\u0ddf", "\u0dde"),        # ෙ + ෟ -> ෞ
    ("\u0dd2\u0dca", "\u0dd3"),        # ි + ් -> ී
]

# Deep phonetic equivalence map for sung Sinhala over background music
SINHALA_PHONETIC_CHAR_MAP = str.maketrans(
    {
        "ණ": "න",
        "ළ": "ල",
        "ශ": "ස",
        "ෂ": "ස",
        "ඛ": "ක",
        "ග": "ක",
        "ඝ": "ක",
        "ඟ": "ක",
        "ඡ": "ච",
        "ජ": "ච",
        "ඣ": "ච",
        "ඨ": "ට",
        "ඩ": "ට",
        "ඪ": "ට",
        "ඬ": "ට",
        "ථ": "ත",
        "ද": "ත",
        "ධ": "ත",
        "ඳ": "ත",
        "ඵ": "ප",
        "බ": "ප",
        "භ": "ප",
        "ඹ": "ප",
        "ා": "",
        "ෑ": "ැ",
        "ී": "ි",
        "ූ": "ු",
        "ේ": "ෙ",
        "ෝ": "ො",
        "\u200d": "",
        "්": "",
    }
)


# Repair broken Rakaraansha (missing ZWJ \u200d before ර in common Sinhala roots from web/LRC sources)
_BROKEN_RAKARAANSHA_RE = re.compile(r"([පශගබදච])\u0DCA\u0DBB")


def normalize_sinhala_text(text: str) -> str:
    """Normalize decomposed Sinhala vowel signs, Rakaraansha ZWJ, and whitespace."""
    out = text
    for raw_seq, precomposed in SINHALA_DECOMPOSED_MAP:
        out = out.replace(raw_seq, precomposed)
    out = _BROKEN_RAKARAANSHA_RE.sub("\\1\u0DCA\u200D\u0DBB", out)
    out = out.replace("ශ\u0DCA\u200Dර", "ශ්‍ර")
    return re.sub(r"[ \t]+", " ", out).strip()


def sinhala_phonetic_key(text: str) -> str:
    """
    Convert a Sinhala/English lyric string into a compact phonetic skeleton so that
    ASR transcriptions over background music reliably match manual Sinhala lyrics.
    """
    cleaned = normalize_sinhala_text(text).lower()
    cleaned = re.sub(r"[^\u0d80-\u0dff\w\s]", " ", cleaned)
    mapped = cleaned.translate(SINHALA_PHONETIC_CHAR_MAP)
    compact = re.sub(r"\s+", "", mapped)
    return re.sub(r"(.)\1+", r"\1", compact)


def count_sinhala_syllable_weight(text: str) -> float:
    """
    Calculate the spoken syllable/mora (මාත්‍රා) count of a Sinhala or English string.
    """
    weight = 0.0
    for ch in normalize_sinhala_text(text):
        cp = ord(ch)
        if cp in SINHALA_INDEP_VOWELS or cp in SINHALA_CONSONANTS:
            weight += 1.0
        elif cp == SINHALA_HAL_KIRIMA:
            weight = max(0.4, weight - 0.55)
        elif cp in SINHALA_LONG_VOWEL_SIGNS:
            weight += 0.45
        elif cp in SINHALA_NASALS:
            weight += 0.25
        elif ch.isalnum():
            weight += 0.38
    return max(weight, 1.0)


def compute_musical_phrase_weight(text: str) -> float:
    """
    Damped syllable weight for musical phrases.
    """
    syllables = count_sinhala_syllable_weight(text)
    words = [w for w in text.strip().split(" ") if w]
    return 1.0 + 0.34 * math.sqrt(syllables) + 0.06 * len(words)


def _clean_lyrics_lines(lyrics_text: str) -> List[str]:
    """
    Split raw manual or web-scraped lyrics block into clean, normalized, non-empty lines.
    - Strips accidental RSC JSON suffixes (`","translation"`) and unescapes literal `\\n`.
    - When Sinhala lines are present, removes pure-English header/footer lines
      (e.g. 'Lyrics of ...', 'Song Title – Artist Name').
    - Splits compound two-phrase lines joined by ' - ' / ' – ' when both halves are Sinhala phrases.
    """
    raw_block = lyrics_text or ""
    for cutoff in ('","translation"', '\\",\\"translation\\"', '","meaning"', '\\",\\"meaning\\"'):
        c_idx = raw_block.find(cutoff)
        if c_idx > 0:
            raw_block = raw_block[:c_idx]
    if "\\n" in raw_block and "\n" not in raw_block:
        raw_block = raw_block.replace("\\r\\n", "\n").replace("\\n", "\n")

    has_sinhala_block = bool(re.search(r"[\u0D80-\u0DFF]", raw_block))
    cleaned: List[str] = []
    for raw in raw_block.splitlines():
        line = normalize_sinhala_text(raw)
        if not line or (line.startswith("[") and line.endswith("]")):
            continue
        low = line.lower()
        if low.startswith(("lyrics of ", "song :", "song:", "artist :", "artist:", "music :", "music:", "lyrics :", "vocal :")):
            continue
        if has_sinhala_block and not re.search(r"[\u0D80-\u0DFF]", line):
            # Drop pure-ASCII title/artist header or footer lines on Sinhala songs
            continue
        # Split compound two-phrase lines like 'අවසර දෙන්නම් යන්න - යන තැන සතුටින් ඉන්න'
        parts = re.split(r"\s+[-–—]\s+", line)
        if len(parts) == 2 and all(
            len(re.findall(r"[\u0D80-\u0DFF]", p)) >= 6 for p in parts
        ):
            for p in parts:
                p_clean = p.strip(" -–—")
                if p_clean:
                    cleaned.append(p_clean)
        else:
            cleaned.append(line)
    return cleaned


def export_lines_to_srt(lines: List[LyricLine], srt_path: Path) -> Path:
    """Export aligned LyricLine list to standard SubRip (.srt) format."""
    def _fmt_srt_time(seconds: float) -> str:
        millis = int(round((seconds % 1) * 1000))
        total_secs = int(seconds)
        hrs = total_secs // 3600
        mins = (total_secs % 3600) // 60
        secs = total_secs % 60
        return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"

    blocks = []
    for idx, item in enumerate(lines, start=1):
        blocks.append(
            f"{idx}\n{_fmt_srt_time(item.start)} --> {_fmt_srt_time(item.end)}\n{item.text}\n"
        )
    srt_path.write_text("\n".join(blocks), encoding="utf-8")
    return srt_path


def _extract_vocal_band_envelope(
    audio_path: Path,
    hop_sec: float = 0.02,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """
    Analyze the 16kHz PCM .wav file using STFT at 20ms resolution to isolate the human
    singing voice frequency band (320Hz - 3400Hz).
    """
    with wave.open(str(audio_path), "rb") as wf:
        sr = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    hop = max(int(round(sr * hop_sec)), 1)
    win_len = hop * 2
    num_frames = max((len(samples) - win_len) // hop, 1)

    window = np.hanning(win_len)
    freqs = np.fft.rfftfreq(win_len, d=1.0 / sr)

    vocal_mask = (freqs >= 320.0) & (freqs <= 3400.0)
    core_mask = (freqs >= 450.0) & (freqs <= 2600.0)
    bass_mask = freqs <= 220.0

    vocal_energy = np.zeros(num_frames, dtype=np.float32)
    onset_flux = np.zeros(num_frames, dtype=np.float32)
    prev_mag = None

    for i in range(num_frames):
        seg = samples[i * hop : i * hop + win_len]
        if len(seg) < win_len:
            seg = np.pad(seg, (0, win_len - len(seg)))
        spec = np.abs(np.fft.rfft(seg * window))

        v_e = float(np.mean(spec[vocal_mask])) + 1.5 * float(np.mean(spec[core_mask]))
        b_e = float(np.mean(spec[bass_mask])) if np.any(bass_mask) else 0.0
        ratio = v_e / (b_e * 0.35 + 1e-5)
        weight = min(1.0, max(0.15, ratio))
        vocal_energy[i] = v_e * weight

        if prev_mag is not None:
            diff = spec[vocal_mask] - prev_mag
            onset_flux[i] = float(np.mean(np.maximum(0.0, diff)))
        prev_mag = spec[vocal_mask]

    short_k = np.ones(8, dtype=np.float32) / 8.0
    vocal_smooth = np.convolve(vocal_energy, short_k, mode="same")

    p95 = max(float(np.percentile(vocal_smooth, 95)), 0.05)
    vocal_norm = np.clip(vocal_smooth / p95, 0.0, 1.0)

    long_win = min(160, max(num_frames // 4, 20))
    long_k = np.ones(long_win, dtype=np.float32) / float(long_win)
    local_mean = np.convolve(vocal_norm, long_k, mode="same")
    local_rel_energy = vocal_norm / np.maximum(local_mean, 0.12)

    f95 = max(float(np.percentile(onset_flux, 95)), 0.01)
    flux_norm = np.clip(onset_flux / f95, 0.0, 1.0)

    return vocal_norm, local_rel_energy, flux_norm, hop_sec


_DEMUCS_LOCK = threading.Lock()
_DEMUCS_STATE: dict = {
    "model": None,
    "vocals_idx": 3,
    "unavailable": False,
}

_CTC_ALIGNER_LOCK = threading.Lock()
_CTC_ALIGNER_STATE: dict = {
    "model": None,
    "tokenizer": None,
    "aligner": None,
    "sample_rate": 16000,
    "device": None,
    "unavailable": False,
}


def _get_demucs_model():
    """Lazy-load and cache Meta Demucs (`htdemucs`) once per process."""
    if _DEMUCS_STATE["unavailable"]:
        return None, 3
    if _DEMUCS_STATE["model"] is not None:
        return _DEMUCS_STATE["model"], _DEMUCS_STATE["vocals_idx"]

    with _DEMUCS_LOCK:
        if _DEMUCS_STATE["unavailable"]:
            return None, 3
        if _DEMUCS_STATE["model"] is not None:
            return _DEMUCS_STATE["model"], _DEMUCS_STATE["vocals_idx"]
        try:
            from demucs.pretrained import get_model

            logger.info("Loading Demucs neural vocal separation model (htdemucs)...")
            model = get_model("htdemucs")
            model.eval()
            vocals_idx = model.sources.index("vocals") if "vocals" in model.sources else 3
            _DEMUCS_STATE["model"] = model
            _DEMUCS_STATE["vocals_idx"] = vocals_idx
            return model, vocals_idx
        except ImportError:
            _DEMUCS_STATE["unavailable"] = True
            logger.info("demucs not installed; falling back to FFmpeg stereo vocal isolation.")
            return None, 3
        except Exception as exc:
            _DEMUCS_STATE["unavailable"] = True
            logger.warning("Could not load Demucs model (%s); falling back to FFmpeg filter.", exc)
            return None, 3


def _extract_stereo_vocal_isolated_wav(audio_path: Path, job_temp_dir: Path) -> Path:
    """
    Create a 16kHz mono WAV containing isolated lead vocals.
    Primary engine: Demucs (`htdemucs`) neural 4-stem source separation (`vocal_isolated_demucs.wav`),
    which cleanly strips drums/bass/guitars/synth even in center-panned live mixes.
    Fallback engine: FFmpeg Stereo Center-Vocal Isolation + Vocal Bandpass (`vocal_isolated_ctc.wav`).
    Validates source size/mtime via sidecar metadata so trimmed/updated audio always regenerates cleanly.
    """
    m4a_candidate = job_temp_dir / "extracted_audio.m4a"
    src_path = audio_path
    # Prefer extracted_audio.m4a only when extracted_audio.wav has NOT been freshly trimmed after it
    if (
        audio_path.name == "extracted_audio.wav"
        and m4a_candidate.exists()
        and m4a_candidate.stat().st_size > 1024
        and m4a_candidate.stat().st_mtime >= audio_path.stat().st_mtime - 2.0
    ):
        src_path = m4a_candidate

    if not src_path.exists():
        return audio_path

    src_stat = src_path.stat()
    meta_file = job_temp_dir / "vocal_isolated_demucs.meta.json"
    demucs_wav = job_temp_dir / "vocal_isolated_demucs.wav"

    if demucs_wav.exists() and demucs_wav.stat().st_size > 2048 and meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            if (
                meta.get("src_name") == src_path.name
                and int(meta.get("src_size", -1)) == src_stat.st_size
                and int(meta.get("src_mtime_ns", -1)) == src_stat.st_mtime_ns
            ):
                return demucs_wav
        except Exception:
            pass

    ffmpeg_bin = get_ffmpeg_binary()

    # 1. Try Neural Demucs Separation (`htdemucs`)
    demucs_model, vocals_idx = _get_demucs_model()
    if demucs_model is not None:
        try:
            import torch
            import torchaudio.functional as TAF
            from demucs.apply import apply_model

            proc = subprocess.run(
                [
                    ffmpeg_bin,
                    "-y",
                    "-i",
                    str(src_path),
                    "-f",
                    "f32le",
                    "-acodec",
                    "pcm_f32le",
                    "-ac",
                    "2",
                    "-ar",
                    str(demucs_model.samplerate),
                    "-loglevel",
                    "error",
                    "pipe:1",
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            pcm = np.frombuffer(proc.stdout, dtype=np.float32).copy()
            if pcm.size >= demucs_model.samplerate:
                wav_stereo = torch.from_numpy(pcm.reshape(-1, 2).T)  # (2, T)
                ref = wav_stereo.mean(dim=0)
                ref_mean = ref.mean()
                ref_std = ref.std().clamp(min=1e-6)
                wav_in = ((wav_stereo - ref_mean) / ref_std).unsqueeze(0)

                t0 = time.monotonic()
                with torch.inference_mode():
                    stems = apply_model(
                        demucs_model,
                        wav_in,
                        device="cpu",
                        shifts=1,
                        split=True,
                        overlap=0.25,
                        progress=False,
                    )
                vocals_stereo = stems[0, vocals_idx] * ref_std + ref_mean
                vocals_mono = vocals_stereo.mean(dim=0, keepdim=True)
                if demucs_model.samplerate != 16000:
                    vocals_16k = TAF.resample(vocals_mono, demucs_model.samplerate, 16000)
                else:
                    vocals_16k = vocals_mono

                samples_i16 = (
                    vocals_16k.squeeze(0).clamp(-1.0, 1.0).numpy() * 32767.0
                ).astype(np.int16)

                tmp_wav = job_temp_dir / "vocal_isolated_demucs.tmp.wav"
                with wave.open(str(tmp_wav), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(16000)
                    wf.writeframes(samples_i16.tobytes())
                tmp_wav.replace(demucs_wav)

                meta_file.write_text(
                    json.dumps(
                        {
                            "engine": "htdemucs",
                            "src_name": src_path.name,
                            "src_size": src_stat.st_size,
                            "src_mtime_ns": src_stat.st_mtime_ns,
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                logger.info(
                    "Demucs vocal stem extracted in %.2fs -> %s",
                    time.monotonic() - t0,
                    demucs_wav.name,
                )
                return demucs_wav
        except Exception as exc:
            logger.warning("Demucs vocal separation failed (%s); falling back to FFmpeg stereo filter.", exc)

    # 2. Fallback: FFmpeg Stereo Center-Vocal Isolation + Vocal Bandpass
    out_wav = job_temp_dir / "vocal_isolated_ctc.wav"
    if (
        out_wav.exists()
        and out_wav.stat().st_size > 2048
        and out_wav.stat().st_mtime >= audio_path.stat().st_mtime - 1.0
    ):
        return out_wav

    is_stereo = src_path.suffix.lower() in (".m4a", ".mp3", ".aac", ".ogg", ".flac", ".mp4")
    if src_path.suffix.lower() == ".wav":
        try:
            with wave.open(str(src_path), "rb") as wf:
                is_stereo = wf.getnchannels() >= 2
        except Exception:
            is_stereo = False

    af_chain = (
        "stereotools=mlev=1:slev=0.15,pan=mono|c0=0.5*c0+0.5*c1,"
        "highpass=f=180,lowpass=f=4200,afftdn=nf=-20,dynaudnorm=f=150:g=9"
        if is_stereo
        else "highpass=f=180,lowpass=f=4200,afftdn=nf=-20,dynaudnorm=f=150:g=9"
    )
    try:
        subprocess.run(
            [
                ffmpeg_bin,
                "-y",
                "-i",
                str(src_path),
                "-af",
                af_chain,
                "-ar",
                "16000",
                "-ac",
                "1",
                "-loglevel",
                "error",
                str(out_wav),
            ],
            check=True,
            capture_output=True,
        )
        if out_wav.exists() and out_wav.stat().st_size > 2048:
            return out_wav
    except Exception as exc:
        logger.debug("Stereo vocal isolation failed (%s), falling back to %s", exc, audio_path)
    return audio_path


# Phonetic Sinhala -> Meta MMS Roman character table (matching torchaudio.pipelines.MMS_FA tokens a..z, ')
_MMS_VALID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz'")

_MMS_SIN_CONSONANTS: Dict[str, str] = {
    "ක": "k",
    "ඛ": "k",
    "ග": "g",
    "ඝ": "g",
    "ඞ": "ng",
    "ඟ": "ng",
    "ච": "c",
    "ඡ": "c",
    "ජ": "j",
    "ඣ": "j",
    "ඤ": "ny",
    "ඥ": "gn",
    "ඦ": "nj",
    "ට": "t",
    "ඨ": "t",
    "ඩ": "d",
    "ඪ": "d",
    "ණ": "n",
    "ඬ": "nd",
    "ත": "t",
    "ථ": "t",
    "ද": "d",
    "ධ": "d",
    "න": "n",
    "ඳ": "nd",
    "ප": "p",
    "ඵ": "p",
    "බ": "b",
    "භ": "b",
    "ම": "m",
    "ඹ": "mb",
    "ය": "y",
    "ර": "r",
    "ල": "l",
    "ව": "v",
    "ශ": "s",
    "ෂ": "s",
    "ස": "s",
    "හ": "h",
    "ළ": "l",
    "ෆ": "f",
}

_MMS_SIN_INDEP_VOWELS: Dict[str, str] = {
    "අ": "a",
    "ආ": "a",
    "ඇ": "ae",
    "ඈ": "ae",
    "ඉ": "i",
    "ඊ": "i",
    "උ": "u",
    "ඌ": "u",
    "ඍ": "ru",
    "ඎ": "ru",
    "ඏ": "li",
    "ඐ": "li",
    "එ": "e",
    "ඒ": "e",
    "ඓ": "ai",
    "ඔ": "o",
    "ඕ": "o",
    "ඖ": "au",
}

_MMS_SIN_VOWEL_SIGNS: Dict[str, str] = {
    "\u0dca": "",     # ් hal kirima (suppresses inherent 'a')
    "\u0dcf": "a",    # ා
    "\u0dd0": "ae",   # ැ
    "\u0dd1": "ae",   # ෑ
    "\u0dd2": "i",    # ි
    "\u0dd3": "i",    # ී
    "\u0dd4": "u",    # ු
    "\u0dd6": "u",    # ූ
    "\u0dd8": "ru",   # ෘ
    "\u0dd9": "e",    # ෙ
    "\u0dda": "e",    # ේ
    "\u0ddb": "ai",   # ෛ
    "\u0ddc": "o",    # ො
    "\u0ddd": "o",    # ෝ
    "\u0dde": "au",   # ෞ
    "\u0ddf": "au",   # ෟ
    "\u0df2": "ru",   # ෲ
}


def _sinhala_word_to_mms_roman(word: str) -> str:
    """
    Convert a Sinhala, Singlish, or English word into Meta MMS_FA's normalized Roman vocabulary (`a..z`, `'`).
    Preserves Sinhala inherent `a` vowels while suppressing them on hal kirima (`්`), yansaya (`්‍ය`), and rakaransaya (`්‍ර`).
    """
    cleaned = normalize_sinhala_text(word or "").strip()
    if not cleaned:
        return ""

    out: List[str] = []
    pending_inherent_a = False

    def _flush_pending():
        nonlocal pending_inherent_a
        if pending_inherent_a:
            out.append("a")
            pending_inherent_a = False

    for ch in cleaned:
        if ch == "\u200d" or ch == "\u200c":
            # ZWJ / ZWNJ joiners in yansaya/rakaransaya/repaya: preceding hal-kirima already cleared pending 'a'
            continue
        if ch in _MMS_SIN_CONSONANTS:
            _flush_pending()
            out.append(_MMS_SIN_CONSONANTS[ch])
            pending_inherent_a = True
        elif ch in _MMS_SIN_VOWEL_SIGNS:
            # Replaces inherent 'a' with explicit vowel sign (or empty string on hal kirima)
            pending_inherent_a = False
            sign = _MMS_SIN_VOWEL_SIGNS[ch]
            if sign:
                out.append(sign)
        elif ch in _MMS_SIN_INDEP_VOWELS:
            _flush_pending()
            out.append(_MMS_SIN_INDEP_VOWELS[ch])
        elif ch == "\u0d82":  # ං anusvara (bindu)
            _flush_pending()
            out.append("ng")
        elif ch == "\u0d83":  # ඃ visarga
            _flush_pending()
            out.append("h")
        else:
            _flush_pending()
            low = ch.lower()
            if low in _MMS_VALID_CHARS:
                out.append(low)
            elif ord(low) > 127:
                try:
                    from unidecode import unidecode

                    for u_ch in unidecode(low).lower():
                        if u_ch in _MMS_VALID_CHARS:
                            out.append(u_ch)
                except Exception:
                    pass

    _flush_pending()
    roman = "".join(c for c in "".join(out) if c in _MMS_VALID_CHARS).strip("'")
    return roman


def _get_ctc_aligner_model():
    """
    Lazy-load and cache `torchaudio.pipelines.MMS_FA` (Meta MMS 300M Multilingual Forced Aligner)
    with star `*` support from the local checkpoint cache.
    """
    if _CTC_ALIGNER_STATE["unavailable"]:
        return None, None, None, 16000
    if _CTC_ALIGNER_STATE["model"] is not None:
        return (
            _CTC_ALIGNER_STATE["model"],
            _CTC_ALIGNER_STATE["tokenizer"],
            _CTC_ALIGNER_STATE["aligner"],
            _CTC_ALIGNER_STATE["sample_rate"],
        )

    with _CTC_ALIGNER_LOCK:
        if _CTC_ALIGNER_STATE["unavailable"]:
            return None, None, None, 16000
        if _CTC_ALIGNER_STATE["model"] is not None:
            return (
                _CTC_ALIGNER_STATE["model"],
                _CTC_ALIGNER_STATE["tokenizer"],
                _CTC_ALIGNER_STATE["aligner"],
                _CTC_ALIGNER_STATE["sample_rate"],
            )
        try:
            import torch
            import torchaudio

            bundle = torchaudio.pipelines.MMS_FA
            logger.info("Loading local AI Meta MMS_FA Forced Aligner (cpu)...")
            # Prefer pre-downloaded `mms_fa_model.pt` in torch hub checkpoints if present
            ckpt_path = Path(torch.hub.get_dir()) / "checkpoints" / "mms_fa_model.pt"
            if ckpt_path.exists() and ckpt_path.stat().st_size > 100_000_000:
                model = bundle.get_model(with_star=True, dl_kwargs={"file_name": "mms_fa_model.pt"})
            else:
                model = bundle.get_model(with_star=True)
            model.eval()
            tokenizer = bundle.get_tokenizer()
            aligner = bundle.get_aligner()
            sr = int(getattr(bundle, "sample_rate", 16000))

            _CTC_ALIGNER_STATE["model"] = model
            _CTC_ALIGNER_STATE["tokenizer"] = tokenizer
            _CTC_ALIGNER_STATE["aligner"] = aligner
            _CTC_ALIGNER_STATE["sample_rate"] = sr
            _CTC_ALIGNER_STATE["device"] = "cpu"
            return model, tokenizer, aligner, sr
        except Exception as exc:
            _CTC_ALIGNER_STATE["unavailable"] = True
            logger.warning("Could not load Meta MMS_FA Forced Aligner (%s); using ASR+DP fallback.", exc)
            return None, None, None, 16000


def _load_mono_16k_waveform(wav_path: Path, target_sr: int = 16000) -> np.ndarray:
    """Read 16kHz mono float32 waveform from a WAV file (with FFmpeg resample fallback)."""
    try:
        with wave.open(str(wav_path), "rb") as wf:
            if wf.getnchannels() == 1 and wf.getsampwidth() == 2 and wf.getframerate() == target_sr:
                raw = wf.readframes(wf.getnframes())
                return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    except Exception:
        pass

    ffmpeg_bin = get_ffmpeg_binary()
    proc = subprocess.run(
        [
            ffmpeg_bin,
            "-y",
            "-i",
            str(wav_path),
            "-f",
            "f32le",
            "-acodec",
            "pcm_f32le",
            "-ac",
            "1",
            "-ar",
            str(target_sr),
            "-loglevel",
            "error",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
    )
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


def _align_lines_with_ctc_forced_aligner(
    audio_path: Path,
    active_lines: List[str],
    job_temp_dir: Path,
    total_duration: float,
    language: str = "si",
    align_start_sec: Optional[float] = None,
    align_end_sec: Optional[float] = None,
    vocal_norm: Optional[np.ndarray] = None,
    hop_sec: float = 0.02,
    insert_inter_line_star: bool = True,
) -> Optional[List[LyricLine]]:
    """
    Run offline word-level phoneme forced alignment via Meta `torchaudio.pipelines.MMS_FA`
    on Demucs-isolated lead vocals (`vocal_isolated_demucs.wav`).
    Optionally restricts alignment to `[align_start_sec, align_end_sec]` and inserts `*` star
    tokens at line breaks to absorb instrumental intros, breathing gaps, and interludes.
    Returns `List[LyricLine]` with millisecond-level `WordTiming`s, or `None` if unavailable/mismatched.
    """
    if not active_lines:
        return None
    model, tokenizer, aligner, target_sr = _get_ctc_aligner_model()
    if model is None or tokenizer is None or aligner is None:
        return None

    line_words_list: List[List[str]] = []
    # For each (line_idx, word_idx), store roman token OR None if punctuation-only
    roman_plan: List[Tuple[int, int, str, Optional[str]]] = []
    for l_idx, ln in enumerate(active_lines):
        ws = [w for w in ln.strip().split(" ") if w]
        if not ws:
            continue
        cur_line_idx = len(line_words_list)
        line_words_list.append(ws)
        for w_idx, orig_w in enumerate(ws):
            rom = _sinhala_word_to_mms_roman(orig_w)
            roman_plan.append((cur_line_idx, w_idx, orig_w, rom if rom else None))

    # Build MMS_FA token word sequence with '*' star absorbers
    transcript_words: List[str] = ["*"]
    # Map token position inside `transcript_words` -> (line_idx, word_idx)
    tok_idx_to_word: Dict[int, Tuple[int, int]] = {}
    prev_l_idx = -1
    total_real_tokens = 0
    for l_idx, w_idx, orig_w, rom in roman_plan:
        if prev_l_idx != -1 and l_idx != prev_l_idx and insert_inter_line_star:
            transcript_words.append("*")
        prev_l_idx = l_idx
        if rom:
            tok_pos = len(transcript_words)
            transcript_words.append(rom)
            tok_idx_to_word[tok_pos] = (l_idx, w_idx)
            total_real_tokens += len(rom)
    transcript_words.append("*")

    if total_real_tokens == 0 or not tok_idx_to_word:
        return None

    vocal_wav = _extract_stereo_vocal_isolated_wav(audio_path, job_temp_dir)

    try:
        import torch

        samples = _load_mono_16k_waveform(vocal_wav, target_sr=target_sr)
        if len(samples) < int(target_sr * 0.4):
            return None

        wav_dur = len(samples) / float(target_sr)
        eff_total = min(total_duration, wav_dur) if total_duration > 0 else wav_dur

        reg_start = max(0.0, float(align_start_sec) - 0.12) if align_start_sec is not None else 0.0
        reg_end = (
            min(eff_total, float(align_end_sec) + 0.18)
            if align_end_sec is not None
            else eff_total
        )
        if reg_end - reg_start < 0.8:
            reg_start = 0.0
            reg_end = eff_total

        # Include up to 0.4s transformer context outside the bounded vocal region
        ctx_start = max(0.0, reg_start - 0.40)
        ctx_end = min(wav_dur, reg_end + 0.40)
        s_samp = int(round(ctx_start * target_sr))
        e_samp = max(s_samp + 320, int(round(ctx_end * target_sr)))
        seg_samples = samples[s_samp:e_samp]
        waveform = torch.from_numpy(seg_samples).unsqueeze(0)  # (1, T)

        t0 = time.monotonic()
        with torch.inference_mode():
            emission, _ = model(waveform)
            emission = emission[0].cpu()  # (F, 29) log-probs

        num_frames = int(emission.size(0))
        if num_frames < 10:
            return None
        frame_dur = (len(seg_samples) / float(target_sr)) / float(num_frames)

        # Crop emission strictly to [reg_start, reg_end] so trailing/leading silence or skips stay excluded
        f_lo = max(0, min(num_frames - 4, int(math.floor((reg_start - ctx_start) / frame_dur))))
        f_hi = max(f_lo + 4, min(num_frames, int(math.ceil((reg_end - ctx_start) / frame_dur))))
        emission_crop = emission[f_lo:f_hi]
        crop_origin_sec = ctx_start + f_lo * frame_dur

        # Ensure emission frames >= total character targets to guarantee forced_align feasibility
        tokenized = tokenizer(transcript_words)
        total_targets = sum(len(ts) for ts in tokenized)
        if emission_crop.size(0) < total_targets + 2:
            # Drop optional inter-line '*' first if tight; otherwise abort cleanly to DP fallback
            transcript_compact = ["*"]
            tok_compact_map: Dict[int, Tuple[int, int]] = {}
            for l_idx, w_idx, _, rom in roman_plan:
                if rom:
                    tok_compact_map[len(transcript_compact)] = (l_idx, w_idx)
                    transcript_compact.append(rom)
            transcript_compact.append("*")
            tokenized = tokenizer(transcript_compact)
            total_targets = sum(len(ts) for ts in tokenized)
            if emission_crop.size(0) < total_targets + 2:
                logger.debug(
                    "MMS_FA skipped: %d emission frames < %d phoneme targets.",
                    int(emission_crop.size(0)),
                    total_targets,
                )
                return None
            tok_idx_to_word = tok_compact_map

        aligned_spans = aligner(emission_crop, tokenized)

        # Gather per-word (start_sec, end_sec, score) across lines
        word_boxes: Dict[Tuple[int, int], Tuple[float, float, float]] = {}
        char_scores: List[float] = []
        for tok_pos, (l_idx, w_idx) in tok_idx_to_word.items():
            spans = aligned_spans[tok_pos]
            if not spans:
                continue
            w_s = crop_origin_sec + spans[0].start * frame_dur
            w_e = crop_origin_sec + spans[-1].end * frame_dur
            mean_sc = sum(sp.score for sp in spans) / len(spans)
            word_boxes[(l_idx, w_idx)] = (max(0.0, w_s), min(eff_total, w_e), mean_sc)
            char_scores.extend(sp.score for sp in spans)

        if not word_boxes:
            return None

        avg_align_score = sum(char_scores) / max(len(char_scores), 1)
        if avg_align_score < 0.015:
            logger.info("MMS_FA alignment confidence too low (%.3f); falling back to ASR+DP.", avg_align_score)
            return None

        # Assemble LyricLines with per-word WordTimings
        aligned_lines: List[LyricLine] = []
        for l_idx, ws in enumerate(line_words_list):
            n_w = len(ws)
            present = [word_boxes[(l_idx, wi)] for wi in range(n_w) if (l_idx, wi) in word_boxes]
            if not present:
                # Punctuation-only or missing line: place right after previous line
                prev_end = aligned_lines[-1].end if aligned_lines else reg_start
                l_start = round(min(max(0.0, total_duration - 0.65), prev_end + 0.04), 2)
                l_end = round(min(total_duration, l_start + 1.2), 2)
                aligned_lines.append(
                    LyricLine(
                        start=l_start,
                        end=l_end,
                        text=" ".join(ws),
                        words=_compute_word_timings_for_line(
                            " ".join(ws), l_start, l_end, np.ones(4, dtype=np.float32), 0.02
                        ),
                    )
                )
                continue

            raw_s = min(b[0] for b in present)
            raw_e = max(b[1] for b in present)

            # Trim trailing silence tail inside raw_e if Demucs vocal envelope shows early drop-off
            if vocal_norm is not None and len(vocal_norm) > 10 and (raw_e - raw_s) > 1.8:
                s_f = max(0, int(raw_s / hop_sec))
                e_f = min(len(vocal_norm) - 1, int(raw_e / hop_sec))
                # Walk backwards from e_f to find the last genuinely active vocal frame (> 0.11)
                last_act_f = e_f
                while last_act_f > s_f + int(0.8 / hop_sec) and float(vocal_norm[last_act_f]) < 0.09:
                    last_act_f -= 1
                trimmed_e = (last_act_f + 3) * hop_sec
                if trimmed_e < raw_e - 0.35:
                    raw_e = max(raw_s + 0.75, trimmed_e)

            l_start = round(max(0.0, raw_s), 2)
            l_end = round(min(total_duration, max(l_start + 0.65, raw_e + 0.08)), 2)

            if aligned_lines and l_start < aligned_lines[-1].end + 0.04:
                mid = round(0.5 * (aligned_lines[-1].end + l_start), 2)
                aligned_lines[-1].end = max(aligned_lines[-1].start + 0.50, round(mid - 0.02, 2))
                l_start = round(max(aligned_lines[-1].end + 0.04, mid + 0.02), 2)
                l_end = round(min(total_duration, max(l_start + 0.65, l_end)), 2)

            # Fill any non-romanizable punctuation tokens by linear interpolation between neighbors
            line_dur_ms = max(650, int(round((l_end - l_start) * 1000)))
            w_timings: List[WordTiming] = []
            for wi, orig_w in enumerate(ws):
                if (l_idx, wi) in word_boxes:
                    ws_abs, we_abs, _ = word_boxes[(l_idx, wi)]
                    s_ms = max(0, min(line_dur_ms - 120, int(round((ws_abs - l_start) * 1000))))
                    e_ms = max(s_ms + 110, min(line_dur_ms, int(round((we_abs - l_start) * 1000))))
                else:
                    prev_ms = w_timings[-1].end_ms if w_timings else 0
                    s_ms = min(line_dur_ms - 120, prev_ms)
                    e_ms = min(line_dur_ms, s_ms + 220)
                if w_timings and s_ms < w_timings[-1].start_ms:
                    s_ms = w_timings[-1].start_ms
                    e_ms = max(s_ms + 110, min(line_dur_ms, e_ms))
                w_timings.append(WordTiming(word=orig_w, start_ms=s_ms, end_ms=e_ms))

            aligned_lines.append(
                LyricLine(
                    start=l_start,
                    end=l_end,
                    text=" ".join(ws),
                    words=w_timings,
                )
            )

        if aligned_lines:
            logger.info(
                "Meta MMS_FA Forced Aligner synced %d lines (%d words, score=%.3f) in %.2fs -> %s",
                len(aligned_lines),
                len(word_boxes),
                avg_align_score,
                time.monotonic() - t0,
                vocal_wav.name,
            )
            return aligned_lines
        return None
    except Exception as exc:
        logger.warning("Meta MMS_FA Forced Aligner execution failed (%s), falling back to ASR+DP.", exc)
        return None


def _compute_word_timings_for_line(
    line_text: str,
    start_sec: float,
    end_sec: float,
    flux_slice: np.ndarray,
    hop_sec: float,
) -> List[WordTiming]:
    """
    Compute per-word acoustic onset timings (in ms relative to `start_sec`) inside a single
    lyric line using each word's Sinhala syllable/mora weight and local syllable onset flux peaks.
    """
    words = [w for w in line_text.strip().split(" ") if w]
    duration_ms = max(int(round((end_sec - start_sec) * 1000)), 400)
    fade_out_ms = min(240, duration_ms // 5)

    if len(words) <= 1:
        return [
            WordTiming(
                word=line_text.strip(),
                start_ms=0,
                end_ms=min(240, duration_ms - fade_out_ms),
            )
        ]

    word_weights = [count_sinhala_syllable_weight(w) for w in words]
    total_w = sum(word_weights)
    usable_span_ms = max(int(duration_ms * 0.72), len(words) * 140)

    timings: List[WordTiming] = []
    acc_weight = 0.0
    n_flux = len(flux_slice)

    for idx, (word, w_val) in enumerate(zip(words, word_weights)):
        nominal_start_ms = int(round((acc_weight / total_w) * usable_span_ms))
        acc_weight += w_val
        nominal_end_ms = int(round((acc_weight / total_w) * usable_span_ms))

        if idx > 0 and n_flux > 4:
            center_frame = min(int(round((nominal_start_ms / 1000.0) / hop_sec)), n_flux - 1)
            radius = max(int(round(0.12 / hop_sec)), 2)
            lo = max(1, center_frame - radius)
            hi = min(n_flux, center_frame + radius + 1)
            if hi > lo:
                peak_offset = int(np.argmax(flux_slice[lo:hi]))
                snapped_frame = lo + peak_offset
                nominal_start_ms = int(round(snapped_frame * hop_sec * 1000))

        fade_dur = max(160, min(nominal_end_ms - nominal_start_ms, 340))
        t_start = max(0, min(nominal_start_ms, duration_ms - fade_out_ms - 100))
        t_end = min(t_start + fade_dur, duration_ms - fade_out_ms)

        timings.append(WordTiming(word=word, start_ms=t_start, end_ms=max(t_start + 100, t_end)))

    return timings


_ASR_EMPTY_MEMO: dict[Tuple[str, int, str], float] = {}


def _recognize_google_via_httpx(
    audio_data,
    language: str = "si-LK",
    timeout: float = 8.0,
) -> str:
    """
    Send FLAC audio to Google Speech-to-Text (`https://www.google.com/speech-api/v2/recognize`)
    via our IPv4-first `httpx` client, delegating request/key construction to
    `speech_recognition`'s `create_request_builder` (optionally overridden via `GOOGLE_STT_API_KEY`).
    """
    from speech_recognition.recognizers.google import create_request_builder

    custom_key = os.getenv("GOOGLE_STT_API_KEY", "").strip() or None
    req_builder = create_request_builder(
        endpoint="https://www.google.com/speech-api/v2/recognize",
        key=custom_key,
        language=language,
        filter_level=0,
    )
    url = req_builder.build_url()
    headers = req_builder.build_headers(audio_data)
    flac_data = req_builder.build_data(audio_data)

    with create_resilient_sync_client(timeout=timeout) as client:
        resp = client.post(
            url,
            content=flac_data,
            headers=headers,
        )
        if resp.status_code != 200 or not resp.text:
            return ""
        for raw_line in resp.text.splitlines():
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                parsed = json.loads(raw_line)
            except Exception:
                continue
            results = parsed.get("result") or []
            if results and isinstance(results, list):
                alts = results[0].get("alternative") or []
                if alts and isinstance(alts, list):
                    best = max(alts, key=lambda a: float(a.get("confidence", 0.5)))
                    transcript = (best.get("transcript") or "").strip()
                    if transcript:
                        return transcript
    return ""


def _find_hint_start_line_index(
    lyric_lines: List[str],
    hint_texts: Optional[List[str]] = None,
) -> Optional[int]:
    """
    Check if a TikTok caption / search query (e.g. 'Horahin balanne', 'එදා පටන් පෙම් බැඳී',
    'තුන් තිස් පැයක්') matches a specific line inside `lyric_lines`.
    If it matches a mid-song line (or skips a title header line 0), returns that line index.
    """
    if not lyric_lines or not hint_texts:
        return None

    line_phonetics = [sinhala_phonetic_key(ln) for ln in lyric_lines]
    line_singlish_tokens = [
        [_canonical_singlish_word(t) for t in _normalize_latin_tokens(sinhala_to_singlish(ln)) if len(t) >= 2]
        for ln in lyric_lines
    ]

    best_idx: Optional[int] = None
    best_score = 0.0

    for raw_hint in hint_texts:
        if not raw_hint or not raw_hint.strip():
            continue
        hint_clean = normalize_sinhala_text(raw_hint)
        # 1. Check Sinhala Unicode phonetic match if hint contains Sinhala script
        if re.search(r"[\u0D80-\u0DFF]", hint_clean):
            hp = sinhala_phonetic_key(hint_clean)
            if len(hp) >= 5:
                for idx, lp in enumerate(line_phonetics):
                    if not lp:
                        continue
                    if hp in lp or lp in hp:
                        sc = min(len(hp), len(lp)) / max(len(hp), 1)
                        if sc > best_score:
                            best_score = sc
                            best_idx = idx
        # 2. Check Singlish / Romanized token match (e.g. 'Horahin balanne' -> line 1 'හොරැහින් බලන්නේ ඇයි එහෙනම්')
        h_tokens = [_canonical_singlish_word(t) for t in _normalize_latin_tokens(hint_clean) if len(t) >= 2]
        if len(h_tokens) >= 2:
            for idx, l_tokens in enumerate(line_singlish_tokens):
                if not l_tokens:
                    continue
                matched_toks = 0
                for ht in h_tokens[:5]:
                    if any(
                        ht == lt
                        or (len(ht) >= 4 and len(lt) >= 4 and (ht.startswith(lt[:4]) or lt.startswith(ht[:4])))
                        for lt in l_tokens
                    ):
                        matched_toks += 1
                ratio = matched_toks / min(len(h_tokens), 4)
                if matched_toks >= 2 and ratio >= 0.66:
                    # Prefer non-title lines (length >= 3 words) when line 0 is just a 2-word title header
                    sc = ratio + (0.08 if len(l_tokens) >= 3 else 0.0)
                    if sc > best_score:
                        best_score = sc
                        best_idx = idx

    return best_idx if best_score >= 0.65 else None


def _extract_asr_windows_sync(
    audio_path: Path,
    job_temp_dir: Path,
    total_duration: float,
    language: str = "si",
    force_refresh: bool = False,
) -> List[Tuple[float, float, str]]:
    """
    Run parallel overlapping Speech-to-Text (`si-LK` or `en-US`) across 4.0-second windows
    (every 2.0s stride) on both a normalized vocal-bandpass filtered track and raw song audio.
    Caches non-empty results on disk in `job_temp_dir / "asr_windows_<lang>.json"` and
    memoizes empty runs in memory for 25s so sequential multi-site checks don't repeat ASR.
    """
    cache_file = job_temp_dir / f"asr_windows_{language.lower()}.json"
    audio_stat = audio_path.stat() if audio_path.exists() else None
    audio_size = audio_stat.st_size if audio_stat else 0
    audio_mtime_ns = audio_stat.st_mtime_ns if audio_stat else 0
    had_empty_disk_cache = False

    if not force_refresh and cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if (
                cached.get("vocal_engine") == "demucs_v1"
                and cached.get("audio_size") == audio_size
                and cached.get("audio_mtime_ns") == audio_mtime_ns
                and cached.get("windows")
            ):
                return [(float(w[0]), float(w[1]), str(w[2])) for w in cached["windows"]]
            if not cached.get("windows"):
                had_empty_disk_cache = True
        except Exception:
            pass

    sr_lang = "si-LK" if language.lower().startswith("si") else "en-US"
    memo_key = (str(audio_path.resolve()), audio_size, sr_lang)
    if force_refresh:
        _ASR_EMPTY_MEMO.pop(memo_key, None)
    last_empty_ts = _ASR_EMPTY_MEMO.get(memo_key, 0.0)
    if not force_refresh and (time.monotonic() - last_empty_ts) < 25.0:
        return []

    try:
        import speech_recognition as sr
    except ImportError:
        logger.warning("SpeechRecognition package not installed; skipping ASR window extraction.")
        return []

    # Create Demucs Neural Isolated Lead Vocal WAV (fallback to bandpass WAV)
    vocal_wav = _extract_stereo_vocal_isolated_wav(audio_path, job_temp_dir)
    if vocal_wav == audio_path:
        vocal_wav = job_temp_dir / "vocal_filtered_asr.wav"
        if not vocal_wav.exists() or had_empty_disk_cache or (vocal_wav.stat().st_mtime < audio_path.stat().st_mtime - 1.0):
            try:
                ffmpeg_bin = get_ffmpeg_binary()
                subprocess.run(
                    [
                        ffmpeg_bin,
                        "-y",
                        "-i",
                        str(audio_path),
                        "-af",
                        "highpass=f=200,lowpass=f=3800,afftdn=nf=-22,dynaudnorm=f=150:g=11",
                        "-ar",
                        "16000",
                        "-ac",
                        "1",
                        "-loglevel",
                        "error",
                        str(vocal_wav),
                    ],
                    check=True,
                    capture_output=True,
                )
            except Exception as exc:
                logger.warning("Vocal bandpass filter failed, using raw audio only: %s", exc)
                vocal_wav = audio_path

    candidate_wavs: List[Path] = []
    if vocal_wav != audio_path and vocal_wav.exists():
        candidate_wavs.append(vocal_wav)
    if audio_path.exists():
        candidate_wavs.append(audio_path)

    win_len = 4.0
    stride = 2.0
    starts: List[float] = []
    cur = 0.0
    while cur < max(0.5, total_duration - 1.2):
        starts.append(round(cur, 2))
        cur += stride

    overall_budget_sec = min(28.0, max(12.0, len(starts) * 1.1))
    deadline = time.monotonic() + overall_budget_sec
    logger.info(
        "Running ASR (%s) across %d windows (%.1fs, budget=%.1fs, vocal=%s)...",
        sr_lang,
        len(starts),
        total_duration,
        overall_budget_sec,
        candidate_wavs[0].name if candidate_wavs else audio_path.name,
    )
    logged_req_err = False

    def _transcribe_slice(start_s: float, dur_s: float, req_timeout: float) -> str:
        nonlocal logged_req_err
        rec = sr.Recognizer()
        rec.operation_timeout = int(max(4, math.ceil(req_timeout)))
        best_text = ""
        for cand_wav in candidate_wavs:
            if time.monotonic() >= deadline:
                break
            try:
                with sr.AudioFile(str(cand_wav)) as src:
                    if start_s > 0:
                        frame_offset = int(start_s * src.SAMPLE_RATE)
                        src.audio_reader.setpos(min(frame_offset, max(0, src.audio_reader.getnframes() - 1)))
                    audio_data = rec.record(src, duration=dur_s)
                txt = _recognize_google_via_httpx(audio_data, language=sr_lang, timeout=req_timeout)
                if txt and txt.strip():
                    norm = normalize_sinhala_text(txt.strip())
                    if len(norm) > len(best_text):
                        best_text = norm
                    # If clean vocal track yielded a full multi-char phrase, no need to re-query raw mix
                    if len(best_text) >= 6:
                        return best_text
            except Exception as exc:
                if not logged_req_err:
                    logged_req_err = True
                    logger.warning("Google ASR request error at %.1fs: %s", start_s, exc)
                continue
        return best_text

    def _transcribe_one(start_s: float) -> Tuple[float, float, str]:
        dur_s = min(win_len, max(1.2, total_duration - start_s))
        end_s = round(start_s + dur_s, 2)
        return (start_s, end_s, _transcribe_slice(start_s, dur_s, req_timeout=7.5))

    windows: List[Tuple[float, float, str]] = []
    results_by_idx: dict[int, Tuple[float, float, str]] = {}
    pool = ThreadPoolExecutor(max_workers=6)
    try:
        future_to_idx = {pool.submit(_transcribe_one, s): idx for idx, s in enumerate(starts)}
        remaining = max(0.5, deadline - time.monotonic())
        done_futs, not_done_futs = concurrent.futures.wait(
            future_to_idx.keys(),
            timeout=remaining,
        )
        for fut in done_futs:
            idx = future_to_idx[fut]
            try:
                results_by_idx[idx] = fut.result()
            except Exception:
                pass
        for fut in not_done_futs:
            fut.cancel()
        if not_done_futs:
            logger.warning(
                "ASR budget (%.1fs) reached with %d/%d windows completed.",
                overall_budget_sec,
                len(done_futs),
                len(starts),
            )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    results = [results_by_idx.get(i, (s, round(min(total_duration, s + win_len), 2), "")) for i, s in enumerate(starts)]

    for ws, we, wt in results:
        if wt:
            windows.append((ws, we, wt))

    # Retry tighter [0.0, 2.5s] slice if the opening 4s window returned empty
    if (
        time.monotonic() < deadline
        and starts
        and starts[0] == 0.0
        and results
        and not results[0][2]
        and total_duration >= 2.0
    ):
        short_dur = min(2.5, total_duration)
        txt = _transcribe_slice(0.0, short_dur, req_timeout=5.5)
        if txt:
            windows.insert(0, (0.0, round(short_dur, 2), txt))

    # Retry tighter tail slice if the final window returned empty
    if time.monotonic() < deadline and results and not results[-1][2] and total_duration >= 3.0:
        tail_start = round(max(0.0, total_duration - 3.2), 2)
        tail_dur = round(total_duration - tail_start, 2)
        txt = _transcribe_slice(tail_start, tail_dur, req_timeout=5.5)
        if txt and (not windows or tail_start > windows[-1][0] + 0.5):
            windows.append((tail_start, round(total_duration, 2), txt))

    if windows:
        _ASR_EMPTY_MEMO.pop(memo_key, None)
        try:
            cache_file.write_text(
                json.dumps(
                    {
                        "vocal_engine": "demucs_v1",
                        "audio_size": audio_size,
                        "audio_mtime_ns": audio_mtime_ns,
                        "windows": windows,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass
    else:
        _ASR_EMPTY_MEMO[memo_key] = time.monotonic()

    logger.info("Extracted %d non-empty ASR windows (lang=%s) across %.1fs", len(windows), sr_lang, total_duration)
    return windows


def _match_line_to_asr_window(
    line_phonetic: str,
    ws: float,
    we: float,
    wt: str,
    total_duration: float,
) -> Optional[Tuple[float, float, float, float, float]]:
    """
    Check if `line_phonetic` matches inside ASR window `(ws, we, wt)`.
    Returns `(score, est_line_start, est_line_end, match_start, match_end)` or None.
    """
    wp = sinhala_phonetic_key(wt)
    if len(line_phonetic) < 3 or len(wp) < 3:
        return None

    sm = difflib.SequenceMatcher(None, line_phonetic, wp)
    blocks = [b for b in sm.get_matching_blocks() if b.size >= 2]
    matched = sum(b.size for b in blocks)
    if matched < 4:
        return None

    a0 = min(b.a for b in blocks)
    a1 = max(b.a + b.size for b in blocks)
    b0 = min(b.b for b in blocks)
    b1 = max(b.b + b.size for b in blocks)

    span_a = max(a1 - a0, 1)
    span_b = max(b1 - b0, 1)
    density = matched / max(min(span_a, span_b), 4)

    line_cov = matched / len(line_phonetic)
    win_cov = matched / len(wp)
    prefix_bonus = 0.15 if a0 <= 2 else 0.0
    score = 0.45 * line_cov + 0.35 * max(line_cov, win_cov) + 0.20 * min(1.0, density) + prefix_bonus

    is_strong_partial = win_cov >= 0.70 and matched >= 4 and density >= 0.45
    if (score < 0.56 or density < 0.48) and not is_strong_partial:
        return None

    dur = we - ws
    ra0 = a0 / len(line_phonetic)
    ra1 = a1 / len(line_phonetic)
    est_total_dur = max(2.0, min(6.2, len(line_phonetic) * 0.22))

    # If the window only captured a short prefix of a longer line, the line started near the end of [ws, we]!
    if len(wp) < 0.62 * len(line_phonetic) and a0 <= 2 and a1 < 0.68 * len(line_phonetic):
        prefix_dur = max(1.3, min(2.2, matched * 0.24))
        ms = max(ws, we - prefix_dur)
        me = we
        est_s = ms
        est_e = min(total_duration, est_s + est_total_dur)
    # If the window only captured a short suffix of a longer line, the line ended near the start of [ws, we]!
    elif len(wp) < 0.62 * len(line_phonetic) and a0 >= 0.35 * len(line_phonetic) and a1 >= len(line_phonetic) - 2:
        suffix_dur = max(1.3, min(2.4, matched * 0.24))
        ms = ws
        me = min(we, ws + suffix_dur)
        est_e = me
        est_s = max(0.0, est_e - est_total_dur)
    else:
        ms = ws + (b0 / len(wp)) * dur
        me = ws + (b1 / len(wp)) * dur
        est_s = max(0.0, ms - ra0 * est_total_dur)
        est_e = min(total_duration, me + (1.0 - ra1) * est_total_dur)

    return (score, est_s, est_e, ms, me)


def _detect_opening_voice_start(
    vocal_norm: np.ndarray,
    hop_sec: float,
    max_search_sec: float = 12.0,
) -> float:
    """
    Detect whether vocals are already active right from 0.0s (mid-singing clip) or
    find the exact opening vocal timestamp inside `[0.0, max_search_sec]`.
    Requires sustained frames (>= 3 frames) so short drum/breath transients don't trigger early.
    """
    if len(vocal_norm) == 0:
        return 0.0
    immediate_end = min(len(vocal_norm), max(4, int(0.35 / hop_sec)))
    if int(np.sum(vocal_norm[:immediate_end] >= 0.16)) >= 3 or float(np.percentile(vocal_norm[:immediate_end], 75)) >= 0.18:
        return 0.0

    search_end = min(len(vocal_norm) - 3, int(max_search_sec / hop_sec))
    for f in range(immediate_end, search_end):
        win = vocal_norm[f : f + 5]
        if float(vocal_norm[f]) >= 0.17 and int(np.sum(win >= 0.15)) >= 3:
            return round(max(0.0, (f - 1) * hop_sec), 2)
    return 0.0


def _detect_closing_voice_end(
    vocal_norm: np.ndarray,
    hop_sec: float,
    total_duration: float,
    max_search_sec: float = 12.0,
) -> float:
    """
    Detect whether vocals continue right up to `total_duration` or find the exact
    closing vocal timestamp inside `[total_duration - max_search_sec, total_duration]`.
    """
    if len(vocal_norm) == 0:
        return round(total_duration, 2)
    num_frames = len(vocal_norm)
    immediate_start = max(0, num_frames - max(4, int(0.35 / hop_sec)))
    if int(np.sum(vocal_norm[immediate_start:] >= 0.16)) >= 3 or float(np.percentile(vocal_norm[immediate_start:], 75)) >= 0.18:
        return round(total_duration, 2)

    search_start = max(4, num_frames - int(max_search_sec / hop_sec))
    for f in range(immediate_start, search_start - 1, -1):
        lo = max(0, f - 4)
        win = vocal_norm[lo : f + 1]
        if float(vocal_norm[f]) >= 0.17 and int(np.sum(win >= 0.15)) >= 3:
            return round(min(total_duration, (f + 2) * hop_sec), 2)
    return round(max(0.5, total_duration - 0.15), 2)


def _build_auto_captions_from_asr_windows(
    asr_windows: List[Tuple[float, float, str]],
    vocal_norm: np.ndarray,
    local_rel: np.ndarray,
    flux_env: np.ndarray,
    hop_sec: float,
    total_duration: float,
) -> List[LyricLine]:
    """
    Convert overlapping 4.0s ASR windows into clean, deduplicated, breath-synced LyricLines
    for Auto-Caption Mode (when no manual lyrics are provided).
    """
    merged: List[List] = []
    for ws, we, wt in asr_windows:
        words = [w for w in wt.strip().split() if w]
        if not words:
            continue
        if not merged:
            merged.append([ws, we, words])
            continue

        prev_s, prev_e, prev_words = merged[-1]
        if ws < prev_e + 0.5:
            prev_txt = " ".join(prev_words)
            curr_txt = " ".join(words)
            p_prev = sinhala_phonetic_key(prev_txt)
            p_curr = sinhala_phonetic_key(curr_txt)

            # Case 1: previous window is a short prefix/single word leading into current fuller window
            if len(prev_words) <= 2 and len(words) >= 2 and ws <= prev_s + 2.2:
                if difflib.SequenceMatcher(None, p_prev[:4], p_curr[:4]).ratio() >= 0.50:
                    merged[-1] = [prev_s, we, [prev_words[0]] + words[1:]]
                    continue

            if (
                len(p_prev) <= len(p_curr)
                and difflib.SequenceMatcher(None, p_prev, p_curr[: len(p_prev) + 2]).ratio() >= 0.64
            ):
                merged_start = prev_s if ws <= prev_s + 2.2 else ws
                merged[-1] = [merged_start, we, words]
                continue

            # Case 2: current window is already contained at the end of previous window
            if (
                len(p_curr) <= len(p_prev)
                and difflib.SequenceMatcher(None, p_curr, p_prev[-len(p_curr) - 3 :]).ratio() >= 0.60
            ):
                merged[-1][1] = max(prev_e, we - 0.5)
                continue

            # Case 3: suffix of prev_words overlaps with prefix of current words
            overlap_cut = 0
            for k in range(min(len(prev_words), len(words)), 0, -1):
                suf = sinhala_phonetic_key(" ".join(prev_words[-k:]))
                pre = sinhala_phonetic_key(" ".join(words[:k]))
                if len(suf) >= 3 and difflib.SequenceMatcher(None, suf, pre).ratio() >= 0.68:
                    overlap_cut = k
                    break

            if overlap_cut > 0:
                rem_words = words[overlap_cut:]
                bound_t = min(prev_e, ws + 0.5)
                merged[-1][1] = bound_t
                if rem_words:
                    merged.append([bound_t, we, rem_words])
                continue
            else:
                bound_t = min(prev_e, ws + 0.35)
                merged[-1][1] = bound_t
                merged.append([bound_t, we, words])
        else:
            merged.append([ws, we, words])

    # Split any overly long caption (> 6 words and > 3.6s) into 2 TikTok-sized lines at a breath dip
    refined_segments: List[Tuple[float, float, str]] = []
    num_frames = len(local_rel)
    for s_val, e_val, wlist in merged:
        s = float(s_val)
        e = float(e_val)
        if len(wlist) >= 6 and (e - s) >= 3.4:
            mid_w = len(wlist) // 2
            mid_t = 0.5 * (s + e)
            f_lo = max(0, int((mid_t - 0.6) / hop_sec))
            f_hi = min(num_frames - 1, int((mid_t + 0.6) / hop_sec))
            if f_hi > f_lo:
                split_t = (f_lo + int(np.argmin(local_rel[f_lo : f_hi + 1]))) * hop_sec
            else:
                split_t = mid_t
            refined_segments.append((s, split_t, " ".join(wlist[:mid_w])))
            refined_segments.append((split_t, e, " ".join(wlist[mid_w:])))
        else:
            refined_segments.append((s, e, " ".join(wlist)))

    auto_lines: List[LyricLine] = []
    opening_voice_sec = _detect_opening_voice_start(vocal_norm, hop_sec, max_search_sec=2.0)
    closing_voice_sec = _detect_closing_voice_end(vocal_norm, hop_sec, total_duration, max_search_sec=2.0)
    non_empty_segments = [(s, e, text) for s, e, text in refined_segments if text.strip()]

    for idx_seg, (s, e, text) in enumerate(non_empty_segments):
        is_last_seg = idx_seg == len(non_empty_segments) - 1
        s_frame = max(0, min(num_frames - 1, int(s / hop_sec)))
        e_frame = max(s_frame + 10, min(num_frames - 1, int(e / hop_sec)))

        if not auto_lines and s_frame <= int(0.25 / hop_sec) and opening_voice_sec == 0.0:
            # Voice is already active right at 0.0s — never trim the opening!
            s_frame = 0
        else:
            # Advance start slightly if beginning of window is silence
            max_trim = min(int(0.45 / hop_sec), (e_frame - s_frame) // 3)
            for f in range(s_frame, s_frame + max_trim):
                if vocal_norm[f] >= 0.22 and flux_env[f] >= 0.14:
                    s_frame = max(s_frame, f - 1)
                    break

        start_sec = round(s_frame * hop_sec, 2)
        if is_last_seg and closing_voice_sec >= total_duration - 0.05 and e >= total_duration - 0.25:
            e_frame = num_frames - 1
            end_sec = round(total_duration, 2)
        else:
            end_sec = round(min(total_duration, max(start_sec + 1.0, e_frame * hop_sec - 0.06)), 2)
        flux_slice = flux_env[s_frame : max(s_frame + 2, e_frame)]
        word_timings = _compute_word_timings_for_line(
            line_text=text,
            start_sec=start_sec,
            end_sec=end_sec,
            flux_slice=flux_slice,
            hop_sec=hop_sec,
        )
        auto_lines.append(
            LyricLine(
                start=start_sec,
                end=end_sec,
                text=text,
                words=word_timings,
            )
        )

    # Guard: if ASR's first captured window started slightly late (e.g. 0.2s..2.5s) yet vocals
    # were already audible at 0.0s, pull the first caption start back to opening_voice_sec.
    if auto_lines and 0.15 < auto_lines[0].start <= 2.5:
        gap_f = min(num_frames, int(auto_lines[0].start / hop_sec))
        if gap_f > 2 and float(np.max(vocal_norm[:gap_f])) >= 0.20 and float(np.mean(vocal_norm[:gap_f])) >= 0.12:
            auto_lines[0].start = opening_voice_sec
            sf = int(opening_voice_sec / hop_sec)
            ef = max(sf + 2, int(auto_lines[0].end / hop_sec))
            auto_lines[0].words = _compute_word_timings_for_line(
                line_text=auto_lines[0].text,
                start_sec=auto_lines[0].start,
                end_sec=auto_lines[0].end,
                flux_slice=flux_env[sf:ef],
                hop_sec=hop_sec,
            )

    # Guard: if ASR's last captured window ended slightly early yet vocals continued
    # up to closing_voice_sec (near the end of the song), extend the final caption cleanly.
    if auto_lines and 0.15 < (closing_voice_sec - auto_lines[-1].end) <= 2.5:
        gap_s = max(0, int(auto_lines[-1].end / hop_sec))
        tail_f = min(num_frames, int(closing_voice_sec / hop_sec))
        if (
            tail_f - gap_s > 2
            and float(np.max(vocal_norm[gap_s:tail_f])) >= 0.20
            and float(np.mean(vocal_norm[gap_s:tail_f])) >= 0.12
        ):
            auto_lines[-1].end = round(min(total_duration, closing_voice_sec), 2)
            sf = max(0, int(auto_lines[-1].start / hop_sec))
            ef = max(sf + 2, tail_f)
            auto_lines[-1].words = _compute_word_timings_for_line(
                line_text=auto_lines[-1].text,
                start_sec=auto_lines[-1].start,
                end_sec=auto_lines[-1].end,
                flux_slice=flux_env[sf:ef],
                hop_sec=hop_sec,
            )

    return auto_lines


def _align_manual_lines_with_asr_anchors(
    lyric_lines: List[str],
    asr_windows: List[Tuple[float, float, str]],
    vocal_norm: np.ndarray,
    local_rel: np.ndarray,
    flux_env: np.ndarray,
    hop_sec: float,
    total_duration: float,
    audio_path: Optional[Path] = None,
    job_temp_dir: Optional[Path] = None,
    language: str = "si",
    clip_offset_sec: float = 0.0,
    hint_texts: Optional[List[str]] = None,
    lock_first_line: bool = False,
) -> Optional[List[LyricLine]]:
    """
    Align manual Sinhala/English lyric lines to the song's real singing timing using
    ASR window anchors + breath-dip interpolation.
    Handles mid-song TikTok clips (where singing starts in the middle of the lyric sheet
    or where a repeated Chorus appears after a verse) and trims un-sung leading/trailing lines.
    """
    if not lyric_lines or not asr_windows:
        return None

    n_lines = len(lyric_lines)
    num_frames = len(local_rel)
    hint_idx = _find_hint_start_line_index(lyric_lines, hint_texts)
    expected_pos_ratio = (
        min(0.85, max(0.10, (clip_offset_sec - 8.0) / max(90.0, n_lines * 4.0)))
        if clip_offset_sec > 8.0 and n_lines > 8
        else None
    )

    candidates: List[Tuple[int, float, float, float, float]] = []
    for i, line in enumerate(lyric_lines):
        lp = sinhala_phonetic_key(line)
        pos_bonus = 0.0
        if lock_first_line:
            if i == 0:
                pos_bonus += 0.35
            elif i <= 2:
                pos_bonus += 0.15
        else:
            if expected_pos_ratio is not None:
                line_ratio = i / max(1, n_lines - 1)
                pos_bonus += 0.08 * max(0.0, 1.0 - abs(line_ratio - expected_pos_ratio) * 2.0)
            if hint_idx is not None and i >= hint_idx:
                pos_bonus += 0.06 * max(0.0, 1.0 - (i - hint_idx) / 8.0)
        for ws, we, wt in asr_windows:
            m = _match_line_to_asr_window(lp, ws, we, wt, total_duration)
            if m:
                sc, es, ee, ms, me = m
                if lock_first_line and i > 0 and es < i * 1.35:
                    continue
                if lock_first_line and i == 0 and es <= 4.0:
                    pos_bonus_win = pos_bonus + 0.25
                else:
                    pos_bonus_win = pos_bonus
                candidates.append((i, sc + pos_bonus_win, es, ee, 0.5 * (ms + me)))

    if not candidates:
        return None

    candidates.sort(key=lambda x: (x[0], x[2]))
    K = len(candidates)
    dp = [c[1] for c in candidates]
    prev = [-1] * K

    for k in range(K):
        li_k, sc_k, es_k, ee_k, mid_k = candidates[k]
        for j in range(k):
            li_j, sc_j, es_j, ee_j, mid_j = candidates[j]
            if li_k > li_j:
                line_gap = li_k - li_j
                time_gap = es_k - es_j
                if time_gap >= max(1.1, line_gap * 1.25) and mid_k > mid_j + 1.0:
                    skip_pen = (line_gap - 1) * 0.10
                    cand_val = dp[j] + sc_k - skip_pen
                    if cand_val > dp[k]:
                        dp[k] = cand_val
                        prev[k] = j

    # Penalize chains that lock onto an early Chorus occurrence (e.g. Line 0 at t=18s)
    # when there are 15+ seconds of active vocals BEFORE `first_es` that require preceding lines,
    # or lock onto a final Chorus occurrence when there are active vocals AFTER `last_ee`.
    adj_scores: List[float] = []
    chains_list: List[List[Tuple[int, float, float, float, float]]] = []
    for k in range(K):
        ch: List[Tuple[int, float, float, float, float]] = []
        curr = k
        while curr != -1:
            ch.append(candidates[curr])
            curr = prev[curr]
        ch.reverse()
        chains_list.append(ch)

        f_li, _, f_es, _, _ = ch[0]
        l_li, _, _, l_ee, _ = ch[-1]
        pre_f = min(num_frames, max(0, int(max(0.0, f_es - 0.2) / hop_sec)))
        post_s = min(num_frames, max(0, int(min(total_duration, l_ee + 0.2) / hop_sec)))
        pre_active_sec = float(np.sum(vocal_norm[:pre_f] >= 0.18)) * hop_sec if pre_f > 2 else 0.0
        post_active_sec = float(np.sum(vocal_norm[post_s:] >= 0.18)) * hop_sec if post_s < num_frames - 2 else 0.0

        req_pre_lines = int(round(pre_active_sec / 2.8))
        req_post_lines = int(round(post_active_sec / 2.8))
        pre_deficit = max(0, req_pre_lines - f_li)
        post_deficit = max(0, req_post_lines - (n_lines - 1 - l_li))
        lock_skip_pen = (f_li * 0.45) if lock_first_line else 0.0
        adj_scores.append(dp[k] - 0.32 * pre_deficit - 0.32 * post_deficit - lock_skip_pen)

    best_k = int(np.argmax(adj_scores))
    chain = chains_list[best_k]

    expected_clip_lines = max(2, int(total_duration / 2.8))
    min_required_anchors = min(len(lyric_lines), max(1, min(expected_clip_lines // 3, 3)))
    avg_anchor_score = sum(c[1] for c in chain) / max(len(chain), 1)
    if len(chain) == 1 and avg_anchor_score < 0.65:
        return None
    if len(chain) < min_required_anchors or (len(lyric_lines) > 15 and avg_anchor_score < 0.40):
        return None

    # Trim extra un-sung leading/trailing lines if audio clip is only a short excerpt of a full song,
    # while keeping only the exact number of preceding lines needed to cover vocals in [opening_voice_sec, first_es].
    first_anchor_idx, _, first_es, _, _ = chain[0]
    last_anchor_idx, _, last_es, last_ee, _ = chain[-1]
    opening_voice_sec = _detect_opening_voice_start(vocal_norm, hop_sec, max_search_sec=min(12.0, max(0.8, first_es + 0.5)))
    closing_voice_sec = _detect_closing_voice_end(
        vocal_norm, hop_sec, total_duration, max_search_sec=min(12.0, max(0.8, total_duration - last_ee + 0.5))
    )

    start_slice = 0
    if not lock_first_line and first_anchor_idx > 0:
        sung_span = max(0.0, first_es - opening_voice_sec)
        if first_es <= 1.25 and sung_span < 1.1:
            start_slice = first_anchor_idx
            logger.info(
                "Audio clip starts at Line %d (%.2fs); trimming %d un-sung leading lines.",
                first_anchor_idx + 1,
                first_es,
                start_slice,
            )
        else:
            anchor_chorus_keys = {
                sinhala_phonetic_key(lyric_lines[idx_c])
                for idx_c in range(first_anchor_idx, min(n_lines, first_anchor_idx + 4))
                if len(sinhala_phonetic_key(lyric_lines[idx_c])) >= 5
            }
            acc_dur = 0.0
            k_keep = 0
            for idx_prev in range(first_anchor_idx - 1, -1, -1):
                if hint_idx is not None and idx_prev < hint_idx:
                    break
                prev_key = sinhala_phonetic_key(lyric_lines[idx_prev])
                # Stop at stanza boundary if we walk backwards into an earlier repetition of the anchored chorus
                if k_keep >= 2 and prev_key in anchor_chorus_keys:
                    break
                est_d = max(
                    2.55,
                    min(4.5, 1.30 + 0.165 * count_sinhala_syllable_weight(lyric_lines[idx_prev])),
                )
                if acc_dur + est_d * 0.78 <= sung_span:
                    acc_dur += est_d
                    k_keep += 1
                else:
                    break
            gap_frames = min(num_frames, max(1, int(max(0.0, first_es - 0.15) / hop_sec)))
            if (
                sung_span >= 1.5
                and k_keep == 0
                and float(np.max(vocal_norm[:gap_frames])) >= 0.22
                and float(np.mean(vocal_norm[:gap_frames])) >= 0.13
            ):
                k_keep = 1
            start_slice = max(0, first_anchor_idx - k_keep)
            if start_slice > 0:
                logger.info(
                    "Mid-song clip starts ~Line %d (anchor Line %d at %.2fs); trimmed %d leading lines.",
                    start_slice + 1,
                    first_anchor_idx + 1,
                    first_es,
                    start_slice,
                )

    end_slice = len(lyric_lines)
    trimmed_tail = False
    remaining_lines_after_last = len(lyric_lines) - 1 - last_anchor_idx
    if remaining_lines_after_last > 0 and (
        last_ee >= total_duration - 2.2
        or (total_duration - last_ee) < remaining_lines_after_last * 1.6
    ):
        tail_start_f = min(num_frames - 1, max(0, int((last_ee + 0.15) / hop_sec)))
        tail_end_f = min(num_frames, max(tail_start_f + 1, int(closing_voice_sec / hop_sec)))
        has_closing_vocals = (
            (closing_voice_sec - last_ee) >= 1.5
            and tail_end_f > tail_start_f + 2
            and float(np.max(vocal_norm[tail_start_f:tail_end_f])) >= 0.22
            and float(np.mean(vocal_norm[tail_start_f:tail_end_f])) >= 0.13
        )
        if has_closing_vocals:
            sung_tail = max(0.8, closing_voice_sec - last_ee)
            k_keep_tail = min(remaining_lines_after_last, max(1, int(round(sung_tail / 2.6))))
            end_slice = last_anchor_idx + 1 + k_keep_tail
            logger.info(
                "Detected closing vocals (%.2fs -> %.2fs) after Line %d; keeping %d trailing lyric line(s).",
                last_ee,
                closing_voice_sec,
                last_anchor_idx + 1,
                k_keep_tail,
            )
        else:
            logger.info(
                "Audio clip ends at Line %d (%.2fs / %.2fs); trimming %d un-sung trailing lines.",
                last_anchor_idx + 1,
                last_ee,
                total_duration,
                remaining_lines_after_last,
            )
            end_slice = last_anchor_idx + 1
            trimmed_tail = True

    active_lines = lyric_lines[start_slice:end_slice]
    if not active_lines:
        return None

    N_active = len(active_lines)
    anchor_map = {
        (li - start_slice): (es, ee)
        for li, sc, es, ee, _ in chain
        if start_slice <= li < end_slice
    }
    anchored_indices = sorted(anchor_map.keys())
    if not anchored_indices:
        return None

    # Prefer Local AI Meta MMS_FA Phoneme Forced Aligner (word-level millisecond sync)
    # bounded strictly to the active sung region [ctc_start_sec, ctc_end_sec],
    # verifying it respects strong ASR anchors and does not collapse repeated choruses.
    if audio_path is not None and job_temp_dir is not None:
        ctc_start_sec = (
            max(opening_voice_sec, first_es - 0.85)
            if (start_slice > 0 and start_slice == first_anchor_idx)
            else opening_voice_sec
        )
        ctc_end_sec = min(closing_voice_sec, last_ee + 0.85) if trimmed_tail else closing_voice_sec
        ctc_aligned = _align_lines_with_ctc_forced_aligner(
            audio_path=audio_path,
            active_lines=active_lines,
            job_temp_dir=job_temp_dir,
            total_duration=total_duration,
            language=language,
            align_start_sec=ctc_start_sec,
            align_end_sec=ctc_end_sec,
            vocal_norm=vocal_norm,
            hop_sec=hop_sec,
        )
        if ctc_aligned and len(ctc_aligned) == N_active:
            max_anchor_drift = 0.0
            for idx_a in anchored_indices:
                es_a, ee_a = anchor_map[idx_a]
                cl = ctc_aligned[idx_a]
                if cl.end < es_a - 4.2:
                    max_anchor_drift = max(max_anchor_drift, es_a - cl.end)
                elif cl.start > ee_a + 4.2:
                    max_anchor_drift = max(max_anchor_drift, cl.start - ee_a)

            avg_budget = (ctc_end_sec - ctc_start_sec) / max(N_active, 1)
            has_collapsed_line = avg_budget >= 2.1 and any(
                len(cl.text.split()) >= 5 and (cl.end - cl.start) < 1.05
                for cl in ctc_aligned
            )
            if max_anchor_drift <= 4.2 and not has_collapsed_line:
                return ctc_aligned
            logger.info(
                "Global MMS_FA failed anchor sanity check (drift=%.2fs, collapsed=%s); locking onto ASR+DP anchors and refining words per line.",
                max_anchor_drift,
                has_collapsed_line,
            )

    raw_intervals: List[Tuple[float, float]] = [(0.0, 0.0)] * N_active
    num_frames = len(local_rel)

    # 1. Leading unanchored lines before the first anchor
    first_a = anchored_indices[0]
    if first_a > 0:
        span_end = max(first_a * 1.5, anchor_map[first_a][0])
        weights = [compute_musical_phrase_weight(active_lines[idx]) for idx in range(first_a)]
        tot_w = sum(weights) or 1.0
        cur_t = opening_voice_sec
        for idx, w in enumerate(weights):
            next_t = cur_t + (w / tot_w) * max(0.5, span_end - opening_voice_sec)
            raw_intervals[idx] = (cur_t, next_t)
            cur_t = next_t

    for idx in anchored_indices:
        raw_intervals[idx] = anchor_map[idx]

    # 2. Interpolate between consecutive anchors and resolve boundaries at breath dips
    for pos in range(len(anchored_indices) - 1):
        a_idx = anchored_indices[pos]
        b_idx = anchored_indices[pos + 1]

        if b_idx == a_idx + 1:
            s_a, e_a = raw_intervals[a_idx]
            s_b, e_b = raw_intervals[b_idx]
            # If there is a clear instrumental gap (> 0.6s) between e_a and s_b, preserve it!
            if s_b > e_a + 0.6:
                raw_intervals[a_idx] = (s_a, e_a)
                raw_intervals[b_idx] = (s_b, e_b)
            else:
                mid_lo = max(s_a + 1.1, min(e_a, s_b) - 0.5)
                mid_hi = min(e_b - 1.0, max(e_a, s_b) + 0.5)
                if mid_hi > mid_lo:
                    f_lo = max(0, int(mid_lo / hop_sec))
                    f_hi = min(num_frames - 1, int(mid_hi / hop_sec))
                    best_f = f_lo + int(np.argmin(local_rel[f_lo : f_hi + 1]))
                    bound_t = best_f * hop_sec
                else:
                    bound_t = max(s_a + 1.0, 0.5 * (e_a + s_b))
                raw_intervals[a_idx] = (s_a, bound_t)
                raw_intervals[b_idx] = (bound_t, max(bound_t + 1.0, e_b))
        else:
            gap_start = raw_intervals[a_idx][1]
            gap_end = raw_intervals[b_idx][0]
            if gap_end <= gap_start + 0.8:
                gap_start = raw_intervals[a_idx][0] + 1.8
                raw_intervals[a_idx] = (raw_intervals[a_idx][0], gap_start)

            gap_lines = list(range(a_idx + 1, b_idx))
            weights = [compute_musical_phrase_weight(active_lines[g]) for g in gap_lines]
            tot_w = sum(weights) or 1.0
            cur_t = gap_start

            for g_idx, w in zip(gap_lines, weights):
                next_t = cur_t + (w / tot_w) * max(0.8, gap_end - gap_start)
                if g_idx < b_idx - 1:
                    f_lo = max(0, int((next_t - 0.45) / hop_sec))
                    f_hi = min(num_frames - 1, int((next_t + 0.45) / hop_sec))
                    if f_hi > f_lo:
                        best_f = f_lo + int(np.argmin(local_rel[f_lo : f_hi + 1]))
                        next_t = best_f * hop_sec
                raw_intervals[g_idx] = (cur_t, next_t)
                cur_t = next_t

    # 3. Trailing unanchored lines after the last anchor (if audio still has time left)
    last_a = anchored_indices[-1]
    if last_a < N_active - 1:
        gap_start = raw_intervals[last_a][1]
        gap_end = max(gap_start + 0.8, closing_voice_sec)
        tail_lines = list(range(last_a + 1, N_active))
        weights = [compute_musical_phrase_weight(active_lines[g]) for g in tail_lines]
        tot_w = sum(weights) or 1.0
        cur_t = gap_start
        for g_idx, w in zip(tail_lines, weights):
            next_t = cur_t + (w / tot_w) * max(0.8, gap_end - gap_start)
            raw_intervals[g_idx] = (cur_t, next_t)
            cur_t = next_t

    # 4. Snap each line's start/end to exact vocal onset & compute per-word fade timings
    aligned_lines: List[LyricLine] = []
    for i in range(N_active):
        s_raw, e_raw = raw_intervals[i]
        # Ensure active vocals starting from 0.0s aren't delayed by late first-anchor margin
        if i == 0 and opening_voice_sec == 0.0 and s_raw <= 1.2:
            s_raw = 0.0
        elif i == 0 and s_raw > 0.25 and s_raw <= 2.2:
            gap_f = min(num_frames, int(s_raw / hop_sec))
            if gap_f > 2 and float(np.max(vocal_norm[:gap_f])) >= 0.22 and float(np.mean(vocal_norm[:gap_f])) >= 0.13:
                s_raw = opening_voice_sec

        s_frame = max(0, min(num_frames - 1, int(s_raw / hop_sec)))
        e_frame = max(s_frame + 10, min(num_frames - 1, int(e_raw / hop_sec)))

        if i == 0 and opening_voice_sec == 0.0 and s_frame <= int(0.25 / hop_sec):
            s_frame = 0
        else:
            max_trim = min(int(0.45 / hop_sec), (e_frame - s_frame) // 4)
            for f in range(s_frame, s_frame + max_trim):
                if local_rel[f] >= 0.70 and flux_env[f] >= 0.16:
                    s_frame = max(s_frame, f - 1)
                    break

        is_last_line = i == N_active - 1
        start_sec = round(s_frame * hop_sec, 2)
        if is_last_line and closing_voice_sec >= total_duration - 0.05 and e_raw >= total_duration - 0.25:
            e_frame = num_frames - 1
            end_sec = round(total_duration, 2)
        else:
            e_frame = max(s_frame + int(0.85 / hop_sec), e_frame - int(0.06 / hop_sec))
            end_sec = round(min(total_duration, max(start_sec + 0.85, e_frame * hop_sec)), 2)

        if aligned_lines and start_sec < aligned_lines[-1].end + 0.04:
            mid_bound = round(0.5 * (aligned_lines[-1].end + start_sec), 2)
            aligned_lines[-1].end = max(aligned_lines[-1].start + 0.75, round(mid_bound - 0.03, 2))
            start_sec = round(mid_bound + 0.03, 2)
            end_sec = max(start_sec + 0.85, end_sec)

        flux_slice = flux_env[s_frame : max(s_frame + 2, e_frame)]
        word_timings = _compute_word_timings_for_line(
            line_text=active_lines[i],
            start_sec=start_sec,
            end_sec=end_sec,
            flux_slice=flux_slice,
            hop_sec=hop_sec,
        )

        aligned_lines.append(
            LyricLine(
                start=start_sec,
                end=end_sec,
                text=active_lines[i],
                words=word_timings,
            )
        )

    # Guard: if vocals continue beyond the last anchored line near total_duration (0.15s..2.5s),
    # extend the final caption right up to closing_voice_sec so trailing vocals always stay captioned.
    if aligned_lines and 0.15 < (closing_voice_sec - aligned_lines[-1].end) <= 2.5:
        gap_s = max(0, int(aligned_lines[-1].end / hop_sec))
        tail_f = min(num_frames, int(closing_voice_sec / hop_sec))
        if (
            tail_f - gap_s > 2
            and float(np.max(vocal_norm[gap_s:tail_f])) >= 0.20
            and float(np.mean(vocal_norm[gap_s:tail_f])) >= 0.12
        ):
            aligned_lines[-1].end = round(min(total_duration, closing_voice_sec), 2)
            sf = max(0, int(aligned_lines[-1].start / hop_sec))
            ef = max(sf + 2, tail_f)
            aligned_lines[-1].words = _compute_word_timings_for_line(
                line_text=aligned_lines[-1].text,
                start_sec=aligned_lines[-1].start,
                end_sec=aligned_lines[-1].end,
                flux_slice=flux_env[sf:ef],
                hop_sec=hop_sec,
            )

    if audio_path is not None and job_temp_dir is not None and aligned_lines:
        aligned_lines = _refine_line_word_timings_with_mms(
            audio_path=audio_path,
            job_temp_dir=job_temp_dir,
            total_duration=total_duration,
            lines=aligned_lines,
            vocal_norm=vocal_norm,
            hop_sec=hop_sec,
            language=language,
        )

    return aligned_lines


def _refine_line_word_timings_with_mms(
    audio_path: Path,
    job_temp_dir: Path,
    total_duration: float,
    lines: List[LyricLine],
    vocal_norm: Optional[np.ndarray] = None,
    hop_sec: float = 0.02,
    language: str = "si",
    slack_sec: float = 0.55,
) -> List[LyricLine]:
    """
    Refine word-level `WordTiming`s (and tight start/end snaps within `±slack_sec`)
    line-by-line using Meta `MMS_FA` inside each line's local acoustic slot.
    Ideal for repeated-chorus lyrics, LRC synced lines (`words=None`), and ASR auto-captions
    where global alignment might jump across duplicate stanzas.
    """
    if not lines:
        return lines
    model, _, _, _ = _get_ctc_aligner_model()
    if model is None:
        return lines

    refined: List[LyricLine] = []
    for ln in lines:
        win_s = max(0.0, ln.start - slack_sec)
        win_e = min(total_duration, ln.end + slack_sec)
        if win_e - win_s >= 0.7:
            single = _align_lines_with_ctc_forced_aligner(
                audio_path=audio_path,
                active_lines=[ln.text],
                job_temp_dir=job_temp_dir,
                total_duration=total_duration,
                language=language,
                align_start_sec=win_s,
                align_end_sec=win_e,
                vocal_norm=vocal_norm,
                hop_sec=hop_sec,
                insert_inter_line_star=False,
            )
            if single and single[0].words:
                s_cand = single[0]
                # Keep original anchor bounds if MMS shifted dramatically; otherwise adopt crisp onset
                if abs(s_cand.start - ln.start) <= 1.25 and abs(s_cand.end - ln.end) <= 1.5:
                    ln_new = LyricLine(
                        start=s_cand.start,
                        end=s_cand.end,
                        text=ln.text,
                        words=s_cand.words,
                    )
                else:
                    ln_new = LyricLine(
                        start=ln.start,
                        end=ln.end,
                        text=ln.text,
                        words=s_cand.words,
                    )
                if refined and ln_new.start < refined[-1].end + 0.04:
                    mid = round(0.5 * (refined[-1].end + ln_new.start), 2)
                    refined[-1].end = max(refined[-1].start + 0.50, round(mid - 0.02, 2))
                    ln_new.start = round(max(refined[-1].end + 0.04, mid + 0.02), 2)
                    ln_new.end = round(min(total_duration, max(ln_new.start + 0.65, ln_new.end)), 2)
                refined.append(ln_new)
                continue
        if refined and ln.start < refined[-1].end + 0.04:
            ln = ln.model_copy(update={"start": round(refined[-1].end + 0.04, 2)})
            if ln.end < ln.start + 0.65:
                ln.end = round(min(total_duration, ln.start + 0.65), 2)
        refined.append(ln)
    return refined


async def _align_with_vocal_phrase_dp(
    audio_path: Path,
    lyric_lines: List[str],
    job_temp_dir: Path,
    total_duration: float,
    language: str = "si",
    require_asr_match: bool = False,
    clip_offset_sec: float = 0.0,
    hint_texts: Optional[List[str]] = None,
    start_line_index: int = -1,
    force_refresh_asr: bool = False,
) -> List[LyricLine]:
    """
    Primary Sinhala / Custom Audio Aligner & Auto-Caption Generator:
    1. Extracts vocal-band STFT envelope & breath dips (`local_rel`).
    2. Runs parallel overlapping Speech-to-Text (`si-LK` / `en-US`) across the audio.
    3. If `lyric_lines` is EMPTY (Auto-Caption Mode), builds clean, deduplicated,
       breath-synced Sinhala/English captions directly from the ASR windows!
    4. If `lyric_lines` is PROVIDED, matches the user's lyrics against the ASR windows
       to lock onto the true singing timestamps and interpolates any remaining lines via breath dips.
    5. Falls back to acoustic breath-dip segmentation if offline.
    """
    def _run_pipeline() -> List[LyricLine]:
        stft_wav = _extract_stereo_vocal_isolated_wav(audio_path, job_temp_dir)
        try:
            vocal_norm, local_rel, flux_env, hop_sec = _extract_vocal_band_envelope(
                stft_wav, hop_sec=0.02
            )
        except Exception as exc:
            logger.warning("Vocal STFT extraction failed, using uniform fallback: %s", exc)
            vocal_norm = np.ones(int(max(total_duration, 1.0) / 0.02), dtype=np.float32)
            local_rel = np.ones_like(vocal_norm)
            flux_env = np.ones_like(vocal_norm) * 0.5
            hop_sec = 0.02

        asr_windows = _extract_asr_windows_sync(
            audio_path=audio_path,
            job_temp_dir=job_temp_dir,
            total_duration=total_duration,
            language=language,
            force_refresh=force_refresh_asr,
        )

        active_lyric_lines = list(lyric_lines)
        user_locked_start = False
        if 0 <= start_line_index < len(active_lyric_lines):
            active_lyric_lines = active_lyric_lines[start_line_index:]
            user_locked_start = True
            logger.info(
                "User selected starting lyric line #%d (%r); slicing remaining %d lines.",
                start_line_index + 1,
                active_lyric_lines[0],
                len(active_lyric_lines),
            )
        N = len(active_lyric_lines)

        # MODE A: Auto-Caption Generation (when manual_lyrics is empty)
        if N == 0:
            if asr_windows:
                auto_captions = _build_auto_captions_from_asr_windows(
                    asr_windows=asr_windows,
                    vocal_norm=vocal_norm,
                    local_rel=local_rel,
                    flux_env=flux_env,
                    hop_sec=hop_sec,
                    total_duration=total_duration,
                )
                if auto_captions:
                    auto_captions = _refine_line_word_timings_with_mms(
                        audio_path=audio_path,
                        job_temp_dir=job_temp_dir,
                        total_duration=total_duration,
                        lines=auto_captions,
                        vocal_norm=vocal_norm,
                        hop_sec=hop_sec,
                        language=language,
                    )
                    logger.info(
                        "Auto-generated %d synced caption lines from song audio!",
                        len(auto_captions),
                    )
                    return auto_captions

            return [
                LyricLine(
                    start=0.5,
                    end=min(total_duration, 5.0),
                    text="♪ Instrumental / No Vocals Detected ♪",
                )
            ]

        # MODE B: Manual / Web-Fetched Lyrics + ASR Anchor Alignment
        if asr_windows and N > 0:
            asr_aligned = _align_manual_lines_with_asr_anchors(
                lyric_lines=active_lyric_lines,
                asr_windows=asr_windows,
                vocal_norm=vocal_norm,
                local_rel=local_rel,
                flux_env=flux_env,
                hop_sec=hop_sec,
                total_duration=total_duration,
                audio_path=audio_path,
                job_temp_dir=job_temp_dir,
                language=language,
                clip_offset_sec=0.0 if user_locked_start else clip_offset_sec,
                hint_texts=None if user_locked_start else hint_texts,
                lock_first_line=user_locked_start,
            )
            if asr_aligned:
                logger.info(
                    "ASR-Anchored Sinhala Aligner locked %d lines to vocal timestamps!",
                    len(asr_aligned),
                )
                return asr_aligned
            if require_asr_match and len(asr_windows) >= 2:
                logger.info(
                    "Auto-fetched web lyrics did not match the vocals in this clip; using pure Audio Auto-Transcription instead."
                )
                auto_captions = _build_auto_captions_from_asr_windows(
                    asr_windows=asr_windows,
                    vocal_norm=vocal_norm,
                    local_rel=local_rel,
                    flux_env=flux_env,
                    hop_sec=hop_sec,
                    total_duration=total_duration,
                )
                if auto_captions:
                    return _refine_line_word_timings_with_mms(
                        audio_path=audio_path,
                        job_temp_dir=job_temp_dir,
                        total_duration=total_duration,
                        lines=auto_captions,
                        vocal_norm=vocal_norm,
                        hop_sec=hop_sec,
                        language=language,
                    )

        # MODE C: Offline Acoustic Breath-Dip Fallback (when network/ASR is unavailable)
        num_frames = len(vocal_norm)
        if num_frames < 20:
            step = total_duration / max(N, 1)
            return [
                LyricLine(start=round(i * step, 2), end=round((i + 1) * step, 2), text=txt)
                for i, txt in enumerate(active_lyric_lines)
            ]

        opening_voice_sec = _detect_opening_voice_start(vocal_norm, hop_sec, max_search_sec=min(12.0, total_duration * 0.45))
        closing_voice_sec = _detect_closing_voice_end(vocal_norm, hop_sec, total_duration, max_search_sec=min(12.0, total_duration * 0.45))
        active_singing_span = max(2.5, closing_voice_sec - opening_voice_sec)

        # If a full-song lyric sheet (e.g. 24-35 lines) was fetched for a short TikTok clip (e.g. 15s-40s)
        # and ASR is unavailable/empty, slice to the natural number of lines that fit the singing span
        # (~2.4s-3.4s per line based on Sinhala syllable weight) AND shift start index when Shazam
        # indicates the clip starts mid-song (`clip_offset_sec > 8.0`)!
        if N > 6 and (active_singing_span / float(N)) < 1.85:
            hint_start_idx = None if user_locked_start else _find_hint_start_line_index(active_lyric_lines, hint_texts)
            est_start_idx = 0
            if not user_locked_start:
                if hint_start_idx is not None and hint_start_idx > 0:
                    est_start_idx = min(N - 3, hint_start_idx)
                elif clip_offset_sec > 8.0:
                    est_start_idx = int(round(max(0.0, clip_offset_sec - 10.0) / 4.0))
                    est_start_idx = max(0, min(N - 3, est_start_idx))

            cum_est = 0.0
            best_k = max(3, min(N - est_start_idx, int(round(active_singing_span / 2.65))))
            best_diff = 1e9
            for idx_ln, ln_txt in enumerate(active_lyric_lines[est_start_idx:]):
                est_ln_dur = max(2.15, min(4.2, 0.95 + 0.145 * count_sinhala_syllable_weight(ln_txt)))
                cum_est += est_ln_dur
                k_cand = idx_ln + 1
                if k_cand >= 3:
                    diff = abs(cum_est - active_singing_span)
                    if diff < best_diff:
                        best_diff = diff
                        best_k = k_cand
            best_k = max(3, min(N - est_start_idx, best_k))
            if not user_locked_start and est_start_idx + best_k > N:
                est_start_idx = max(0, N - best_k)
            if best_k < N or est_start_idx > 0:
                logger.info(
                    "Offline clip-duration slicer: selected lines %d..%d (%d/%d) to fit %.2fs singing span (clip_offset=%.1fs).",
                    est_start_idx + 1,
                    est_start_idx + best_k,
                    best_k,
                    N,
                    active_singing_span,
                    clip_offset_sec,
                )
                active_lyric_lines = active_lyric_lines[est_start_idx : est_start_idx + best_k]
                N = len(active_lyric_lines)

        # Try Local AI Meta MMS_FA Phoneme Forced Aligner (word-level millisecond sync) before DP fallback
        ctc_aligned = _align_lines_with_ctc_forced_aligner(
            audio_path=audio_path,
            active_lines=active_lyric_lines,
            job_temp_dir=job_temp_dir,
            total_duration=total_duration,
            language=language,
            align_start_sec=opening_voice_sec,
            align_end_sec=closing_voice_sec,
            vocal_norm=vocal_norm,
            hop_sec=hop_sec,
        )
        if ctc_aligned:
            return ctc_aligned
        active_thresh = max(float(np.percentile(vocal_norm, 20)) * 1.15, 0.14)
        onset_candidates = np.where((vocal_norm > active_thresh) & (flux_env > 0.15))[0]
        if len(onset_candidates) >= 2:
            first_frame = 0 if opening_voice_sec == 0.0 else max(0, min(int(onset_candidates[0]) - 2, int(opening_voice_sec / hop_sec)))
            active_frames = np.where(vocal_norm > active_thresh * 1.1)[0]
            tail_active_frame = min(num_frames - 1, int(closing_voice_sec / hop_sec))
            last_frame = max(int(active_frames[-1]), tail_active_frame)
        else:
            first_frame = int(opening_voice_sec / hop_sec)
            last_frame = max(first_frame + 10, min(num_frames - 1, int(closing_voice_sec / hop_sec)))

        last_frame = min(num_frames - 1, max(last_frame, first_frame + N * 10))
        phrase_weights = np.array(
            [compute_musical_phrase_weight(line) for line in active_lyric_lines],
            dtype=np.float32,
        )
        cum_weights = np.concatenate([[0.0], np.cumsum(phrase_weights)])
        total_weight = float(cum_weights[-1]) or 1.0

        step_f = 3
        M = max((last_frame - first_frame) // step_f, N + 2)
        grid_frames = np.linspace(first_frame, last_frame, M + 1, dtype=int)

        boundary_cost = np.zeros(M + 1, dtype=np.float32)
        for m_idx, f_idx in enumerate(grid_frames):
            lo = max(0, f_idx - 5)
            hi = min(num_frames, f_idx + 5)
            dip_val = float(np.min(local_rel[lo:hi]))
            post_hi = min(num_frames, f_idx + 12)
            onset_val = float(np.max(flux_env[f_idx:post_hi])) if post_hi > f_idx else 0.0
            boundary_cost[m_idx] = 2.2 * dip_val - 0.65 * onset_val

        INF = 1e9
        dp = np.full((N + 1, M + 1), INF, dtype=np.float32)
        back = np.zeros((N + 1, M + 1), dtype=int)
        dp[0, 0] = 0.0
        min_grid_span = max(1, int(1.1 / (step_f * hop_sec)))

        for i in range(1, N + 1):
            target_cum_ratio = float(cum_weights[i] / total_weight)
            target_seg_ratio = float(phrase_weights[i - 1] / total_weight)

            for m in range(i * min_grid_span, M - (N - i) * min_grid_span + 1):
                actual_cum_ratio = float(m) / float(M)
                cum_err = ((actual_cum_ratio - target_cum_ratio) * N * 0.35) ** 2

                prev_m_min = (i - 1) * min_grid_span
                prev_m_max = m - min_grid_span
                if prev_m_max < prev_m_min:
                    continue

                prev_indices = np.arange(prev_m_min, prev_m_max + 1)
                seg_ratios = (m - prev_indices).astype(np.float32) / float(M)
                seg_errs = ((seg_ratios - target_seg_ratio) / max(target_seg_ratio, 0.05)) ** 2

                b_pen = boundary_cost[m] if i < N else 0.0
                total_costs = dp[i - 1, prev_indices] + 0.45 * seg_errs + 0.25 * cum_err + b_pen

                best_local = int(np.argmin(total_costs))
                dp[i, m] = float(total_costs[best_local])
                back[i, m] = int(prev_indices[best_local])

        if dp[N, M] >= INF / 2:
            chosen_grid = np.round((cum_weights / total_weight) * M).astype(int)
        else:
            chosen_grid = np.zeros(N + 1, dtype=int)
            curr_m = M
            for i in range(N, 0, -1):
                chosen_grid[i] = curr_m
                curr_m = back[i, curr_m]
            chosen_grid[0] = 0

        boundary_frames = [int(grid_frames[m]) for m in chosen_grid]
        aligned_lines: List[LyricLine] = []
        for i in range(N):
            raw_s = boundary_frames[i]
            raw_e = boundary_frames[i + 1]
            s_frame = raw_s
            if i == N - 1 and closing_voice_sec >= total_duration - 0.05:
                e_frame = num_frames - 1
                end_sec = round(total_duration, 2)
            else:
                e_frame = max(s_frame + int(0.8 / hop_sec), raw_e - int(0.08 / hop_sec))
                end_sec = round(min(total_duration, max(round(s_frame * hop_sec, 2) + 0.85, e_frame * hop_sec)), 2)
            start_sec = round(s_frame * hop_sec, 2)

            flux_slice = flux_env[s_frame : max(s_frame + 2, e_frame)]
            word_timings = _compute_word_timings_for_line(
                line_text=active_lyric_lines[i],
                start_sec=start_sec,
                end_sec=end_sec,
                flux_slice=flux_slice,
                hop_sec=hop_sec,
            )
            aligned_lines.append(
                LyricLine(
                    start=start_sec,
                    end=end_sec,
                    text=active_lyric_lines[i],
                    words=word_timings,
                )
            )

        return aligned_lines

    return await asyncio.to_thread(_run_pipeline)


async def align_lyrics_with_audio(
    audio_path: Path,
    lyrics_text: str,
    job_temp_dir: Path,
    total_duration: float,
    language: str = DEFAULT_ALIGNMENT_LANG,
    require_asr_match: bool = False,
    clip_offset_sec: float = 0.0,
    hint_texts: Optional[List[str]] = None,
    start_line_index: int = -1,
    force_refresh_asr: bool = False,
) -> List[LyricLine]:
    """
    Tier 3 Entrypoint: Forced Alignment for Manual Sinhala / Custom Lyrics AND
    Auto-Caption Speech-to-Text Generation when `lyrics_text` is empty.
    """
    lyric_lines = _clean_lyrics_lines(lyrics_text)
    aligned_result = await _align_with_vocal_phrase_dp(
        audio_path=audio_path,
        lyric_lines=lyric_lines,
        job_temp_dir=job_temp_dir,
        total_duration=total_duration,
        language=language,
        require_asr_match=require_asr_match,
        clip_offset_sec=clip_offset_sec,
        hint_texts=hint_texts,
        start_line_index=start_line_index,
        force_refresh_asr=force_refresh_asr,
    )

    export_lines_to_srt(aligned_result, job_temp_dir / "aligned_lyrics.srt")
    (job_temp_dir / "timing_map.json").write_text(
        json.dumps([line.model_dump() for line in aligned_result], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return aligned_result


def _score_anchor_chain_for_lines(
    lyric_lines: List[str],
    asr_windows: List[Tuple[float, float, str]],
    total_duration: float,
) -> float:
    """
    Fast heuristic score measuring how strongly a candidate lyric sheet matches the song's
    spoken/sung ASR windows in correct chronological order. Returns 0.0 if mismatch.
    """
    if not lyric_lines or not asr_windows:
        return 0.0
    hits: List[Tuple[int, float, float]] = []
    for i, line in enumerate(lyric_lines):
        lp = sinhala_phonetic_key(line)
        for ws, we, wt in asr_windows:
            m = _match_line_to_asr_window(lp, ws, we, wt, total_duration)
            if m:
                hits.append((i, m[0], m[1]))
    if not hits:
        return 0.0
    hits.sort(key=lambda x: (x[0], x[2]))
    K = len(hits)
    dp = [h[1] for h in hits]
    chain_len = [1] * K
    for k in range(K):
        for j in range(k):
            if hits[k][0] > hits[j][0] and (hits[k][2] - hits[j][2]) >= max(1.1, (hits[k][0] - hits[j][0]) * 1.25):
                cand = dp[j] + hits[k][1]
                if cand > dp[k]:
                    dp[k] = cand
                    chain_len[k] = chain_len[j] + 1
    best_idx = int(np.argmax(dp))
    best_len = chain_len[best_idx]
    best_score = float(dp[best_idx])
    expected_clip_lines = max(2, int(total_duration / 2.8))
    min_req = min(len(lyric_lines), max(1, min(expected_clip_lines // 3, 3)))
    if best_len == 1 and best_score < 0.65:
        return 0.0
    if best_len < min_req:
        return 0.0
    return best_score


async def select_and_align_best_lyrics_candidate(
    audio_path: Path,
    candidates: List[Tuple[str, str]],
    job_temp_dir: Path,
    total_duration: float,
    language: str = DEFAULT_ALIGNMENT_LANG,
    fallback_to_auto: bool = True,
    clip_offset_sec: float = 0.0,
    hint_texts: Optional[List[str]] = None,
) -> Tuple[Optional[str], List[LyricLine]]:
    """
    Evaluate candidate lyric texts (LRCLIB, LyriHub.lk, Lyrics-LK.com, GeeMansala.com)
    against the audio's actual ASR windows, select the candidate that best matches what is sung,
    and align it with vocal onsets & breath dips.
    """
    def _run() -> Tuple[Optional[str], Optional[List[LyricLine]]]:
        stft_wav = _extract_stereo_vocal_isolated_wav(audio_path, job_temp_dir)
        try:
            vocal_norm, local_rel, flux_env, hop_sec = _extract_vocal_band_envelope(
                stft_wav, hop_sec=0.02
            )
        except Exception as exc:
            logger.warning("Vocal STFT extraction failed: %s", exc)
            vocal_norm = np.ones(int(max(total_duration, 1.0) / 0.02), dtype=np.float32)
            local_rel = np.ones_like(vocal_norm)
            flux_env = np.ones_like(vocal_norm) * 0.5
            hop_sec = 0.02

        asr_windows = _extract_asr_windows_sync(
            audio_path=audio_path,
            job_temp_dir=job_temp_dir,
            total_duration=total_duration,
            language=language,
        )

        best_source: Optional[str] = None
        best_lines: Optional[List[LyricLine]] = None
        best_score: float = 0.0

        if asr_windows:
            for src_name, raw_text in candidates:
                lines_clean = _clean_lyrics_lines(raw_text)
                if not lines_clean:
                    continue
                chain_score = _score_anchor_chain_for_lines(lines_clean, asr_windows, total_duration)
                if chain_score <= 0.0:
                    continue
                aligned = _align_manual_lines_with_asr_anchors(
                    lyric_lines=lines_clean,
                    asr_windows=asr_windows,
                    vocal_norm=vocal_norm,
                    local_rel=local_rel,
                    flux_env=flux_env,
                    hop_sec=hop_sec,
                    total_duration=total_duration,
                    audio_path=audio_path,
                    job_temp_dir=job_temp_dir,
                    language=language,
                    clip_offset_sec=clip_offset_sec,
                    hint_texts=hint_texts,
                )
                if aligned and chain_score > best_score:
                    best_score = chain_score
                    best_source = src_name
                    best_lines = aligned

        if best_lines:
            logger.info(
                "Audio-Verified Multi-Source Winner: '%s' (%d lines, anchor_score=%.2f)",
                best_source,
                len(best_lines),
                best_score,
            )
            return best_source, best_lines

        # If offline (no asr_windows at all) and we have at least one web lyric candidate, align first candidate
        if not asr_windows and candidates and fallback_to_auto:
            for src_name, raw_text in candidates:
                lines_clean = _clean_lyrics_lines(raw_text)
                if lines_clean:
                    return src_name, None  # caller will fall back to acoustic alignment

        if not fallback_to_auto:
            return None, []

        # Fallback: Pure Auto-Captioning directly from sung audio
        if asr_windows:
            auto_captions = _build_auto_captions_from_asr_windows(
                asr_windows=asr_windows,
                vocal_norm=vocal_norm,
                local_rel=local_rel,
                flux_env=flux_env,
                hop_sec=hop_sec,
                total_duration=total_duration,
            )
            if auto_captions:
                auto_captions = _refine_line_word_timings_with_mms(
                    audio_path=audio_path,
                    job_temp_dir=job_temp_dir,
                    total_duration=total_duration,
                    lines=auto_captions,
                    vocal_norm=vocal_norm,
                    hop_sec=hop_sec,
                    language=language,
                )
                return "auto_asr", auto_captions

        return None, [
            LyricLine(
                start=0.5,
                end=min(total_duration, 5.0),
                text="♪ Instrumental / No Vocals Detected ♪",
            )
        ]

    winning_source, aligned_result = await asyncio.to_thread(_run)
    if winning_source and aligned_result is None:
        # Offline acoustic DP fallback for matched candidate
        matched_txt = next(
            (txt for s, txt in candidates if s == winning_source and txt.strip()),
            candidates[0][1],
        )
        aligned_result = await align_lyrics_with_audio(
            audio_path=audio_path,
            lyrics_text=matched_txt,
            job_temp_dir=job_temp_dir,
            total_duration=total_duration,
            language=language,
            require_asr_match=False,
            clip_offset_sec=clip_offset_sec,
            hint_texts=hint_texts,
        )
        return winning_source, aligned_result

    if aligned_result:
        export_lines_to_srt(aligned_result, job_temp_dir / "aligned_lyrics.srt")
        (job_temp_dir / "timing_map.json").write_text(
            json.dumps([line.model_dump() for line in aligned_result], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return winning_source, aligned_result or []


async def verify_and_enrich_lrc_lines(
    audio_path: Path,
    lrc_lines: List[LyricLine],
    job_temp_dir: Path,
    total_duration: float,
    language: str = DEFAULT_ALIGNMENT_LANG,
) -> Tuple[Optional[List[LyricLine]], str]:
    """
    Validate LRC/Shazam-offset synced lines against real clip ASR windows so remixes or wrong LRC slices
    never show mismatched studio lyrics, and enrich verified lines with word-level Meta MMS_FA timings.
    Returns `(lines, status)` where `status` is:
      - `"matched"` / `"offline"`: `lines` are verified & enriched
      - `"hybrid"`: early unmatched remix section replaced with clean ASR captions before verified tail lines
      - `"mismatch"`: LRC slice did not match clip audio (`None`)
    """
    if not lrc_lines:
        return None, "mismatch"

    def _run() -> Tuple[Optional[List[LyricLine]], str]:
        stft_wav = _extract_stereo_vocal_isolated_wav(audio_path, job_temp_dir)
        try:
            vocal_norm, local_rel, flux_env, hop_sec = _extract_vocal_band_envelope(stft_wav, hop_sec=0.02)
        except Exception:
            vocal_norm = np.ones(int(max(total_duration, 1.0) / 0.02), dtype=np.float32)
            local_rel = np.ones_like(vocal_norm)
            flux_env = np.ones_like(vocal_norm) * 0.5
            hop_sec = 0.02

        asr_windows = _extract_asr_windows_sync(
            audio_path=audio_path,
            job_temp_dir=job_temp_dir,
            total_duration=total_duration,
            language=language,
        )

        if not asr_windows:
            enriched = _refine_line_word_timings_with_mms(
                audio_path=audio_path,
                job_temp_dir=job_temp_dir,
                total_duration=total_duration,
                lines=lrc_lines,
                vocal_norm=vocal_norm,
                hop_sec=hop_sec,
                language=language,
            )
            return enriched, "offline"

        matched_indices: List[int] = []
        for idx, ln in enumerate(lrc_lines):
            lp = sinhala_phonetic_key(ln.text)
            if len(lp) < 3:
                continue
            for ws, we, wt in asr_windows:
                if ws <= ln.end + 3.5 and we >= ln.start - 3.5:
                    if _match_line_to_asr_window(lp, ws, we, wt, total_duration):
                        matched_indices.append(idx)
                        break

        if not matched_indices:
            return None, "mismatch"

        first_m_idx = matched_indices[0]
        first_m_start = lrc_lines[first_m_idx].start
        early_asr = [
            w for w in asr_windows
            if w[1] <= first_m_start - 0.5 and len(sinhala_phonetic_key(w[2])) >= 4
        ]
        # Remix / live mashup case: opening lines (e.g. 0..18s) didn't match early ASR while tail matches cleanly
        if first_m_idx >= 2 and first_m_start >= 7.0 and len(early_asr) >= 2:
            prefix_caps = _build_auto_captions_from_asr_windows(
                asr_windows=early_asr,
                vocal_norm=vocal_norm,
                local_rel=local_rel,
                flux_env=flux_env,
                hop_sec=hop_sec,
                total_duration=max(1.0, first_m_start - 0.1),
            )
            tail_lines = list(lrc_lines[first_m_idx:])
            for p_ln in prefix_caps:
                p_ln.end = round(min(p_ln.end, first_m_start - 0.08), 2)
                if p_ln.end < p_ln.start + 0.5:
                    continue
            prefix_clean = [
                p for p in prefix_caps
                if p.end <= first_m_start - 0.05 and p.end >= p.start + 0.5
            ]
            combined = prefix_clean + tail_lines
            enriched = _refine_line_word_timings_with_mms(
                audio_path=audio_path,
                job_temp_dir=job_temp_dir,
                total_duration=total_duration,
                lines=combined,
                vocal_norm=vocal_norm,
                hop_sec=hop_sec,
                language=language,
            )
            return enriched, "hybrid"

        # If more than 2/3 of lines mismatched despite abundant ASR, reject so full-text search runs
        if len(matched_indices) == 1 and len(lrc_lines) >= 5 and len(asr_windows) >= 6:
            return None, "mismatch"

        enriched = _refine_line_word_timings_with_mms(
            audio_path=audio_path,
            job_temp_dir=job_temp_dir,
            total_duration=total_duration,
            lines=lrc_lines,
            vocal_norm=vocal_norm,
            hop_sec=hop_sec,
            language=language,
        )
        return enriched, "matched"

    return await asyncio.to_thread(_run)
