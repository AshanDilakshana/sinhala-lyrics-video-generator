from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class LyricsSourceTier(str, Enum):
    TIER_1_LRCLIB = "tier_1_lrclib"
    TIER_2_SPOTIFY = "tier_2_spotify"
    TIER_3_ALIGNMENT = "tier_3_forced_alignment"


class StylePreset(str, Enum):
    RW_AESTHETIC_SINHALA = "rw_aesthetic_sinhala"
    SOFT_LINE_FADE = "soft_line_fade"
    TIKTOK_YELLOW = "tiktok_yellow"
    CAPCUT_WHITE_POP = "capcut_white_pop"
    NEON_CYAN = "neon_cyan"


class FrameLayout(str, Enum):
    ROUNDED_SQUARE_CARD = "rounded_square_card"
    ROUNDED_PORTRAIT_CARD = "rounded_portrait_card"
    FULLSCREEN_VERTICAL = "fullscreen_vertical"


class ColorGradePreset(str, Enum):
    ORIGINAL = "original"
    GOLDEN_SUNSET = "golden_sunset"
    MOODY_CINEMA = "moody_cinema"
    DREAMY_PASTEL = "dreamy_pastel"
    BLACK_AND_WHITE = "black_and_white"


class LoopMode(str, Enum):
    CROSSFADE = "crossfade"
    NORMAL = "normal"
    PINGPONG = "pingpong"


class WordTiming(BaseModel):
    """Acoustic timing for a single word within a lyric line."""
    word: str
    start_ms: int = Field(..., description="Word fade-in start offset in ms relative to line start")
    end_ms: int = Field(..., description="Word fade-in completion offset in ms relative to line start")


class LyricLine(BaseModel):
    """Represents a single time-synced lyric line (with optional per-word acoustic timings)."""
    start: float = Field(..., description="Start time in seconds")
    end: float = Field(..., description="End time in seconds")
    text: str = Field(..., description="Lyric text content (UTF-8 / Sinhala supported)")
    words: Optional[List[WordTiming]] = Field(
        default=None,
        description="Optional per-word acoustic onset timings relative to line.start",
    )


class VideoEditConfig(BaseModel):
    """Complete Video Editing & Custom Typography Configuration."""
    # Framing & Color Preset
    frame_layout: FrameLayout = FrameLayout.ROUNDED_SQUARE_CARD
    color_grade: ColorGradePreset = ColorGradePreset.GOLDEN_SUNSET
    style_preset: StylePreset = StylePreset.RW_AESTHETIC_SINHALA

    # Custom Sinhala / Font Typography Controls
    font_family: str = Field(default="Sinhala Sangam MN", description="Font family name (system or custom TTF/OTF)")
    font_size: int = Field(default=40, ge=16, le=110)
    font_thickness: float = Field(default=0.0, ge=0.0, le=4.0, description="Extra stroke thickness (0 = original font weight)")
    font_brightness: float = Field(default=1.35, ge=0.4, le=2.0, description="Font brightness & luminance glow multiplier (1.0 = normal, >1.0 = bright glow boost)")
    primary_color_hex: str = Field(default="#FFFFFF", description="Hex text color e.g. #FFFFFF")
    outline_width: float = Field(default=1.8, ge=0.0, le=10.0)
    shadow_depth: float = Field(default=2.8, ge=0.0, le=10.0)
    text_pos_y_percent: int = Field(default=50, ge=15, le=85, description="Vertical lyrics position % (50 = center)")
    bold_text: bool = Field(default=True)
    italic_text: bool = Field(default=False)
    lyrics_offset_sec: float = Field(
        default=0.0,
        ge=-10.0,
        le=10.0,
        description="Global timing shift in seconds applied to all lyric lines",
    )

    # Simple Video Editing Tools (Trim, Loop, Zoom, Mirror, Fine Color Adjustments)
    clip_trim_start: float = Field(default=0.0, ge=0.0, description="Trim start of AI clip in seconds")
    clip_trim_end: float = Field(default=0.0, ge=0.0, description="Max duration per AI clip (0 = full clip)")
    clip_segments_json: str = Field(
        default="",
        description="Optional JSON array of kept video blade segments [{'start': float, 'end': float}]",
    )
    loop_mode: LoopMode = Field(
        default=LoopMode.CROSSFADE,
        description="Smooth Crossfade loop, Normal loop, or Ping-Pong (Boomerang) loop to match song duration",
    )
    video_zoom: float = Field(default=1.0, ge=1.0, le=1.6, description="Zoom factor (useful to crop out AI watermarks)")
    mirror_flip: bool = Field(default=False, description="Flip video horizontally (Mirror)")
    brightness: float = Field(default=-0.3, ge=-0.6, le=0.6, description="Video-only brightness adjustment (does not affect captions)")
    contrast: float = Field(default=1.0, ge=0.5, le=1.8)
    saturation: float = Field(default=1.0, ge=0.0, le=2.2)
    vignette: bool = Field(default=False, description="Apply cinematic dark vignette around frame")

    # Transitions, Speed & Audio Tools
    blur_transition_interval: float = Field(default=0.0, ge=0.0, le=30.0)
    clip_speed: float = Field(default=1.0, ge=0.5, le=2.0)
    corner_radius: int = Field(default=48, ge=0, le=160)
    watermark_text: str = Field(default="The_Black Hart 🖤")
    watermark_size: int = Field(default=15, ge=8, le=48)
    watermark_pos_x_percent: int = Field(default=50, ge=5, le=95)
    watermark_pos_y_percent: int = Field(default=98, ge=4, le=99)
    audio_start_sec: float = Field(default=0.0, ge=0.0)
    audio_max_duration_sec: float = Field(default=0.0, ge=0.0)
    fade_in_out: bool = Field(default=True)
    audio_fade: bool = Field(default=True, description="Smoothly fade song audio in at start and out at end")


class LyricsResult(BaseModel):
    """Unified output from the 3-tier Lyrics & Timing Engine."""
    tier: LyricsSourceTier
    lines: List[LyricLine]
    raw_lrc: str
    full_lyrics_text: Optional[str] = None
    detected_title: Optional[str] = None
    detected_artist: Optional[str] = None
    lyrics_source: Optional[str] = None


class JobStage(str, Enum):
    QUEUED = "queued"
    EXTRACTING_MEDIA = "extracting_media"
    FETCHING_LYRICS = "fetching_lyrics"
    ALIGNING_AUDIO = "aligning_audio"
    GENERATING_SUBTITLES = "generating_subtitles"
    RENDERING_VIDEO = "rendering_video"
    COMPLETED = "completed"
    FAILED = "failed"


class JobStatusResponse(BaseModel):
    job_id: str
    stage: JobStage
    progress: int = Field(..., ge=0, le=100)
    message: str
    is_draft: bool = False
    output_filename: Optional[str] = None
    promoted_from_draft: Optional[str] = None
    lyrics_tier: Optional[LyricsSourceTier] = None
    detected_title: Optional[str] = None
    detected_artist: Optional[str] = None
    lyrics_source: Optional[str] = None
    video_url: Optional[str] = None
    lrc_content: Optional[str] = None
    lyrics_preview: Optional[List[LyricLine]] = None
    song_duration: Optional[float] = None
    error: Optional[str] = None
