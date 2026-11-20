import asyncio
import hashlib
import logging
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
import httpx
from fastapi import UploadFile

from backend.config import FONTS_DIR, TEMP_DIR, create_resilient_async_client

logger = logging.getLogger(__name__)

# Curated 1-Click Downloadable Open-Source Sinhala Unicode Fonts (Google Fonts OFL .ttf files)
ONLINE_SINHALA_FONT_CATALOG: List[Dict[str, str]] = [
    {
        "id": "abhaya_libre_bold",
        "label": "Abhaya Libre Bold (අභය බෝල්ඩ් - Classic Aesthetic Serif)",
        "family": "Abhaya Libre Bold",
        "ass_family": "Abhaya Libre",
        "filename": "AbhayaLibre-Bold.ttf",
        "sample_si": "මගේ අභියස ලෝකයක් තියා",
        "style_tag": "Bold Serif",
        "url": "https://raw.githubusercontent.com/google/fonts/main/ofl/abhayalibre/AbhayaLibre-Bold.ttf",
    },
    {
        "id": "abhaya_libre_regular",
        "label": "Abhaya Libre Regular (අභය - Clean Book Style)",
        "family": "Abhaya Libre Regular",
        "ass_family": "Abhaya Libre",
        "filename": "AbhayaLibre-Regular.ttf",
        "sample_si": "වටිනම දෙය තෝරගන් කියා",
        "style_tag": "Book Serif",
        "url": "https://raw.githubusercontent.com/google/fonts/main/ofl/abhayalibre/AbhayaLibre-Regular.ttf",
    },
    {
        "id": "gemunu_libre_bold",
        "label": "Gemunu Libre (ගැමුණු - Modern Rounded Sinhala)",
        "family": "Gemunu Libre",
        "ass_family": "Gemunu Libre",
        "filename": "GemunuLibre-Bold.ttf",
        "sample_si": "කිව්වෝතින් එක පාරින් නුඹ වෙත එනවා",
        "style_tag": "Rounded Modern",
        "url": "https://raw.githubusercontent.com/google/fonts/main/ofl/gemunulibre/static/GemunuLibre-Bold.ttf",
    },
    {
        "id": "noto_serif_sinhala",
        "label": "Noto Serif Sinhala (නොටෝ සෙරිෆ් - Poetic & Lyric Style)",
        "family": "Noto Serif Sinhala",
        "ass_family": "Noto Serif Sinhala",
        "filename": "NotoSerifSinhala-Regular.ttf",
        "sample_si": "මගේ හදවත මාලයක් තනා",
        "style_tag": "Poetic Serif",
        "url": "https://raw.githubusercontent.com/google/fonts/main/ofl/notoserifsinhala/static/NotoSerifSinhala-Regular.ttf",
    },
    {
        "id": "noto_sans_sinhala",
        "label": "Noto Sans Sinhala (නොටෝ සෑන්ස් - Clean Modern Sans)",
        "family": "Noto Sans Sinhala",
        "ass_family": "Noto Sans Sinhala",
        "filename": "NotoSansSinhala-Regular.ttf",
        "sample_si": "නුඹේ හදවතෙහි ලඟින් තියා",
        "style_tag": "Clean Sans",
        "url": "https://raw.githubusercontent.com/google/fonts/main/ofl/notosanssinhala/static/NotoSansSinhala-Regular.ttf",
    },
    {
        "id": "yaldevi_sinhala",
        "label": "Yaldevi Colombo (යාල්දේවි - Stylish Display Sinhala)",
        "family": "Yaldevi",
        "ass_family": "Yaldevi",
        "filename": "Yaldevi-Bold.ttf",
        "sample_si": "හැමදාටම පලඳන්නෙමි මා",
        "style_tag": "Display Wide",
        "url": "https://raw.githubusercontent.com/google/fonts/main/ofl/yaldevi/static/Yaldevi-Bold.ttf",
    },
]

def _get_windows_fonts_dir() -> Path:
    return Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"


def _build_system_font_registry() -> Tuple[Dict[str, Tuple[Path, int]], List[Dict[str, str]]]:
    ttc_map: Dict[str, Tuple[Path, int]] = {}
    system_fonts: List[Dict[str, str]] = []

    mac_sangam = Path("/System/Library/Fonts/Supplemental/Sinhala Sangam MN.ttc")
    if mac_sangam.is_file():
        ttc_map["sinhala-sangam-mn-regular.ttf"] = (mac_sangam, 0)
        ttc_map["sinhala-sangam-mn-bold.ttf"] = (mac_sangam, 1)
        system_fonts.append(
            {
                "family": "Sinhala Sangam MN",
                "ass_family": "Sinhala Sangam MN",
                "label": "Sinhala Sangam MN (The_Black Hart Default)",
                "filename": "",
                "style_tag": "The_Black Hart Original",
                "sample_si": "මගේ අභියස ලෝකයක් තියා",
                "url": "/api/fonts/system/sinhala-sangam-mn-regular.ttf",
                "regular_url": "/api/fonts/system/sinhala-sangam-mn-regular.ttf",
                "bold_url": "/api/fonts/system/sinhala-sangam-mn-bold.ttf",
                "source": "system",
            }
        )

    mac_sinhala_mn = Path("/System/Library/Fonts/Supplemental/Sinhala MN.ttc")
    if mac_sinhala_mn.is_file():
        ttc_map["sinhala-mn-regular.ttf"] = (mac_sinhala_mn, 0)
        ttc_map["sinhala-mn-bold.ttf"] = (mac_sinhala_mn, 1)
        system_fonts.append(
            {
                "family": "Sinhala MN",
                "ass_family": "Sinhala MN",
                "label": "Sinhala MN (Classic Sinhala Serif)",
                "filename": "",
                "style_tag": "System Serif",
                "sample_si": "වටිනම දෙය තෝරගන් කියා",
                "url": "/api/fonts/system/sinhala-mn-regular.ttf",
                "regular_url": "/api/fonts/system/sinhala-mn-regular.ttf",
                "bold_url": "/api/fonts/system/sinhala-mn-bold.ttf",
                "source": "system",
            }
        )

    # Windows Sinhala System Fonts (Iskoola Pota & Nirmala UI)
    win_fonts = _get_windows_fonts_dir()
    iskpota_reg = win_fonts / "iskpota.ttf"
    iskpota_bold = win_fonts / "iskpotab.ttf"
    if iskpota_reg.is_file():
        ttc_map["iskoola-pota-regular.ttf"] = (iskpota_reg, 0)
        ttc_map["iskoola-pota-bold.ttf"] = (iskpota_bold if iskpota_bold.is_file() else iskpota_reg, 0)
        system_fonts.append(
            {
                "family": "Iskoola Pota",
                "ass_family": "Iskoola Pota",
                "label": "Iskoola Pota (Windows Classic Sinhala Serif)",
                "filename": "",
                "style_tag": "Windows System Serif",
                "sample_si": "මගේ අභියස ලෝකයක් තියා",
                "url": "/api/fonts/system/iskoola-pota-regular.ttf",
                "regular_url": "/api/fonts/system/iskoola-pota-regular.ttf",
                "bold_url": "/api/fonts/system/iskoola-pota-bold.ttf",
                "source": "system",
            }
        )

    nirmala_ttc = win_fonts / "Nirmala.ttc"
    nirmala_reg = win_fonts / "Nirmala.ttf"
    nirmala_bold = win_fonts / "NirmalaB.ttf"
    if nirmala_reg.is_file():
        ttc_map["nirmala-ui-regular.ttf"] = (nirmala_reg, 0)
        ttc_map["nirmala-ui-bold.ttf"] = (nirmala_bold if nirmala_bold.is_file() else nirmala_reg, 0)
        has_nirmala = True
    elif nirmala_ttc.is_file():
        ttc_map["nirmala-ui-regular.ttf"] = (nirmala_ttc, 0)
        ttc_map["nirmala-ui-bold.ttf"] = (nirmala_ttc, 1)
        has_nirmala = True
    else:
        has_nirmala = False

    if has_nirmala:
        system_fonts.append(
            {
                "family": "Nirmala UI",
                "ass_family": "Nirmala UI",
                "label": "Nirmala UI (Windows Clean Sinhala Sans)",
                "filename": "",
                "style_tag": "Windows System Sans",
                "sample_si": "නුඹේ හදවතෙහි ලඟින් තියා",
                "url": "/api/fonts/system/nirmala-ui-regular.ttf",
                "regular_url": "/api/fonts/system/nirmala-ui-regular.ttf",
                "bold_url": "/api/fonts/system/nirmala-ui-bold.ttf",
                "source": "system",
            }
        )

    system_fonts.extend(
        [
            {
                "family": "Georgia",
                "ass_family": "Georgia",
                "label": "Georgia Serif (English / Aesthetic)",
                "filename": "",
                "style_tag": "English Serif",
                "sample_si": "Aesthetic Midnight Vibes",
                "source": "system",
            },
            {
                "family": "Arial",
                "ass_family": "Arial",
                "label": "Arial Bold Sans (CapCut / TikTok)",
                "filename": "",
                "style_tag": "Bold Sans",
                "sample_si": "TikTok Pop Captions",
                "source": "system",
            },
        ]
    )
    return ttc_map, system_fonts


SYSTEM_TTC_FONT_MAP, SYSTEM_DEFAULT_FONTS = _build_system_font_registry()
_SYSTEM_TTF_CACHE: Dict[str, bytes] = {}


def extract_system_font_ttf(slug: str) -> Optional[bytes]:
    """
    Extract a standalone .ttf binary for a system .ttc/.ttf Sinhala font so browsers
    (which block Supplemental .ttc fonts in CSS font-family) can load it via @font-face
    and render the exact same glyphs as FFmpeg libass.
    """
    if slug in _SYSTEM_TTF_CACHE:
        return _SYSTEM_TTF_CACHE[slug]
    entry = SYSTEM_TTC_FONT_MAP.get(slug)
    if not entry:
        return None
    ttc_path, font_index = entry
    if not ttc_path.exists():
        return None

    ttc_bytes = ttc_path.read_bytes()
    if ttc_bytes[0:4] != b"ttcf":
        _SYSTEM_TTF_CACHE[slug] = ttc_bytes
        return ttc_bytes

    num_fonts = struct.unpack(">I", ttc_bytes[8:12])[0]
    if font_index >= num_fonts:
        font_index = 0
    sfnt_offset = struct.unpack(">I", ttc_bytes[12 + 4 * font_index : 16 + 4 * font_index])[0]
    sfnt_version, num_tables, search_range, entry_selector, range_shift = struct.unpack(
        ">IHHHH", ttc_bytes[sfnt_offset : sfnt_offset + 12]
    )

    records = []
    for i in range(num_tables):
        rec_off = sfnt_offset + 12 + i * 16
        t_tag, checksum, orig_off, length = struct.unpack(">4sIII", ttc_bytes[rec_off : rec_off + 16])
        records.append((t_tag, checksum, orig_off, length))

    header = struct.pack(">IHHHH", sfnt_version, num_tables, search_range, entry_selector, range_shift)
    cur_offset = 12 + num_tables * 16
    dir_bytes = bytearray()
    body_bytes = bytearray()
    for t_tag, checksum, orig_off, length in records:
        table_data = ttc_bytes[orig_off : orig_off + length]
        pad_len = (4 - (length % 4)) % 4
        dir_bytes += struct.pack(">4sIII", t_tag, checksum, cur_offset, length)
        body_bytes += table_data + (b"\x00" * pad_len)
        cur_offset += length + pad_len

    ttf_bytes = header + bytes(dir_bytes) + bytes(body_bytes)
    _SYSTEM_TTF_CACHE[slug] = ttf_bytes
    return ttf_bytes


def parse_ttf_name_records(font_path: Path) -> Dict[int, str]:
    """
    Parse OpenType/TrueType `name` table records (nameID 1, 2, 4, 6, 16, 17).
    `libass` (CoreText / FreeType) matches `.ass` `Fontname` against `nameID 1`.
    """
    records: Dict[int, str] = {}
    try:
        data = font_path.read_bytes()
        if len(data) < 12:
            return records
        num_tables = struct.unpack(">H", data[4:6])[0]
        name_offset = 0
        for i in range(num_tables):
            rec_offset = 12 + i * 16
            if rec_offset + 16 > len(data):
                break
            tag = data[rec_offset : rec_offset + 4]
            if tag == b"name":
                name_offset = struct.unpack(">I", data[rec_offset + 8 : rec_offset + 12])[0]
                break
        if name_offset and name_offset + 6 <= len(data):
            count = struct.unpack(">H", data[name_offset + 2 : name_offset + 4])[0]
            str_offset = name_offset + struct.unpack(">H", data[name_offset + 4 : name_offset + 6])[0]
            for i in range(count):
                rec = name_offset + 6 + i * 12
                if rec + 12 > len(data):
                    break
                platform_id, _, lang_id, name_id, length, offset = struct.unpack(
                    ">HHHHHH", data[rec : rec + 12]
                )
                if name_id in (1, 2, 4, 6, 16, 17):
                    raw_bytes = data[str_offset + offset : str_offset + offset + length]
                    text = (
                        raw_bytes.decode("utf-16-be", errors="ignore")
                        if platform_id in (0, 3)
                        else raw_bytes.decode("latin-1", errors="ignore")
                    ).strip("\x00 ")
                    if text:
                        # Prefer Windows English (platform 3, lang 0x0409) or first non-empty
                        if name_id not in records or (platform_id == 3 and lang_id == 0x0409):
                            records[name_id] = text
    except Exception as exc:
        logger.debug("TTF name table parse error on %s: %s", font_path, exc)
    return records


def extract_ttf_family_name(font_path: Path) -> str:
    """
    Extract the internal Font Family Name (`nameID 1`) from a .ttf or .otf file
    so that FFmpeg's libass (`ass=subtitles.ass:fontsdir=...`) matches the exact family name.
    """
    records = parse_ttf_name_records(font_path)
    if records.get(1):
        return records[1]

    try:
        from PIL import ImageFont

        ft = ImageFont.truetype(str(font_path), size=24)
        family, _ = ft.getname()
        if family and family.strip():
            return family.strip()
    except Exception:
        pass

    return font_path.stem.replace("-", " ").replace("_", " ")


def get_ttf_codepoints(font_file: Path) -> Set[int]:
    """Extract Unicode/legacy codepoints from a font's cmap table (formats 4 and 12)."""
    cps: Set[int] = set()
    try:
        data = font_file.read_bytes()
        if len(data) < 12:
            return cps
        num_tables = struct.unpack(">H", data[4:6])[0]
        cmap_offset = 0
        for i in range(num_tables):
            rec = 12 + i * 16
            if rec + 16 > len(data):
                break
            if data[rec : rec + 4] == b"cmap":
                cmap_offset = struct.unpack(">I", data[rec + 8 : rec + 12])[0]
                break
        if not cmap_offset or cmap_offset + 4 > len(data):
            return cps
        num_subtables = struct.unpack(">H", data[cmap_offset + 2 : cmap_offset + 4])[0]
        for i in range(num_subtables):
            sub_rec = cmap_offset + 4 + i * 8
            if sub_rec + 8 > len(data):
                break
            _, _, offset = struct.unpack(">HHI", data[sub_rec : sub_rec + 8])
            st = cmap_offset + offset
            if st + 4 > len(data):
                continue
            fmt = struct.unpack(">H", data[st : st + 2])[0]
            if fmt == 4 and st + 14 <= len(data):
                seg_count = struct.unpack(">H", data[st + 6 : st + 8])[0] // 2
                end_off = st + 14
                start_off = end_off + seg_count * 2 + 2
                if start_off + seg_count * 2 <= len(data):
                    for s in range(seg_count):
                        end_c = struct.unpack(">H", data[end_off + s * 2 : end_off + s * 2 + 2])[0]
                        start_c = struct.unpack(">H", data[start_off + s * 2 : start_off + s * 2 + 2])[0]
                        if start_c == 0xFFFF:
                            continue
                        if end_c - start_c <= 4096:
                            cps.update(range(start_c, end_c + 1))
                        else:
                            cps.add(start_c)
                            cps.add(end_c)
            elif fmt == 12 and st + 16 <= len(data):
                num_groups = struct.unpack(">I", data[st + 12 : st + 16])[0]
                g_off = st + 16
                if g_off + num_groups * 12 <= len(data):
                    for g in range(num_groups):
                        start_c, end_c, _ = struct.unpack(">III", data[g_off + g * 12 : g_off + g * 12 + 12])
                        if end_c - start_c <= 4096:
                            cps.update(range(start_c, end_c + 1))
                        else:
                            cps.add(start_c)
                            cps.add(end_c)
    except Exception:
        pass
    return cps


def _font_supports_sinhala_unicode(font_file: Path, cps: Optional[Set[int]] = None) -> bool:
    """Return True if the font cmap table contains Sinhala Unicode codepoints (U+0D85..U+0DC6)."""
    codepoints = cps if cps is not None else get_ttf_codepoints(font_file)
    if not codepoints:
        return True
    return any(0x0D85 <= cp <= 0x0DC6 for cp in codepoints)


def detect_font_encoding(
    font_file: Path, ass_family: str = "", cps: Optional[Set[int]] = None
) -> str:
    """
    Detect whether a custom font expects:
      - 'unicode'       : standard Sinhala Unicode (U+0D80..U+0DFF)
      - 'isi'           : Informatics ISI legacy encoding (e.g. IsiArundathee)
      - 'wijesekara_fm' : standard FM / Tharu / ams / DL Wijesekara ASCII encoding
    """
    codepoints = cps if cps is not None else get_ttf_codepoints(font_file)
    if _font_supports_sinhala_unicode(font_file, cps=codepoints):
        return "unicode"
    name_hint = f"{font_file.stem} {ass_family}".lower()
    if "isi" in name_hint or (0x5E not in codepoints and 0x81 in codepoints and 0x8D in codepoints):
        return "isi"
    return "wijesekara_fm"


_FM_VOWELS: Dict[str, str] = {
    "\u0d85": "w",   # අ
    "\u0d86": "wd",  # ආ
    "\u0d87": "we",  # ඇ
    "\u0d88": "wE",  # ඈ
    "\u0d89": "b",   # ඉ
    "\u0d8a": "B",   # ඊ
    "\u0d8b": "W",   # උ
    "\u0d8c": "W+",  # ඌ
    "\u0d8d": "R",   # ඍ
    "\u0d8e": "RR",  # ඎ
    "\u0d8f": ",",   # ඏ
    "\u0d90": ",=",  # ඐ
    "\u0d91": "t",   # එ
    "\u0d92": "ta",  # ඒ
    "\u0d93": "ft",  # ඓ
    "\u0d94": "T",   # ඔ
    "\u0d95": "Ta",  # ඕ
    "\u0d96": "TD",  # ඖ
}

_FM_CONSONANTS: Dict[str, str] = {
    "\u0d9a": "l",     # ක
    "\u0d9b": "L",     # ඛ
    "\u0d9c": ".",     # ග
    "\u0d9d": ">",     # ඝ
    "\u0d9e": "*",     # ඞ
    "\u0d9f": "\xd5",  # ඟ (falls back to . when 0xd5 absent)
    "\u0da0": "p",     # ච
    "\u0da1": "P",     # ඡ
    "\u0da2": "c",     # ජ
    "\u0da3": "J",     # ඣ
    "\u0da4": "[",     # ඤ
    "\u0da5": "{",     # ඥ
    "\u0da6": "C",     # ඦ
    "\u0da7": "g",     # ට
    "\u0da8": "G",     # ඨ
    "\u0da9": "v",     # ඩ
    "\u0daa": "V",     # ඪ
    "\u0dab": "K",     # ණ
    "\u0dac": "~",     # ඬ
    "\u0dad": ";",     # ත
    "\u0dae": "F",     # ථ
    "\u0daf": "o",     # ද
    "\u0db0": "O",     # ධ
    "\u0db1": "k",     # න
    "\u0db3": "|",     # ඳ
    "\u0db4": "m",     # ප
    "\u0db5": "M",     # ඵ
    "\u0db6": "n",     # බ
    "\u0db7": "N",     # භ
    "\u0db8": "u",     # ම
    "\u0db9": "U",     # ඹ
    "\u0dba": "h",     # ය
    "\u0dbb": "r",     # ර
    "\u0dbd": ",",     # ල
    "\u0dc0": "j",     # ව
    "\u0dc1": "Y",     # ශ
    "\u0dc2": "I",     # ෂ
    "\u0dc3": "i",     # ස
    "\u0dc4": "y",     # හ
    "\u0dc5": "<",     # ළ
    "\u0dc6": "f*",    # ෆ
}

_FM_VOWEL_SIGNS = {
    "\u0dcf", "\u0dd0", "\u0dd1", "\u0dd2", "\u0dd3",
    "\u0dd4", "\u0dd6", "\u0dd8", "\u0df2", "\u0dd9",
    "\u0dda", "\u0ddb", "\u0ddc", "\u0ddd", "\u0dde",
}

_FM_PRECOMPOSED_LIGATURES: Dict[Tuple[str, str], int] = {
    ("\u0d9a", "\u0dd2"): 0xA2,  # කි
    ("\u0d9a", "\u0dd3"): 0xA3,  # කී
    ("\u0dbb", "\u0dd4"): 0x2F,  # රු
    ("\u0dbb", "\u0dd6"): 0x3F,  # රූ
    ("\u0dc5", "\u0dd4"): 0xA8,  # ළු
    ("\u0daf", "\u0dd4"): 0xF8,  # දු
    ("\u0daf", "\u0dd6"): 0xDE,  # දූ
    ("\u0db8", "\u0dd2"): 0xC7,  # මි
    ("\u0db8", "\u0dd3"): 0xDA,  # මී
    ("\u0dc0", "\u0dd2"): 0xC9,  # වි
    ("\u0dc0", "\u0dd3"): 0xCA,  # වී
    ("\u0db3", "\u0dd4"): 0x5C,  # ඳු
}


def unicode_to_wijesekara_fm(
    text: str, supported_codepoints: Optional[Set[int]] = None
) -> str:
    """
    Convert Sinhala Unicode text (`U+0D80..U+0DFF`) into Wijesekara / FM-Abhaya ASCII
    encoding (used by FM-*, DL-*, Tharu Digital, ams*, and Wijesekara TTF fonts).
    Pure ASCII/English strings pass through unchanged so pre-typed FM text also works.
    """
    if not text or not any("\u0d80" <= ch <= "\u0dff" for ch in text):
        return text

    def has(cp: int) -> bool:
        return supported_codepoints is None or cp in supported_codepoints

    out: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in _FM_VOWELS:
            out.append(_FM_VOWELS[ch])
            i += 1
            continue
        if ch == "\u0d82":  # ං
            out.append("x")
            i += 1
            continue
        if ch == "\u0d83":  # ඃ
            out.append("#")
            i += 1
            continue
        if ch == "\u0df4":  # ෴
            out.append(".")
            i += 1
            continue

        if ch in _FM_CONSONANTS:
            # Repaya: ර + ් + ZWJ + C
            repha = False
            if (
                ch == "\u0dbb"
                and i + 3 < n
                and text[i + 1] == "\u0dca"
                and text[i + 2] == "\u200d"
                and text[i + 3] in _FM_CONSONANTS
            ):
                repha = True
                i += 3
                ch = text[i]

            base = _FM_CONSONANTS[ch]
            if base == "\xd5" and not has(0xD5):
                base = "."
            elif base == "~" and not has(0x7E):
                base = "v"

            # Conjunct: ක + ් + ZWJ + ෂ -> ක්‍ෂ
            if (
                ch == "\u0d9a"
                and i + 3 < n
                and text[i + 1] == "\u0dca"
                and text[i + 2] == "\u200d"
                and text[i + 3] == "\u0dc2"
                and has(0xCB)
            ):
                base = "\xcb"
                i += 3

            # Medial yansaya (්‍ය) or rakaransaya (්‍ර)
            medial = ""
            if i + 3 < n and text[i + 1] == "\u0dca" and text[i + 2] == "\u200d":
                nxt = text[i + 3]
                if nxt == "\u0dba":
                    medial = "H"
                    i += 3
                elif nxt == "\u0dbb":
                    medial = "%"
                    i += 3

            sign = text[i + 1] if i + 1 < n else ""
            prefix = "`" if repha else ""

            if sign == "\u0dca":  # halant ්
                out.append(prefix + base + medial + "a")
                i += 2
                continue

            if sign not in _FM_VOWEL_SIGNS:
                out.append(prefix + base + medial)
                i += 1
                continue

            if not medial and (ch, sign) in _FM_PRECOMPOSED_LIGATURES:
                lig_cp = _FM_PRECOMPOSED_LIGATURES[(ch, sign)]
                if has(lig_cp):
                    out.append(prefix + chr(lig_cp))
                    i += 2
                    continue

            if sign == "\u0dcf":      # ා
                out.append(prefix + base + medial + "d")
            elif sign == "\u0dd0":    # ැ
                out.append(prefix + base + medial + "e")
            elif sign == "\u0dd1":    # ෑ
                out.append(prefix + base + medial + "E")
            elif sign == "\u0dd2":    # ි
                out.append(prefix + base + medial + "s")
            elif sign == "\u0dd3":    # ී
                out.append(prefix + base + medial + "S")
            elif sign == "\u0dd4":    # ු
                out.append(prefix + base + medial + "q")
            elif sign == "\u0dd6":    # ූ
                out.append(prefix + base + medial + "Q")
            elif sign == "\u0dd8":    # ෘ
                out.append(prefix + base + medial + "D")
            elif sign == "\u0df2":    # ෲ
                out.append(prefix + base + medial + "DD")
            elif sign == "\u0dd9":    # ෙ
                out.append("f" + prefix + base + medial)
            elif sign == "\u0dda":    # ේ
                out.append("f" + prefix + base + medial + "a")
            elif sign == "\u0ddb":    # ෛ
                out.append("ff" + prefix + base + medial)
            elif sign == "\u0ddc":    # ො
                out.append("f" + prefix + base + medial + "d")
            elif sign == "\u0ddd":    # ෝ
                out.append("f" + prefix + base + medial + "da")
            elif sign == "\u0dde":    # ෞ
                out.append("f" + prefix + base + medial + "D")
            i += 2
            continue

        if ch not in ("\u200d", "\u200c"):
            out.append(ch)
        i += 1

    return "".join(out)


def build_font_entry_from_file(font_file: Path) -> Dict[str, Any]:
    """
    Build a structured font metadata dictionary for a `.ttf` or `.otf` file in `FONTS_DIR`.
    Ensures distinct `family` names when Abhaya Libre Regular/Bold are listed separately,
    pairs Regular/Bold weights for multi-weight Sinhala families, and preserves the exact
    `ass_family` (`nameID 1`) required by FFmpeg `libass`.
    """
    catalog_match = next(
        (item for item in ONLINE_SINHALA_FONT_CATALOG if item["filename"].lower() == font_file.name.lower()),
        None,
    )
    records = parse_ttf_name_records(font_file)
    ass_family = records.get(1) or extract_ttf_family_name(font_file)
    subfamily = records.get(2, "Regular")
    full_name = records.get(4, "")
    cps = get_ttf_codepoints(font_file)
    encoding = detect_font_encoding(font_file, ass_family=ass_family, cps=cps)

    if not catalog_match:
        # Match Bold companion files for multi-weight catalog families (e.g. NotoSansSinhala-Bold.ttf)
        catalog_match = next(
            (
                item
                for item in ONLINE_SINHALA_FONT_CATALOG
                if item["family"].lower() == ass_family.lower()
                and "abhaya libre" not in item["family"].lower()
            ),
            None,
        )

    if catalog_match:
        ui_family = catalog_match["family"]
        label = catalog_match["label"]
        style_tag = catalog_match.get("style_tag", subfamily)
        sample_si = catalog_match.get("sample_si", "මගේ අභියස ලෝකයක් තියා")
    else:
        if subfamily.lower() not in ("regular", "normal", "") and subfamily.lower() not in ass_family.lower():
            ui_family = f"{ass_family} {subfamily}".strip()
        elif full_name and len(full_name) <= 40:
            ui_family = full_name
        else:
            ui_family = ass_family
        label = f"{ui_family} ({font_file.name})"
        style_tag = f"Custom {subfamily}"
        if encoding == "wijesekara_fm":
            style_tag = "Wijesekara (FM)"
        elif encoding == "isi":
            style_tag = "Legacy ISI Font"
        sample_si = "මගේ අභියස ලෝකයක් තියා"

    file_url = f"/api/fonts/file/{font_file.name}"
    is_bold_file = "bold" in subfamily.lower()
    mtime = float(font_file.stat().st_mtime)
    is_newly_added = (not catalog_match) and ((time.time() - mtime) < 86400 * 3)
    supported_high = sorted(cp for cp in cps if 0x80 <= cp <= 0xFF or cp == 0x7E)
    return {
        "family": ui_family,
        "ass_family": ass_family,
        "subfamily": subfamily,
        "label": label,
        "filename": font_file.name,
        "style_tag": style_tag,
        "encoding": encoding,
        "supported_codepoints": supported_high,
        "sample_si": sample_si,
        "url": file_url,
        "regular_url": "" if is_bold_file else file_url,
        "bold_url": file_url if is_bold_file else "",
        "added_at": mtime,
        "is_new": is_newly_added,
        "source": "custom",
    }


def _iter_custom_font_files() -> List[Path]:
    """Iterate custom .ttf/.otf font files in FONTS_DIR without case-insensitive duplicates."""
    if not FONTS_DIR.exists():
        return []
    return sorted(
        (
            p
            for p in FONTS_DIR.iterdir()
            if p.is_file() and p.suffix.lower() in (".ttf", ".otf")
        ),
        key=lambda p: p.name.lower(),
    )


def get_default_font_family() -> str:
    """
    Return the best default Sinhala font family available on the current OS:
    - macOS: 'Sinhala Sangam MN' (when system TTC exists)
    - Windows/Linux: bundled 'Abhaya Libre Bold' (or first Unicode Sinhala font in FONTS_DIR),
      falling back to Windows 'Iskoola Pota' / 'Nirmala UI' / 'Arial'.
    """
    available_system = {f["family"] for f in SYSTEM_DEFAULT_FONTS}
    if "Sinhala Sangam MN" in available_system:
        return "Sinhala Sangam MN"

    custom_files = _iter_custom_font_files()
    if custom_files:
        entries = [build_font_entry_from_file(fp) for fp in custom_files]
        for preferred in ("Abhaya Libre Bold", "Noto Sans Sinhala", "Noto Serif Sinhala", "Gemunu Libre"):
            if any(e["family"] == preferred for e in entries):
                return preferred
        unicode_entry = next((e for e in entries if e.get("encoding") == "unicode"), None)
        if unicode_entry:
            return str(unicode_entry["family"])
        return str(entries[0]["family"])

    for win_fam in ("Iskoola Pota", "Nirmala UI"):
        if win_fam in available_system:
            return win_fam

    return "Arial"


def _find_matching_font_files(requested_family: str) -> List[Tuple[Path, Dict[str, Any]]]:
    """Return `(font_file, entry)` pairs in `FONTS_DIR` matching `requested_family`."""
    req_low = (requested_family or get_default_font_family()).strip().lower()
    matched: List[Tuple[Path, Dict[str, Any]]] = []
    for font_file in _iter_custom_font_files():
        entry = build_font_entry_from_file(font_file)
        if req_low in (
            entry["family"].lower(),
            entry["ass_family"].lower(),
            entry["filename"].lower(),
            Path(entry["filename"]).stem.lower(),
        ):
            matched.append((font_file, entry))
    return matched


def _compute_ttf_win_scale(font_file: Path) -> float:
    """
    Compute `(usWinAscent + usWinDescent) / unitsPerEm` for a TTF/OTF file.
    `libass` scales custom fonts so `usWinAscent + usWinDescent` fits Fontsize, whereas CSS
    scales by `unitsPerEm`; multiplying ASS Fontsize by this ratio matches Chrome 1:1.
    """
    try:
        data = font_file.read_bytes()
        if len(data) < 12:
            return 1.0
        sfnt = struct.unpack(">I", data[:4])[0]
        offset = struct.unpack(">I", data[12:16])[0] if sfnt == 0x74746366 else 0
        if offset + 12 > len(data):
            return 1.0
        num_tables = struct.unpack(">H", data[offset + 4 : offset + 6])[0]
        tables: Dict[str, bytes] = {}
        for i in range(num_tables):
            pos = offset + 12 + i * 16
            if pos + 16 > len(data):
                break
            tag = data[pos : pos + 4].decode("latin1")
            tbl_off, tbl_len = struct.unpack(">II", data[pos + 8 : pos + 16])
            if tbl_off + tbl_len <= len(data):
                tables[tag] = data[tbl_off : tbl_off + tbl_len]
        head = tables.get("head")
        os2 = tables.get("OS/2")
        if not head or len(head) < 20 or not os2 or len(os2) < 78:
            return 1.0
        upm = struct.unpack(">H", head[18:20])[0]
        win_asc, win_desc = struct.unpack(">HH", os2[74:78])
        if upm > 0 and (win_asc + win_desc) > 0:
            return round(min(2.5, max(1.0, (win_asc + win_desc) / upm)), 4)
    except Exception:
        pass
    return 1.0


def get_ass_font_size_multiplier(requested_family: str) -> float:
    """
    Return the ASS `Fontsize` compensation factor so rendered `libass` glyph size
    matches browser CSS `font-size` for bundled/custom TTF/OTF fonts.
    """
    matched = _find_matching_font_files(requested_family)
    if not matched:
        return 1.0
    # Prefer bold file metrics if available, otherwise first matching file
    bold_match = next((fp for fp, e in matched if e.get("bold_url")), matched[0][0])
    return _compute_ttf_win_scale(bold_match)


def resolve_ass_font_spec(requested_family: str, requested_bold: bool = True) -> Tuple[str, bool]:
    """
    Given a `font_family` selected in the UI (e.g. `'Abhaya Libre Bold'`, `'Abhaya Libre Regular'`,
    `'Gemunu Libre'`, `'Yaldevi'`, or any uploaded custom font), resolve the exact
    `(ass_fontname, bold_flag_bool)` that FFmpeg `libass` requires to match the browser preview.
    """
    req = (requested_family or get_default_font_family()).strip()
    req_low = req.lower()

    # If a macOS-only system font is requested on Windows/Linux where it doesn't exist,
    # fall back cleanly to the best installed Sinhala font on this OS.
    available_system_low = {f["family"].lower() for f in SYSTEM_DEFAULT_FONTS}
    if req_low in ("sinhala sangam mn", "sinhala mn") and req_low not in available_system_low:
        fallback_fam = get_default_font_family()
        if fallback_fam.lower() != req_low:
            return resolve_ass_font_spec(fallback_fam, requested_bold)

    matched = _find_matching_font_files(req)
    if matched:
        entries = [e for _, e in matched]
        ass_fam = entries[0]["ass_family"]
        if req_low == "abhaya libre bold":
            return ass_fam, True
        if req_low == "abhaya libre regular":
            return ass_fam, False
        has_bold_face = any(
            e.get("bold_url") or "bold" in (e.get("subfamily") or "").lower()
            for e in entries
        )
        # Single-weight Regular fonts don't synthesize bold in Chrome (@font-face 100 900),
        # so keep Bold=0 in ASS to prevent synthetic emboldening mismatch.
        effective_bold = bool(requested_bold and has_bold_face)
        return ass_fam, effective_bold

    # Also check catalog aliases
    for item in ONLINE_SINHALA_FONT_CATALOG:
        if req_low in (item["family"].lower(), item["id"].lower()):
            if req_low == "abhaya libre bold":
                return item.get("ass_family", item["family"]), True
            if req_low == "abhaya libre regular":
                return item.get("ass_family", item["family"]), False
            return item.get("ass_family", item["family"]), requested_bold

    return req, requested_bold


def get_font_conversion_context(requested_family: str) -> Tuple[str, Optional[Set[int]]]:
    """Return `(encoding, cmap_codepoints)` for any selected font family."""
    matched = _find_matching_font_files(requested_family)
    if not matched:
        return "unicode", None
    font_file, entry = matched[0]
    cps = get_ttf_codepoints(font_file)
    return str(entry.get("encoding", "unicode")), cps


def list_available_fonts() -> Dict[str, Any]:
    """
    Return all available fonts (System + Uploaded/Downloaded in FONTS_DIR)
    plus the 1-click downloadable Online Sinhala Font Catalog and `default_font`.
    """
    custom_by_family: Dict[str, Dict[str, Any]] = {}
    custom_order: List[str] = []

    for font_file in _iter_custom_font_files():
        entry = build_font_entry_from_file(font_file)
        key = entry["family"].lower()
        if key not in custom_by_family:
            custom_by_family[key] = entry
            custom_order.append(key)
        else:
            existing = custom_by_family[key]
            if entry.get("regular_url") and not existing.get("regular_url"):
                existing["regular_url"] = entry["regular_url"]
                existing["url"] = entry["regular_url"]
            if entry.get("bold_url") and not existing.get("bold_url"):
                existing["bold_url"] = entry["bold_url"]

    custom_fonts = [custom_by_family[k] for k in custom_order]
    seen_ui_families = set(custom_order)

    # Put Sinhala Sangam MN first if present (macOS), followed by custom TTF Sinhala fonts, then other system fonts
    default_primary = [f for f in SYSTEM_DEFAULT_FONTS if f["family"] == "Sinhala Sangam MN"]
    remaining_system = [
        f
        for f in SYSTEM_DEFAULT_FONTS
        if f["family"] != "Sinhala Sangam MN" and f["family"].lower() not in seen_ui_families
    ]
    all_installed = default_primary + custom_fonts + remaining_system

    return {
        "default_font": get_default_font_family(),
        "installed": all_installed,
        "online_catalog": ONLINE_SINHALA_FONT_CATALOG,
    }


async def save_uploaded_font(upload_file: UploadFile) -> Dict[str, str]:
    """Save a user-uploaded .ttf or .otf Sinhala/custom font file into FONTS_DIR, deduplicating identical binaries."""
    orig_name = Path(upload_file.filename or "custom_font.ttf").name
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", orig_name)
    if not safe_name.lower().endswith((".ttf", ".otf")):
        raise ValueError("Only .ttf and .otf font files are supported.")

    dest_path = FONTS_DIR / safe_name

    def _save():
        raw_bytes = upload_file.file.read()
        if len(raw_bytes) < 256:
            raise ValueError("Uploaded font file is empty or corrupted.")
        incoming_sha = hashlib.sha256(raw_bytes).hexdigest()
        for existing in FONTS_DIR.iterdir():
            if existing.is_file() and existing.suffix.lower() in (".ttf", ".otf") and existing != dest_path:
                try:
                    if existing.stat().st_size == len(raw_bytes):
                        if hashlib.sha256(existing.read_bytes()).hexdigest() == incoming_sha:
                            return build_font_entry_from_file(existing)
                except Exception:
                    pass
        dest_path.write_bytes(raw_bytes)
        return build_font_entry_from_file(dest_path)

    entry = await asyncio.to_thread(_save)
    logger.info("Saved uploaded font %s (family='%s', ass_family='%s')", dest_path.name, entry["family"], entry["ass_family"])
    return entry


async def download_font_from_internet(
    font_url: Optional[str] = None,
    catalog_id: Optional[str] = None,
) -> Dict[str, str]:
    """
    Download a .ttf or .otf Sinhala font either from our 1-click catalog (`catalog_id`)
    or from any direct `.ttf`/`.otf` URL on the internet (`font_url`).
    """
    target_url = (font_url or "").strip()
    suggested_name = "downloaded_sinhala_font.ttf"

    if catalog_id:
        match = next((item for item in ONLINE_SINHALA_FONT_CATALOG if item["id"] == catalog_id), None)
        if not match:
            raise ValueError(f"Unknown catalog font id '{catalog_id}'.")
        target_url = match["url"]
        suggested_name = re.sub(r"[^A-Za-z0-9._-]", "_", match["filename"])
    elif target_url:
        url_path = target_url.split("?")[0].rstrip("/")
        tail = Path(url_path).name
        if tail.lower().endswith((".ttf", ".otf")):
            suggested_name = re.sub(r"[^A-Za-z0-9._-]", "_", tail)
        else:
            suggested_name = f"web_font_{abs(hash(target_url)) % 100000}.ttf"
    else:
        raise ValueError("Please provide either a catalog_id or a direct font URL.")

    dest_path = FONTS_DIR / suggested_name

    # If already downloaded locally and valid, return immediately without network call
    if dest_path.exists() and dest_path.stat().st_size > 1024:
        return await asyncio.to_thread(build_font_entry_from_file, dest_path)

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) TikTokLyricsCreator/2.0"
    }
    async with create_resilient_async_client(timeout=25.0, headers=headers) as client:
        resp = await client.get(target_url)
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to download font from URL (HTTP {resp.status_code}).")
        content = resp.content
        if len(content) < 1024:
            raise RuntimeError("Downloaded file is too small to be a valid .ttf/.otf font.")
        dest_path.write_bytes(content)

    entry = await asyncio.to_thread(build_font_entry_from_file, dest_path)
    logger.info("Downloaded internet font to %s (family='%s')", dest_path.name, entry["family"])
    return entry


def delete_custom_font(font_identifier: str) -> List[str]:
    """
    Delete custom `.ttf` / `.otf` font file(s) from `FONTS_DIR` matching either
    the UI `family` name or filename. System built-in fonts are never deleted.
    """
    ident = (font_identifier or "").strip()
    if not ident:
        raise ValueError("Font identifier is required.")

    fonts_root = FONTS_DIR.resolve()
    deleted_names: List[str] = []

    # Check direct filename inside FONTS_DIR first
    candidate = (fonts_root / Path(ident).name).resolve()
    if candidate.parent == fonts_root and candidate.exists() and candidate.is_file():
        if candidate.suffix.lower() in (".ttf", ".otf"):
            candidate.unlink()
            deleted_names.append(candidate.name)
            logger.info("Deleted custom font file: %s", candidate.name)
            return deleted_names

    # Otherwise delete all custom font files matching the UI/ASS family name
    matched = _find_matching_font_files(ident)
    seen_paths: Set[Path] = set()
    for font_file, _ in matched:
        resolved = font_file.resolve()
        if resolved.parent == fonts_root and resolved.exists() and resolved not in seen_paths:
            seen_paths.add(resolved)
            resolved.unlink()
            deleted_names.append(resolved.name)
            logger.info("Deleted custom font file: %s", resolved.name)

    if not deleted_names:
        raise FileNotFoundError(f"Custom font '{ident}' not found in fonts directory.")

    return deleted_names


def render_ffmpeg_font_preview_png(
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
    watermark_text: str = "The_Black Hart 🖤",
) -> bytes:
    """
    Renders a 100% real FFmpeg `libass` HarfBuzz-shaped preview PNG frame (920x260)
    using the exact `.ttf`/`.otf` or system font, outline, shadow, and color settings.
    Caches rendered previews in `TEMP_DIR / font_previews` for instant response.
    """
    from backend.models import FrameLayout, LyricLine, StylePreset, VideoEditConfig
    from backend.services.media_service import get_ffmpeg_binary
    from backend.services.subtitle_service import generate_ass_subtitles

    preview_dir = TEMP_DIR / "font_previews"
    preview_dir.mkdir(parents=True, exist_ok=True)

    clean_text = (sample_text or "මගේ අභියස ලෝකයක් තියා").strip()[:90]
    resolved_ass_fam, resolved_bold = resolve_ass_font_spec(font_family, bold_text)
    cache_key = hashlib.md5(
        f"v8|{font_family}|{resolved_ass_fam}|{resolved_bold}|{clean_text}|{font_size}|{font_thickness}|{font_brightness}|{primary_color_hex}|{outline_width}|{shadow_depth}|{bold_text}|{italic_text}|{watermark_text}".encode(
            "utf-8"
        )
    ).hexdigest()[:14]

    cached_png = preview_dir / f"preview_{cache_key}.png"
    if cached_png.exists() and cached_png.stat().st_size > 500:
        return cached_png.read_bytes()

    ass_path = preview_dir / f"preview_{cache_key}.ass"
    cfg = VideoEditConfig(
        font_family=font_family,
        font_size=max(22, min(76, int(font_size))),
        font_thickness=max(0.0, min(4.0, float(font_thickness))),
        font_brightness=max(0.4, min(2.0, float(font_brightness))),
        primary_color_hex=primary_color_hex,
        outline_width=outline_width,
        shadow_depth=shadow_depth,
        bold_text=bold_text,
        italic_text=italic_text,
        text_pos_y_percent=52,
        watermark_text=watermark_text,
    )

    generate_ass_subtitles(
        lines=[LyricLine(start=0.0, end=4.0, text=clean_text)],
        output_ass_path=ass_path,
        preset=StylePreset.SOFT_LINE_FADE,
        frame_layout=FrameLayout.ROUNDED_SQUARE_CARD,
        watermark_text="",
        total_duration=4.0,
        play_res_x=1080,
        play_res_y=1920,
        edit_config=cfg,
    )

    try:
        fonts_dir_arg = os.path.relpath(FONTS_DIR.resolve(), preview_dir.resolve()).replace("\\", "/")
    except ValueError:
        # Cross-drive fallback on Windows: single quotes protect ':' and spaces (do NOT escape ':')
        fonts_dir_arg = str(FONTS_DIR.resolve()).replace("\\", "/")
    safe_fonts_dir_arg = fonts_dir_arg.replace("'", r"'\''")

    vf_chain = (
        f"drawbox=x=0:y=0:w=1080:h=1920:color=0x16120e:t=fill,"
        f"drawbox=x=80:y=820:w=920:h=280:color=0x261c14@0.85:t=fill,"
        f"ass={ass_path.name}:fontsdir='{safe_fonts_dir_arg}',"
        f"crop=920:240:80:840"
    )

    cmd = [
        get_ffmpeg_binary(),
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=0x12100e:s=1080x1920:d=1.0",
        "-vf",
        vf_chain,
        "-ss",
        "0.5",
        "-frames:v",
        "1",
        cached_png.name,
    ]
    res = subprocess.run(cmd, cwd=str(preview_dir.resolve()), capture_output=True)
    try:
        ass_path.unlink(missing_ok=True)
    except Exception:
        pass

    if res.returncode != 0 or not cached_png.exists():
        raise RuntimeError(
            f"FFmpeg font preview failed: {res.stderr.decode(errors='ignore')[-300:]}"
        )

    # Prune oldest cached font preview PNGs when cache exceeds 80 files
    try:
        png_files = sorted(preview_dir.glob("preview_*.png"), key=lambda p: p.stat().st_mtime)
        if len(png_files) > 80:
            for stale in png_files[: len(png_files) - 80]:
                if stale != cached_png:
                    stale.unlink(missing_ok=True)
    except Exception:
        pass

    return cached_png.read_bytes()
