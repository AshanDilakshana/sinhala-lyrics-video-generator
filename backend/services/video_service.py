import asyncio
import logging
import math
import os
import shutil
from pathlib import Path
from typing import List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont

from backend.config import FONTS_DIR
from backend.models import ColorGradePreset, FrameLayout, LoopMode, VideoEditConfig
from backend.services.media_service import get_ffmpeg_binary, get_media_duration
from backend.services.subtitle_service import watermark_uses_bitmap_overlay

logger = logging.getLogger(__name__)

CANVAS_W = 1080
CANVAS_H = 1920

BT709_COLOR_FLAGS: List[str] = [
    "-colorspace",
    "bt709",
    "-color_primaries",
    "bt709",
    "-color_trc",
    "bt709",
    "-color_range",
    "tv",
]


def _build_css_vignette_layer(card_w: int, card_h: int) -> Image.Image:
    """
    Build an RGBA layer replicating Live Canvas CSS:
    `radial-gradient(circle, rgba(0,0,0,0) 48%, rgba(0,0,0,0.68) 100%)`
    using farthest-corner circle radius centered inside (card_w, card_h).
    """
    import numpy as np

    radius = 0.5 * math.sqrt(card_w * card_w + card_h * card_h)
    yy, xx = np.ogrid[0:card_h, 0:card_w]
    cx = (card_w - 1) / 2.0
    cy = (card_h - 1) / 2.0
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    norm = dist / max(radius, 1.0)
    alpha_f = np.clip((norm - 0.48) / 0.52, 0.0, 1.0) * 0.68 * 255.0
    alpha_u8 = np.rint(alpha_f).astype(np.uint8)

    rgba = np.zeros((card_h, card_w, 4), dtype=np.uint8)
    rgba[..., 3] = alpha_u8
    return Image.fromarray(rgba, mode="RGBA")


def create_rounded_card_mask_png(
    output_png_path: Path,
    frame_layout: FrameLayout = FrameLayout.ROUNDED_SQUARE_CARD,
    corner_radius: int = 48,
) -> Tuple[Path, int, int, int, int]:
    """
    Generate an anti-aliased 1080x1920 RGBA PNG overlay mask that is solid pitch-black
    (#000000) outside the centered rounded-rectangle video frame and transparent inside.

    Returns:
        Tuple[Path, card_w, card_h, pad_x, pad_y]
    """
    if frame_layout == FrameLayout.ROUNDED_SQUARE_CARD:
        card_w, card_h = 920, 920
    elif frame_layout == FrameLayout.ROUNDED_PORTRAIT_CARD:
        card_w, card_h = 920, 1150
    else:
        card_w, card_h = CANVAS_W, CANVAS_H

    pad_x = (CANVAS_W - card_w) // 2
    pad_y = (CANVAS_H - card_h) // 2

    scale = 2
    hi_w, hi_h = CANVAS_W * scale, CANVAS_H * scale
    mask_img = Image.new("RGBA", (hi_w, hi_h), (0, 0, 0, 255))
    draw = ImageDraw.Draw(mask_img)

    x0 = pad_x * scale
    y0 = pad_y * scale
    x1 = (pad_x + card_w) * scale
    y1 = (pad_y + card_h) * scale
    r = max(0, corner_radius) * scale

    draw.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=(0, 0, 0, 0))

    final_mask = mask_img.resize((CANVAS_W, CANVAS_H), resample=Image.Resampling.LANCZOS)
    final_mask.save(output_png_path, format="PNG")
    return output_png_path, card_w, card_h, pad_x, pad_y


def create_watermark_overlay_png(
    output_png_path: Path,
    watermark_text: str,
    frame_layout: FrameLayout,
    card_w: int,
    card_h: int,
    pad_x: int,
    pad_y: int,
    watermark_size: int = 15,
    watermark_pos_x_percent: int = 50,
    watermark_pos_y_percent: int = 98,
) -> Optional[Tuple[Path, int, int]]:
    """
    Render an RGBA PNG watermark at `(watermark_pos_x_percent, watermark_pos_y_percent)`
    inside the card when `watermark_text` contains emoji glyphs (e.g. `The_Black Hart 🖤`)
    that libass cannot shape cleanly.

    Returns `(png_path, overlay_x, overlay_y)` or `None` if plain ASS handles it.
    """
    clean_wm = (watermark_text or "").strip()
    if not clean_wm or not watermark_uses_bitmap_overlay(clean_wm):
        return None

    scale = 2
    wm_sz = max(8, min(48, int(watermark_size)))
    txt_size = wm_sz * scale
    emoji_size = wm_sz  # Scaled cleanly from Apple Color Emoji strike (20/32/48)

    win_fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"

    text_font = None
    for fpath in (
        "/System/Library/Fonts/Supplemental/Georgia Italic.ttf",
        "/System/Library/Fonts/Supplemental/Georgia.ttf",
        "/System/Library/Fonts/Times.ttc",
        str(win_fonts_dir / "georgiai.ttf"),
        str(win_fonts_dir / "georgia.ttf"),
        str(win_fonts_dir / "timesi.ttf"),
        str(win_fonts_dir / "times.ttf"),
        str(win_fonts_dir / "arial.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
        str(FONTS_DIR / "AbhayaLibre-Regular.ttf"),
    ):
        if Path(fpath).is_file():
            try:
                text_font = ImageFont.truetype(fpath, txt_size)
                break
            except Exception:
                continue
    if text_font is None:
        text_font = ImageFont.load_default()

    emoji_font = None
    emoji_strike = emoji_size
    emoji_candidates = (
        (Path("/System/Library/Fonts/Apple Color Emoji.ttc"), (48, 32, 20)),
        (win_fonts_dir / "seguiemj.ttf", (48, 32, emoji_size * scale)),
        (Path("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"), (109, 48)),
    )
    for emoji_path, strike_sizes in emoji_candidates:
        if emoji_path.is_file():
            for cand_sz in strike_sizes:
                try:
                    emoji_font = ImageFont.truetype(str(emoji_path), max(12, int(cand_sz)))
                    emoji_strike = max(12, int(cand_sz))
                    break
                except Exception:
                    continue
        if emoji_font is not None:
            break

    def _safe_emoji_bbox(draw_obj: ImageDraw.ImageDraw, seg_text: str) -> Tuple[int, int, int, int]:
        try:
            return draw_obj.textbbox((0, 0), seg_text, font=emoji_font, embedded_color=True)
        except Exception:
            return draw_obj.textbbox((0, 0), seg_text, font=emoji_font)

    def _safe_emoji_draw(draw_obj: ImageDraw.ImageDraw, xy: Tuple[int, int], seg_text: str) -> None:
        try:
            draw_obj.text(xy, seg_text, font=emoji_font, embedded_color=True)
        except Exception:
            draw_obj.text(xy, seg_text, font=emoji_font, fill=(255, 255, 255, 215))

    # Split text into contiguous non-emoji vs emoji runs
    runs: List[Tuple[bool, str]] = []
    for ch in clean_wm:
        if ord(ch) in (0xFE0F, 0xFE0E, 0x200D):
            if runs and runs[-1][0]:
                runs[-1] = (True, runs[-1][1] + ch)
            continue
        is_emo = ord(ch) >= 0x2600
        if runs and runs[-1][0] == is_emo:
            runs[-1] = (is_emo, runs[-1][1] + ch)
        else:
            runs.append((is_emo, ch))

    tmp_img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    tmp_draw = ImageDraw.Draw(tmp_img)

    target_emoji_hi = emoji_size * scale
    total_w_hi = 0
    max_h_hi = txt_size + 12
    run_metrics: List[Tuple[bool, str, int]] = []
    for is_emo, seg in runs:
        if is_emo and emoji_font is not None:
            bbox = _safe_emoji_bbox(tmp_draw, seg)
            seg_w = max(1, bbox[2] - bbox[0])
            seg_h = max(1, bbox[3] - bbox[1])
            drawn_w = int(round(seg_w * (target_emoji_hi / max(seg_h, emoji_strike))))
        else:
            bbox = tmp_draw.textbbox((0, 0), seg, font=text_font)
            drawn_w = max(1, int(round(bbox[2] - bbox[0])))
        run_metrics.append((is_emo, seg, drawn_w))
        total_w_hi += drawn_w

    pad_hi = 8
    wm_w_hi = total_w_hi + pad_hi * 2
    wm_h_hi = max_h_hi + pad_hi
    canvas_hi = Image.new("RGBA", (wm_w_hi, wm_h_hi), (0, 0, 0, 0))
    draw_hi = ImageDraw.Draw(canvas_hi)

    cur_x = pad_hi
    baseline_y = pad_hi // 2
    for is_emo, seg, drawn_w in run_metrics:
        if is_emo and emoji_font is not None:
            bbox = _safe_emoji_bbox(draw_hi, seg)
            bw = max(1, bbox[2] - bbox[0])
            bh = max(1, bbox[3] - bbox[1])
            emo_patch = Image.new("RGBA", (bw + 8, bh + 8), (0, 0, 0, 0))
            _safe_emoji_draw(ImageDraw.Draw(emo_patch), (4 - bbox[0], 4 - bbox[1]), seg)
            emo_scaled = emo_patch.resize(
                (drawn_w + 4, target_emoji_hi),
                resample=Image.Resampling.LANCZOS,
            )
            # Attenuate alpha slightly so emoji matches subtle watermark opacity
            r_c, g_c, b_c, a_c = emo_scaled.split()
            a_c = a_c.point(lambda p: int(p * 0.82))
            emo_scaled = Image.merge("RGBA", (r_c, g_c, b_c, a_c))
            emo_y = baseline_y + max(0, (txt_size - target_emoji_hi) // 2) + 2
            canvas_hi.alpha_composite(emo_scaled, (cur_x, emo_y))
        else:
            # Soft shadow + subtle white watermark text matching Georgia ASS watermark
            draw_hi.text((cur_x + 2, baseline_y + 2), seg, font=text_font, fill=(0, 0, 0, 145))
            draw_hi.text((cur_x, baseline_y), seg, font=text_font, fill=(255, 255, 255, 195))
        cur_x += drawn_w

    wm_w = max(2, wm_w_hi // scale)
    wm_h = max(2, wm_h_hi // scale)
    final_wm = canvas_hi.resize((wm_w, wm_h), resample=Image.Resampling.LANCZOS)
    final_wm.save(output_png_path, format="PNG")

    anchor_x = int(pad_x + (max(5, min(95, watermark_pos_x_percent)) / 100.0) * card_w)
    anchor_y = int(pad_y + (max(4, min(99, watermark_pos_y_percent)) / 100.0) * card_h)

    overlay_x = max(0, min(CANVAS_W - wm_w, anchor_x - wm_w // 2))
    overlay_y = max(0, min(CANVAS_H - wm_h, anchor_y - wm_h // 2))
    return output_png_path, overlay_x, overlay_y


def _get_color_grade_filter(edit_config: VideoEditConfig) -> str:
    """
    Replicate Live Canvas's CSS `filter` pipeline (`buildLiveCssFilter` in `page.tsx`)
    pixel-for-pixel in sRGB (`rgb24`) space:
      `contrast(c_eff) brightness(b_eff) saturate(s_eff) [sepia(t) | grayscale(1)]`
    (Vignette is composited inside `create_rounded_card_mask_png` using the exact CSS radial gradient.)
    """
    preset = edit_config.color_grade
    b_mult = max(0.0, 1.0 + edit_config.brightness)
    c_mult = max(0.0, edit_config.contrast)
    s_mult = max(0.0, edit_config.saturation)

    sepia_t = 0.0
    if preset == ColorGradePreset.GOLDEN_SUNSET:
        c_eff = 1.12 * c_mult
        b_eff = 0.98 * b_mult
        s_eff = 1.35 * s_mult
        sepia_t = 0.18
    elif preset == ColorGradePreset.MOODY_CINEMA:
        c_eff = 1.24 * c_mult
        b_eff = 0.94 * b_mult
        s_eff = 1.08 * s_mult
    elif preset == ColorGradePreset.DREAMY_PASTEL:
        c_eff = 0.95 * c_mult
        b_eff = 1.04 * b_mult
        s_eff = 1.15 * s_mult
    elif preset == ColorGradePreset.BLACK_AND_WHITE:
        c_eff = 1.15 * c_mult
        b_eff = b_mult
        s_eff = 0.0
    else:
        c_eff = c_mult
        b_eff = b_mult
        s_eff = s_mult

    filters: List[str] = []

    # 1. W3C CSS contrast(c_eff) -> brightness(b_eff) in sRGB [0..255]
    if abs(c_eff - 1.0) > 0.001 or abs(b_eff - 1.0) > 0.001:
        lut_expr = f"clip(clip((val-127.5)*{c_eff:.4f}+127.5,0,255)*{b_eff:.4f},0,255)"
        filters.append(f"lutrgb=r='{lut_expr}':g='{lut_expr}':b='{lut_expr}'")

    # 2. W3C CSS saturate(s_eff) (also handles grayscale(1) when s_eff == 0.0)
    if abs(s_eff - 1.0) > 0.001:
        lum_r, lum_g, lum_b = 0.2126, 0.7152, 0.0722
        filters.append(
            "colorchannelmixer="
            f"rr={lum_r + (1.0 - lum_r) * s_eff:.5f}:rg={lum_g - lum_g * s_eff:.5f}:rb={lum_b - lum_b * s_eff:.5f}:"
            f"gr={lum_r - lum_r * s_eff:.5f}:gg={lum_g + (1.0 - lum_g) * s_eff:.5f}:gb={lum_b - lum_b * s_eff:.5f}:"
            f"br={lum_r - lum_r * s_eff:.5f}:bg={lum_g - lum_g * s_eff:.5f}:bb={lum_b + (1.0 - lum_b) * s_eff:.5f}"
        )

    # 3. W3C CSS sepia(sepia_t) for Golden Sunset
    if sepia_t > 0.001:
        t = sepia_t
        filters.append(
            "colorchannelmixer="
            f"rr={1.0 - 0.607 * t:.5f}:rg={0.769 * t:.5f}:rb={0.189 * t:.5f}:"
            f"gr={0.349 * t:.5f}:gg={1.0 - 0.314 * t:.5f}:gb={0.168 * t:.5f}:"
            f"br={0.272 * t:.5f}:bg={0.534 * t:.5f}:bb={1.0 - 0.869 * t:.5f}"
        )

    return ",".join(filters) if filters else "null"


async def _build_smooth_looped_video_to_duration(
    base_visual: Path,
    visual_dur: float,
    target_duration: float,
    job_temp_dir: Path,
    loop_mode: LoopMode,
) -> Path:
    """
    When the provided video's duration (`visual_dur`) is shorter than the song's duration
    (`target_duration`), smoothly replay/loop the video to match `target_duration`:
      - For `CROSSFADE` or `NORMAL` (or long clips > 15s):
        Creates a seamless self-crossfading loop unit (`xfade=transition=fade`) where the
        tail of each replay dissolves smoothly into the head of the next replay with zero jump-cut!
      - For `PINGPONG` (on short clips <= 15s):
        Creates a forward + reverse boomerang unit and repeats it to cover `target_duration`.
    """
    ffmpeg_bin = get_ffmpeg_binary()

    # Option A: Ping-Pong Boomerang (Forward + Reverse) for short clips <= 15s
    if loop_mode == LoopMode.PINGPONG and visual_dur <= 15.0:
        boomerang_unit = job_temp_dir / "boomerang_unit.mp4"
        fc = (
            "[0:v]split=2[fwd][rev_in];"
            "[rev_in]reverse[rev];"
            "[fwd][rev]concat=n=2:v=1:a=0,fps=30,format=yuv420p[vout]"
        )
        proc = await asyncio.create_subprocess_exec(
            ffmpeg_bin,
            "-y",
            "-i",
            str(base_visual.resolve()),
            "-an",
            "-filter_complex",
            fc,
            "-map",
            "[vout]",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "18",
            *BT709_COLOR_FLAGS,
            "-loglevel",
            "error",
            str(boomerang_unit.resolve()),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode == 0 and boomerang_unit.exists():
            unit_dur = await get_media_duration(boomerang_unit)
            repeats = max(2, int(math.ceil((target_duration + 1.0) / max(unit_dur, 0.5))))
            loop_list_txt = job_temp_dir / "boomerang_concat.txt"
            loop_list_txt.write_text(
                "\n".join([f"file '{boomerang_unit.name}'" for _ in range(repeats)]),
                encoding="utf-8",
            )
            full_looped = job_temp_dir / "full_looped_visual.mp4"
            c_proc = await asyncio.create_subprocess_exec(
                ffmpeg_bin,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(loop_list_txt.resolve()),
                "-c",
                "copy",
                "-loglevel",
                "error",
                str(full_looped.resolve()),
                cwd=str(job_temp_dir.resolve()),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await c_proc.communicate()
            if c_proc.returncode == 0 and full_looped.exists():
                return full_looped

    # Option B: Smooth Crossfade Replay (Default & Fallback)
    # Crossfade duration `d`: 0.65s for Crossfade mode, 0.40s for Normal smooth replay
    if visual_dur >= 2.0:
        fade_d = 0.40 if loop_mode == LoopMode.NORMAL else 0.65
        d = round(min(fade_d, visual_dur * 0.20), 2)
        offset = round(visual_dur - 2 * d, 2)

        if d >= 0.20 and offset > 0.20:
            head_intro = job_temp_dir / "loop_head_intro.mp4"
            seamless_unit = job_temp_dir / "loop_seamless_unit.mp4"

            # 1. Extract the very first `d` seconds so Play #1 starts at frame 0.00s
            head_proc = await asyncio.create_subprocess_exec(
                ffmpeg_bin,
                "-y",
                "-i",
                str(base_visual.resolve()),
                "-an",
                "-vf",
                f"trim=start=0:end={d},setpts=PTS-STARTPTS,fps=30,format=yuv420p",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "18",
                *BT709_COLOR_FLAGS,
                "-loglevel",
                "error",
                str(head_intro.resolve()),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await head_proc.communicate()

            # 2. Build `seamless_unit` = BODY (d..D) crossfaded at the end with HEAD (0..d)
            fc = (
                f"[0:v]split=2[v_body_in][v_head_in];"
                f"[v_body_in]trim=start={d}:end={visual_dur:.2f},setpts=PTS-STARTPTS[body];"
                f"[v_head_in]trim=start=0:end={d},setpts=PTS-STARTPTS[head];"
                f"[body][head]xfade=transition=fade:duration={d}:offset={offset},fps=30,format=yuv420p[vout]"
            )
            unit_proc = await asyncio.create_subprocess_exec(
                ffmpeg_bin,
                "-y",
                "-i",
                str(base_visual.resolve()),
                "-an",
                "-filter_complex",
                fc,
                "-map",
                "[vout]",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "18",
                *BT709_COLOR_FLAGS,
                "-loglevel",
                "error",
                str(seamless_unit.resolve()),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await unit_proc.communicate()

            if head_proc.returncode == 0 and unit_proc.returncode == 0 and seamless_unit.exists():
                unit_dur = await get_media_duration(seamless_unit)
                repeats = max(2, int(math.ceil((target_duration + 1.5) / max(unit_dur, 0.5))))
                entries = [f"file '{head_intro.name}'"] + [
                    f"file '{seamless_unit.name}'" for _ in range(repeats)
                ]
                loop_list_txt = job_temp_dir / "smooth_loop_concat.txt"
                loop_list_txt.write_text("\n".join(entries), encoding="utf-8")

                full_looped = job_temp_dir / "full_looped_visual.mp4"
                c_proc = await asyncio.create_subprocess_exec(
                    ffmpeg_bin,
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(loop_list_txt.resolve()),
                    "-c",
                    "copy",
                    "-loglevel",
                    "error",
                    str(full_looped.resolve()),
                    cwd=str(job_temp_dir.resolve()),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await c_proc.communicate()
                if c_proc.returncode == 0 and full_looped.exists():
                    looped_dur = await get_media_duration(full_looped)
                    logger.info(
                        "Built smooth crossfade replay video: %.2fs -> %.2fs (song target=%.2fs)",
                        visual_dur,
                        looped_dur,
                        target_duration,
                    )
                    return full_looped

    return base_visual


async def prepare_concatenated_visual_source(
    visual_clips: List[Path],
    job_temp_dir: Path,
    card_w: int,
    card_h: int,
    target_duration: float,
    edit_config: VideoEditConfig,
) -> Path:
    """
    Normalize one or multiple AI-generated animated video clips and, if their combined
    duration is shorter than the song's `target_duration`, smoothly replay/loop them
    to match the song's full duration!
    """
    normalized_clips: List[Path] = []
    pts_factor = round(1.0 / max(0.5, min(edit_config.clip_speed, 2.0)), 4)
    zoom = max(1.0, min(edit_config.video_zoom, 1.6))
    scaled_w = int(round(card_w * zoom))
    scaled_h = int(round(card_h * zoom))
    scaled_w += scaled_w % 2
    scaled_h += scaled_h % 2

    parsed_segments: List[Tuple[float, float]] = []
    if getattr(edit_config, "clip_segments_json", ""):
        import json as _json

        try:
            raw_segs = _json.loads(edit_config.clip_segments_json)
            if isinstance(raw_segs, list):
                for item in raw_segs:
                    s_val = float(item.get("start", 0.0))
                    e_val = float(item.get("end", 0.0))
                    if e_val > s_val + 0.15:
                        parsed_segments.append((max(0.0, s_val), e_val))
        except Exception:
            parsed_segments = []

    for idx, clip_path in enumerate(visual_clips):
        base_steps = [
            f"setpts={pts_factor}*PTS",
            (
                f"scale={scaled_w}:{scaled_h}:force_original_aspect_ratio=increase:"
                "flags=lanczos+accurate_rnd+full_chroma_int:"
                "in_color_matrix=auto:in_range=auto:out_color_matrix=bt709:out_range=tv"
            ),
            f"crop={card_w}:{card_h}",
        ]
        if edit_config.mirror_flip:
            base_steps.append("hflip")
        base_steps.extend(["fps=30", "format=yuv420p"])
        base_vf = ",".join(base_steps)

        if idx == 0 and parsed_segments:
            slice_specs = [
                (seg_idx, s_start, max(0.2, s_end - s_start))
                for seg_idx, (s_start, s_end) in enumerate(parsed_segments)
            ]
        else:
            slice_specs = [
                (0, edit_config.clip_trim_start, edit_config.clip_trim_end)
            ]

        for seg_idx, s_start, s_dur in slice_specs:
            norm_path = job_temp_dir / f"norm_clip_{idx:02d}_seg_{seg_idx:02d}.mp4"
            trim_args: List[str] = []
            if s_start > 0:
                trim_args.extend(["-ss", f"{s_start:.2f}"])
            if s_dur > 0:
                trim_args.extend(["-t", f"{s_dur:.2f}"])

            cmd = [
                get_ffmpeg_binary(),
                "-y",
                *trim_args,
                "-i",
                str(clip_path.resolve()),
                "-an",
                "-vf",
                base_vf,
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "18",
                *BT709_COLOR_FLAGS,
                "-loglevel",
                "error",
                str(norm_path.resolve()),
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(
                    f"Failed to normalize AI clip {clip_path.name}: {stderr.decode('utf-8', errors='replace')}"
                )
            normalized_clips.append(norm_path)

    if len(normalized_clips) == 1:
        base_visual = normalized_clips[0]
    else:
        concat_list_path = job_temp_dir / "concat_list.txt"
        concat_lines = [f"file '{p.name}'" for p in normalized_clips]
        concat_list_path.write_text("\n".join(concat_lines), encoding="utf-8")

        stitched_path = job_temp_dir / "stitched_visuals.mp4"
        concat_cmd = [
            get_ffmpeg_binary(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list_path.resolve()),
            "-c",
            "copy",
            "-loglevel",
            "error",
            str(stitched_path.resolve()),
        ]
        proc = await asyncio.create_subprocess_exec(
            *concat_cmd,
            cwd=str(job_temp_dir.resolve()),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"Failed to stitch AI clips: {stderr.decode('utf-8', errors='replace')}"
            )
        base_visual = stitched_path

    # Check if the visual video is shorter than the song's target_duration
    visual_dur = await get_media_duration(base_visual)
    if visual_dur < target_duration - 0.15:
        logger.info(
            "Provided video duration (%.2fs) is shorter than song duration (%.2fs); "
            "smoothly replaying video to match song duration (mode=%s)...",
            visual_dur,
            target_duration,
            edit_config.loop_mode,
        )
        return await _build_smooth_looped_video_to_duration(
            base_visual=base_visual,
            visual_dur=visual_dur,
            target_duration=target_duration,
            job_temp_dir=job_temp_dir,
            loop_mode=edit_config.loop_mode,
        )

    return base_visual


async def render_aesthetic_lyrics_video(
    visual_clips: List[Path],
    song_audio_path: Path,
    ass_subtitle_path: Path,
    output_video_path: Path,
    job_temp_dir: Path,
    target_duration: float,
    edit_config: VideoEditConfig,
) -> Path:
    """
    Step D: Complete Aesthetic TikTok Video Editor & Renderer (with Smooth Video Replay to Song Duration).
    """
    mask_png_path = job_temp_dir / "rounded_mask.png"
    _, card_w, card_h, pad_x, pad_y = await asyncio.to_thread(
        create_rounded_card_mask_png,
        output_png_path=mask_png_path,
        frame_layout=edit_config.frame_layout,
        corner_radius=edit_config.corner_radius,
    )

    wm_overlay_path = job_temp_dir / "watermark_overlay.png"
    wm_overlay_info = await asyncio.to_thread(
        create_watermark_overlay_png,
        output_png_path=wm_overlay_path,
        watermark_text=edit_config.watermark_text,
        frame_layout=edit_config.frame_layout,
        card_w=card_w,
        card_h=card_h,
        pad_x=pad_x,
        pad_y=pad_y,
        watermark_size=edit_config.watermark_size,
        watermark_pos_x_percent=edit_config.watermark_pos_x_percent,
        watermark_pos_y_percent=edit_config.watermark_pos_y_percent,
    )

    vignette_png_path: Optional[Path] = None
    if edit_config.vignette:
        vignette_png_path = job_temp_dir / "vignette_card.png"
        vignette_img = await asyncio.to_thread(_build_css_vignette_layer, card_w, card_h)
        await asyncio.to_thread(vignette_img.save, vignette_png_path, "PNG")

    stitched_visual = await prepare_concatenated_visual_source(
        visual_clips=visual_clips,
        job_temp_dir=job_temp_dir,
        card_w=card_w,
        card_h=card_h,
        target_duration=target_duration,
        edit_config=edit_config,
    )

    local_ass_name = "subtitles.ass"
    local_ass_path = job_temp_dir / local_ass_name
    if ass_subtitle_path.resolve() != local_ass_path.resolve():
        shutil.copyfile(ass_subtitle_path, local_ass_path)

    local_fonts_dir = job_temp_dir / "fonts"
    local_fonts_dir.mkdir(parents=True, exist_ok=True)
    if FONTS_DIR.exists():
        for fpath in FONTS_DIR.iterdir():
            if fpath.is_file() and fpath.suffix.lower() in (".ttf", ".otf"):
                shutil.copyfile(fpath, local_fonts_dir / fpath.name)

    # Guarantee continuous 30fps timestamps and convert cleanly into full-range sRGB (`rgb24`)
    # so all color grading, radial vignette, watermark & mask compositing match browser CSS 1:1.
    v_filters: List[str] = [
        "fps=30",
        "setpts=N/(30*TB)",
        (
            "scale=in_color_matrix=auto:in_range=auto:"
            "out_color_matrix=bt709:out_range=full:"
            "flags=accurate_rnd+full_chroma_int"
        ),
        "format=rgb24",
    ]

    # 1. CSS-Parity RGB Color Grade + Fine Adjustments (Brightness, Contrast, Saturation)
    color_filter = _get_color_grade_filter(edit_config)
    if color_filter != "null":
        v_filters.append(color_filter)

    # 2. Periodic Blur-Pulse Transition
    if edit_config.blur_transition_interval > 0:
        interval = round(edit_config.blur_transition_interval, 2)
        v_filters.append(
            f"boxblur=luma_radius=6:luma_power=1:enable='lt(mod(t,{interval}),0.42)*gt(t,1.0)'"
        )

    card_chain = ",".join(v_filters)

    # Video Fade-in from black at start and Fade-out to black at end (in RGB before BT.709 encode)
    fade_chain = "null"
    if edit_config.fade_in_out and target_duration > 2.5:
        fade_out_start = round(max(target_duration - 1.1, 1.0), 2)
        fade_chain = f"fade=t=in:st=0:d=0.6,fade=t=out:st={fade_out_start}:d=1.1"

    # Audio filter chain (optional smooth audio fade-in / fade-out)
    audio_filter_chain = "anull"
    if edit_config.audio_fade and target_duration > 2.5:
        afade_out_start = round(max(target_duration - 1.2, 1.0), 2)
        audio_filter_chain = f"afade=t=in:st=0:d=0.6,afade=t=out:st={afade_out_start}:d=1.2"

    extra_inputs: List[str] = []
    next_input_idx = 3
    vig_idx: Optional[int] = None
    wm_idx: Optional[int] = None

    if vignette_png_path is not None:
        extra_inputs.extend(["-i", str(vignette_png_path.resolve())])
        vig_idx = next_input_idx
        next_input_idx += 1

    if wm_overlay_info is not None:
        wm_png_resolved, wm_x, wm_y = wm_overlay_info
        extra_inputs.extend(["-i", str(wm_png_resolved.resolve())])
        wm_idx = next_input_idx
        next_input_idx += 1

    graph_parts: List[str] = [f"[0:v]{card_chain}[card_raw]"]
    current_card_label = "[card_raw]"

    if vig_idx is not None:
        graph_parts.append(f"{current_card_label}[{vig_idx}:v]overlay=0:0:format=rgb[card_vig]")
        current_card_label = "[card_vig]"

    graph_parts.append(
        f"{current_card_label}pad={CANVAS_W}:{CANVAS_H}:{pad_x}:{pad_y}:black[padded_raw]"
    )
    current_canvas_label = "[padded_raw]"

    if wm_idx is not None and wm_overlay_info is not None:
        _, wm_x, wm_y = wm_overlay_info
        graph_parts.append(
            f"{current_canvas_label}[{wm_idx}:v]overlay={wm_x}:{wm_y}:format=rgb[canvas_wm]"
        )
        current_canvas_label = "[canvas_wm]"

    fade_prefix = f"{fade_chain}," if fade_chain != "null" else ""
    graph_parts.append(
        f"{current_canvas_label}[1:v]overlay=0:0:format=rgb,{fade_prefix}"
        "scale=in_color_matrix=bt709:in_range=full:out_color_matrix=bt709:out_range=tv:"
        "flags=accurate_rnd+full_chroma_int,format=yuv420p,"
        f"ass={local_ass_name}:fontsdir=fonts,format=yuv420p[vout]"
    )
    graph_parts.append(f"[2:a]{audio_filter_chain}[aout]")
    filter_complex = ";".join(graph_parts)

    cmd = [
        get_ffmpeg_binary(),
        "-y",
        "-stream_loop",
        "-1",
        "-i",
        str(stitched_visual.resolve()),
        "-i",
        str(mask_png_path.resolve()),
        "-i",
        str(song_audio_path.resolve()),
        *extra_inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-t",
        f"{target_duration:.2f}",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        *BT709_COLOR_FLAGS,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        "-shortest",
        "-loglevel",
        "error",
        str(output_video_path.resolve()),
    ]

    logger.info("Running FFmpeg aesthetic editor pipeline: %s", " ".join(cmd))
    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(job_temp_dir.resolve()),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()

    if process.returncode != 0:
        err_msg = stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"FFmpeg video editor rendering failed: {err_msg}")

    logger.info("Successfully rendered aesthetic TikTok lyrics video to %s", output_video_path)
    return output_video_path


def cleanup_temp_directory(job_temp_dir: Path) -> None:
    """Safely remove all temporary files (.wav, raw video, .ass, .srt, masks) for a finished job."""
    try:
        if job_temp_dir.exists() and job_temp_dir.is_dir():
            shutil.rmtree(job_temp_dir, ignore_errors=True)
            logger.info("Cleaned up temporary job folder: %s", job_temp_dir)
    except Exception as exc:
        logger.warning("Failed to clean up temp folder %s: %s", job_temp_dir, exc)
