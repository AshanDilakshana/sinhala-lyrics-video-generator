import logging
import re
from pathlib import Path
from typing import Callable, List, Optional

from backend.models import FrameLayout, LyricLine, StylePreset, VideoEditConfig
from backend.services.font_service import (
    get_ass_font_size_multiplier,
    get_font_conversion_context,
    resolve_ass_font_spec,
    unicode_to_wijesekara_fm,
)

logger = logging.getLogger(__name__)


def _format_ass_timestamp(seconds: float) -> str:
    """
    Format float seconds into Advanced SubStation Alpha (ASS) timestamp `H:MM:SS.cc`
    (where `cc` is centiseconds, 00-99).
    """
    seconds = max(0.0, seconds)
    total_cs = int(round(seconds * 100))
    hours = total_cs // 360000
    rem = total_cs % 360000
    minutes = rem // 6000
    rem = rem % 6000
    secs = rem // 100
    cs = rem % 100
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _escape_ass_text(text: str) -> str:
    """Escape characters that have special meaning in ASS dialogue lines."""
    # Note: In libass, literal backslash is preserved except before \n, \N, \h;
    # doubling `\\` renders two visible glyphs (e.g. double ඳු in FM fonts).
    escaped = text.replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")
    return escaped


def _apply_font_brightness_hex(hex_str: str, font_brightness: float = 1.0) -> str:
    """Scale or brighten an `#RRGGBB` color string according to `font_brightness`."""
    cleaned = (hex_str or "#FFFFFF").strip().lstrip("#")
    if len(cleaned) != 6:
        cleaned = "FFFFFF"
    try:
        r = int(cleaned[0:2], 16)
        g = int(cleaned[2:4], 16)
        b = int(cleaned[4:6], 16)
    except ValueError:
        r, g, b = 255, 255, 255

    fb = max(0.4, min(2.0, float(font_brightness or 1.0)))
    if fb < 1.0:
        r = int(round(max(0, min(255, r * fb))))
        g = int(round(max(0, min(255, g * fb))))
        b = int(round(max(0, min(255, b * fb))))
    elif fb > 1.0:
        lift = min(1.0, (fb - 1.0) * 0.65)
        r = int(round(max(0, min(255, r + (255 - r) * lift))))
        g = int(round(max(0, min(255, g + (255 - g) * lift))))
        b = int(round(max(0, min(255, b + (255 - b) * lift))))
    return f"#{r:02X}{g:02X}{b:02X}"


def _hex_to_ass_color(hex_str: str, alpha_hex: str = "00") -> str:
    """Convert standard HTML `#RRGGBB` hex string into ASS `&HAABBGGRR` format."""
    cleaned = hex_str.strip().lstrip("#")
    if len(cleaned) != 6:
        return f"&H{alpha_hex}FFFFFF"
    rr = cleaned[0:2].upper()
    gg = cleaned[2:4].upper()
    bb = cleaned[4:6].upper()
    return f"&H{alpha_hex}{bb}{gg}{rr}"


def watermark_uses_bitmap_overlay(text: str) -> bool:
    """Return True when watermark text contains emoji glyphs libass cannot shape cleanly."""
    return any(ord(ch) >= 0x2600 for ch in (text or ""))


def _build_progressive_word_fade_line(
    line: LyricLine,
    pos_tag: str = "",
    text_transform: Optional[Callable[[str], str]] = None,
    target_alpha_hex: str = "00",
) -> str:
    """
    Replicates the The_Black Hart word-by-word progressive opacity reveal:
    Uses per-word acoustic syllable onset timings (`line.words`) when available,
    so each Sinhala word fades in at the exact millisecond the singer articulates it!
    """
    duration_ms = max(int(round((line.end - line.start) * 1000)), 400)
    fade_out_ms = min(240, duration_ms // 4)
    transform = text_transform or (lambda s: s)
    alpha_tag = f"&H{target_alpha_hex}&"

    # Use acoustic word-level onset timings if computed by the vocal aligner
    if line.words and len(line.words) > 1:
        parts: List[str] = []
        max_reveal_end = max(140, duration_ms - fade_out_ms)
        for idx, wt in enumerate(line.words):
            safe_word = _escape_ass_text(transform(wt.word))
            t_start = max(0, min(wt.start_ms, max(0, max_reveal_end - 110)))
            t_end = max(t_start + 100, min(wt.end_ms, max_reveal_end))
            if idx == 0:
                parts.append(
                    rf"{{{pos_tag}\fad(0,{fade_out_ms})\alpha&HFF&\t({t_start},{t_end},\alpha{alpha_tag})}}{safe_word}"
                )
            else:
                parts.append(
                    rf"{{\alpha&HFF&\t({t_start},{t_end},\alpha{alpha_tag})}}{safe_word}"
                )
        return " ".join(parts)

    words = [w for w in line.text.strip().split(" ") if w]
    if len(words) <= 1:
        safe = _escape_ass_text(transform(line.text.strip()))
        return rf"{{{pos_tag}\fad(180,{fade_out_ms})}}{safe}"

    reveal_span_ms = max(int(duration_ms * 0.68), len(words) * 150)
    step_ms = reveal_span_ms // len(words)
    word_fade_dur = max(min(step_ms + 110, 320), 140)

    parts = []
    for idx, word in enumerate(words):
        safe_word = _escape_ass_text(transform(word))
        t_start = idx * step_ms
        t_end = min(t_start + word_fade_dur, duration_ms - fade_out_ms)
        if idx == 0:
            parts.append(
                rf"{{{pos_tag}\fad(0,{fade_out_ms})\alpha&HFF&\t(0,{t_end},\alpha{alpha_tag})}}{safe_word}"
            )
        else:
            parts.append(
                rf"{{\alpha&HFF&\t({t_start},{t_end},\alpha{alpha_tag})}}{safe_word}"
            )

    return " ".join(parts)


def generate_ass_subtitles(
    lines: List[LyricLine],
    output_ass_path: Path,
    preset: StylePreset = StylePreset.RW_AESTHETIC_SINHALA,
    frame_layout: FrameLayout = FrameLayout.ROUNDED_SQUARE_CARD,
    watermark_text: str = "The_Black Hart 🖤",
    total_duration: float = 30.0,
    play_res_x: int = 1080,
    play_res_y: int = 1920,
    edit_config: Optional[VideoEditConfig] = None,
) -> Path:
    """
    Step C: Convert time-synced LyricLine data into a styled .ass subtitle file.
    Respects custom Sinhala font family, font size, hex color, shadow/outline,
    vertical Y position, and global `lyrics_offset_sec` from `edit_config`.
    """
    cfg = edit_config or VideoEditConfig(
        style_preset=preset,
        frame_layout=frame_layout,
        watermark_text=watermark_text,
    )

    raw_font_name = (cfg.font_family or "Sinhala Sangam MN").strip()
    font_name, effective_bold = resolve_ass_font_spec(raw_font_name, cfg.bold_text)
    font_size = max(12, int(round(cfg.font_size * get_ass_font_size_multiplier(raw_font_name))))
    font_encoding, font_cps = get_font_conversion_context(raw_font_name)

    def _transform_lyric_text(raw: str) -> str:
        if font_encoding == "wijesekara_fm":
            converted = unicode_to_wijesekara_fm(raw, font_cps)
            # Prevent `\n`, `\N`, `\h` ASS escape collisions when ඳු (`\`) precedes බ/භ/ය
            return re.sub(r"\\(?=[nNh])", "|q", converted)
        return raw
    font_brightness = round(max(0.4, min(2.0, float(getattr(cfg, "font_brightness", 1.0) or 1.0))), 2)
    base_hex = cfg.primary_color_hex
    if preset == StylePreset.TIKTOK_YELLOW and cfg.primary_color_hex.upper() == "#FFFFFF":
        base_hex = "#FFFF00"
    elif preset == StylePreset.NEON_CYAN and cfg.primary_color_hex.upper() == "#FFFFFF":
        base_hex = "#00FFFF"

    bright_primary_hex = _apply_font_brightness_hex(base_hex, font_brightness)
    bright_secondary_hex = _apply_font_brightness_hex("#FFFFFF", font_brightness)
    primary_color = _hex_to_ass_color(bright_primary_hex, "00")
    secondary_color = _hex_to_ass_color(bright_secondary_hex, "00")

    is_soft_aesthetic = preset in (StylePreset.RW_AESTHETIC_SINHALA, StylePreset.SOFT_LINE_FADE)
    extra_thick = round(max(0.0, min(4.0, float(getattr(cfg, "font_thickness", 0.0) or 0.0))), 2)
    outline_color = "&H66000000" if is_soft_aesthetic else "&H00000000"
    back_color = "&HA8000000" if is_soft_aesthetic else "&H80000000"
    bold_flag = -1 if effective_bold else 0
    italic_flag = -1 if cfg.italic_text else 0
    if is_soft_aesthetic:
        outline_width = round(max(0.0, (cfg.outline_width - 2.2) * 0.8), 2)
        if extra_thick > 0:
            outline_color = primary_color
            outline_width = extra_thick
    else:
        outline_width = round(cfg.outline_width, 2)
    shadow_depth = round(cfg.shadow_depth, 2)
    offset_sec = cfg.lyrics_offset_sec

    if frame_layout == FrameLayout.ROUNDED_SQUARE_CARD:
        card_w, card_h = 920, 920
    elif frame_layout == FrameLayout.ROUNDED_PORTRAIT_CARD:
        card_w, card_h = 920, 1150
    else:
        card_w, card_h = play_res_x, play_res_y

    card_left = (play_res_x - card_w) // 2
    card_top = (play_res_y - card_h) // 2

    if frame_layout == FrameLayout.FULLSCREEN_VERTICAL:
        lyric_area_top, lyric_area_h = 160, 1600
    else:
        lyric_area_top, lyric_area_h = card_top, card_h

    wm_size = max(8, min(48, int(cfg.watermark_size)))
    wm_x = int(card_left + (cfg.watermark_pos_x_percent / 100.0) * card_w)
    wm_y = int(card_top + (cfg.watermark_pos_y_percent / 100.0) * card_h)

    pos_x = play_res_x // 2
    pos_y = int(lyric_area_top + (cfg.text_pos_y_percent / 100.0) * lyric_area_h)
    pos_tag = rf"\pos({pos_x},{pos_y})"

    use_pop_thicken_layer = (not is_soft_aesthetic) and extra_thick > 0
    if use_pop_thicken_layer:
        center_outline_col = primary_color
        alt_outline_col = secondary_color
        center_outline_w = extra_thick
        center_shadow = 0.0
        pop_back_styles = (
            f"Style: TikTokCenterBack,{font_name},{font_size},{primary_color},{secondary_color},&H00000000,{back_color},{bold_flag},{italic_flag},0,0,100,100,0,0,1,{round(outline_width + extra_thick, 2)},{shadow_depth},5,110,110,0,1\n"
            f"Style: TikTokAltBack,{font_name},{font_size},{secondary_color},{primary_color},&H00000000,{back_color},{bold_flag},{italic_flag},0,0,100,100,0,0,1,{round(outline_width + extra_thick, 2)},{shadow_depth},5,110,110,0,1\n"
        )
    else:
        center_outline_col = outline_color
        alt_outline_col = outline_color
        center_outline_w = outline_width
        center_shadow = shadow_depth
        pop_back_styles = ""

    use_glow_layer = font_brightness > 1.02
    glow_blur = 0.0
    glow_alpha_hex = "C8"
    if use_glow_layer:
        boost = min(1.0, font_brightness - 1.0)
        glow_outline_w = round(0.9 + boost * 1.6 + extra_thick * 0.4, 2)
        glow_blur = round(4.0 + boost * 5.0, 1)
        glow_alpha_int = int(round(max(155, 228 - boost * 65)))
        glow_alpha_hex = f"{glow_alpha_int:02X}"
        glow_color = _hex_to_ass_color(bright_primary_hex, glow_alpha_hex)
        glow_style_line = (
            f"Style: TikTokGlow,{font_name},{font_size},{glow_color},{glow_color},{glow_color},&HFF000000,{bold_flag},{italic_flag},0,0,100,100,0,0,1,{glow_outline_w},0.0,5,110,110,0,1\n"
        )
    else:
        glow_style_line = ""

    ass_header = f"""[Script Info]
; Generated by TikTok Aesthetic Lyrics Video Editor
Title: Aesthetic TikTok Synced Lyrics
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: {play_res_x}
PlayResY: {play_res_y}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{glow_style_line}{pop_back_styles}Style: TikTokCenter,{font_name},{font_size},{primary_color},{secondary_color},{center_outline_col},{back_color},{bold_flag},{italic_flag},0,0,100,100,0,0,1,{center_outline_w},{center_shadow},5,110,110,0,1
Style: TikTokAlt,{font_name},{font_size},{secondary_color},{primary_color},{alt_outline_col},{back_color},{bold_flag},{italic_flag},0,0,100,100,0,0,1,{center_outline_w},{center_shadow},5,110,110,0,1
Style: Watermark,Georgia,{wm_size},&H45FFFFFF,&H45FFFFFF,&H90000000,&H90000000,0,-1,0,0,100,100,0.8,0,1,1.0,1.2,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    dialogue_events: List[str] = []

    effective_wm = (cfg.watermark_text if edit_config is not None else watermark_text).strip()
    if effective_wm and not watermark_uses_bitmap_overlay(effective_wm):
        safe_wm = _escape_ass_text(effective_wm)
        end_wm = _format_ass_timestamp(max(total_duration, 5.0))
        dialogue_events.append(
            f"Dialogue: 0,0:00:00.00,{end_wm},Watermark,,0,0,0,,{{\\an5\\pos({wm_x},{wm_y})\\fad(400,600)}}{safe_wm}"
        )

    pop_animation = rf"{{{pos_tag}\fad(90,90)\t(0,110,\fscx108\fscy108)\t(110,220,\fscx100\fscy100)}}"
    soft_fade_animation = rf"{{{pos_tag}\fad(220,240)}}"

    for idx, line in enumerate(lines):
        shifted_start = max(0.0, line.start + offset_sec)
        shifted_end = max(shifted_start + 0.4, line.end + offset_sec)
        start_ass = _format_ass_timestamp(shifted_start)
        end_ass = _format_ass_timestamp(shifted_end)

        if preset == StylePreset.RW_AESTHETIC_SINHALA:
            styled_text = _build_progressive_word_fade_line(
                line, pos_tag=pos_tag, text_transform=_transform_lyric_text
            )
            glow_text = (
                _build_progressive_word_fade_line(
                    line,
                    pos_tag=rf"{pos_tag}\blur{glow_blur}",
                    text_transform=_transform_lyric_text,
                    target_alpha_hex=glow_alpha_hex,
                )
                if use_glow_layer
                else ""
            )
            style_name = "TikTokCenter"
        elif preset == StylePreset.SOFT_LINE_FADE:
            escaped_line = _escape_ass_text(_transform_lyric_text(line.text))
            styled_text = f"{soft_fade_animation}{escaped_line}"
            glow_text = rf"{{{pos_tag}\blur{glow_blur}\fad(220,240)}}{escaped_line}" if use_glow_layer else ""
            style_name = "TikTokCenter"
        else:
            escaped_line = _escape_ass_text(_transform_lyric_text(line.text))
            styled_text = f"{pop_animation}{escaped_line}"
            glow_text = (
                rf"{{{pos_tag}\blur{glow_blur}\fad(90,90)\t(0,110,\fscx108\fscy108)\t(110,220,\fscx100\fscy100)}}{escaped_line}"
                if use_glow_layer
                else ""
            )
            style_name = "TikTokCenter" if idx % 2 == 0 else "TikTokAlt"

        if use_glow_layer and glow_text:
            dialogue_events.append(
                f"Dialogue: 0,{start_ass},{end_ass},TikTokGlow,,0,0,0,,{glow_text}"
            )
        if use_pop_thicken_layer:
            dialogue_events.append(
                f"Dialogue: 1,{start_ass},{end_ass},{style_name}Back,,0,0,0,,{styled_text}"
            )
        dialogue_events.append(
            f"Dialogue: 2,{start_ass},{end_ass},{style_name},,0,0,0,,{styled_text}"
        )

    full_ass_content = ass_header + "\n".join(dialogue_events) + "\n"
    output_ass_path.write_text(full_ass_content, encoding="utf-8-sig")
    logger.info(
        "Generated .ass subtitle file at %s (%d lines, font='%s', size=%d, offset=%.2fs)",
        output_ass_path,
        len(lines),
        font_name,
        font_size,
        offset_sec,
    )
    return output_ass_path
