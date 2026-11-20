"use client";

import React, { useState, useEffect, useRef } from "react";
import {
  PROJECT_STORAGE_KEY,
  clearPersistedProject,
  loadMediaFilesFromIDB,
  saveMediaFilesToIDB,
} from "../lib/projectPersistence";
import {
  AlertCircle,
  Check,
  Clock,
  Download,
  Expand,
  Film,
  FolderOpen,
  Globe,
  Keyboard,
  Maximize2,
  MessageSquareQuote,
  Minimize2,
  Music,
  Palette,
  Pause,
  Play,
  Plus,
  Redo2,
  Rocket,
  RotateCcw,
  Scissors,
  Search,
  ShieldCheck,
  Smartphone,
  Sparkles,
  Star,
  Trash2,
  Type,
  Undo2,
  Upload,
  Video,
  Wand2,
  Zap,
} from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const STARRED_FONTS_STORAGE_KEY = "tiktok_studio_starred_fonts_v1";

type SongSourceMode = "url" | "upload";
type SidebarTab = "media" | "audio" | "captions" | "fonts" | "effects" | "export" | "videos";
type TimelineViewMode = "visual" | "list";
type StylePreset =
  | "rw_aesthetic_sinhala"
  | "soft_line_fade"
  | "tiktok_yellow"
  | "capcut_white_pop"
  | "neon_cyan";
type FrameLayout =
  | "rounded_square_card"
  | "rounded_portrait_card"
  | "fullscreen_vertical";
type ColorGradePreset =
  | "golden_sunset"
  | "moody_cinema"
  | "dreamy_pastel"
  | "black_and_white"
  | "original";
type LoopMode = "crossfade" | "pingpong" | "normal";

interface FontEntry {
  family: string;
  ass_family?: string;
  subfamily?: string;
  label: string;
  filename: string;
  style_tag?: string;
  encoding?: string;
  supported_codepoints?: number[];
  sample_si?: string;
  url?: string;
  regular_url?: string;
  bold_url?: string;
  added_at?: number;
  is_new?: boolean;
  source?: string;
}

const FM_VOWELS: Record<string, string> = {
  "\u0d85": "w", // අ
  "\u0d86": "wd", // ආ
  "\u0d87": "we", // ඇ
  "\u0d88": "wE", // ඈ
  "\u0d89": "b", // ඉ
  "\u0d8a": "B", // ඊ
  "\u0d8b": "W", // උ
  "\u0d8c": "W+", // ඌ
  "\u0d8d": "R", // ඍ
  "\u0d8e": "RR", // ඎ
  "\u0d8f": ",", // ඏ
  "\u0d90": ",=", // ඐ
  "\u0d91": "t", // එ
  "\u0d92": "ta", // ඒ
  "\u0d93": "ft", // ඓ
  "\u0d94": "T", // ඔ
  "\u0d95": "Ta", // ඕ
  "\u0d96": "TD", // ඖ
};

const FM_CONSONANTS: Record<string, string> = {
  "\u0d9a": "l", // ක
  "\u0d9b": "L", // ඛ
  "\u0d9c": ".", // ග
  "\u0d9d": ">", // ඝ
  "\u0d9e": "*", // ඞ
  "\u0d9f": "\u00d5", // ඟ
  "\u0da0": "p", // ච
  "\u0da1": "P", // ඡ
  "\u0da2": "c", // ජ
  "\u0da3": "J", // ඣ
  "\u0da4": "[", // ඤ
  "\u0da5": "{", // ඥ
  "\u0da6": "C", // ඦ
  "\u0da7": "g", // ට
  "\u0da8": "G", // ඨ
  "\u0da9": "v", // ඩ
  "\u0daa": "V", // ඪ
  "\u0dab": "K", // ණ
  "\u0dac": "~", // ඬ
  "\u0dad": ";", // ත
  "\u0dae": "F", // ථ
  "\u0daf": "o", // ද
  "\u0db0": "O", // ධ
  "\u0db1": "k", // න
  "\u0db3": "|", // ඳ
  "\u0db4": "m", // ප
  "\u0db5": "M", // ඵ
  "\u0db6": "n", // බ
  "\u0db7": "N", // භ
  "\u0db8": "u", // ම
  "\u0db9": "U", // ඹ
  "\u0dba": "h", // ය
  "\u0dbb": "r", // ර
  "\u0dbd": ",", // ල
  "\u0dc0": "j", // ව
  "\u0dc1": "Y", // ශ
  "\u0dc2": "I", // ෂ
  "\u0dc3": "i", // ස
  "\u0dc4": "y", // හ
  "\u0dc5": "<", // ළ
  "\u0dc6": "f*", // ෆ
};

const FM_VOWEL_SIGNS = new Set([
  "\u0dcf", "\u0dd0", "\u0dd1", "\u0dd2", "\u0dd3",
  "\u0dd4", "\u0dd6", "\u0dd8", "\u0df2", "\u0dd9",
  "\u0dda", "\u0ddb", "\u0ddc", "\u0ddd", "\u0dde",
]);

const FM_PRECOMPOSED_LIGATURES: Record<string, number> = {
  "\u0d9a\u0dd2": 0xa2, // කි
  "\u0d9a\u0dd3": 0xa3, // කී
  "\u0dbb\u0dd4": 0x2f, // රු
  "\u0dbb\u0dd6": 0x3f, // රූ
  "\u0dc5\u0dd4": 0xa8, // ළු
  "\u0daf\u0dd4": 0xf8, // දු
  "\u0daf\u0dd6": 0xde, // දූ
  "\u0db8\u0dd2": 0xc7, // මි
  "\u0db8\u0dd3": 0xda, // මී
  "\u0dc0\u0dd2": 0xc9, // වි
  "\u0dc0\u0dd3": 0xca, // වී
  "\u0db3\u0dd4": 0x5c, // ඳු
};

/**
 * Convert Sinhala Unicode text (U+0D80..U+0DFF) into Wijesekara / FM ASCII
 * glyph codes matching backend `unicode_to_wijesekara_fm`.
 */
function unicodeToWijesekaraFm(
  text: string,
  supportedCodepoints?: number[]
): string {
  if (!text || !/[\u0d80-\u0dff]/.test(text)) {
    return text;
  }
  const cpSet =
    supportedCodepoints && supportedCodepoints.length > 0
      ? new Set(supportedCodepoints)
      : null;
  const has = (cp: number) => cpSet === null || cpSet.has(cp);

  const out: string[] = [];
  let i = 0;
  const n = text.length;
  while (i < n) {
    let ch = text[i];
    if (ch in FM_VOWELS) {
      out.push(FM_VOWELS[ch]);
      i += 1;
      continue;
    }
    if (ch === "\u0d82") {
      out.push("x");
      i += 1;
      continue;
    }
    if (ch === "\u0d83") {
      out.push("#");
      i += 1;
      continue;
    }
    if (ch === "\u0df4") {
      out.push(".");
      i += 1;
      continue;
    }

    if (ch in FM_CONSONANTS) {
      let repha = false;
      if (
        ch === "\u0dbb" &&
        i + 3 < n &&
        text[i + 1] === "\u0dca" &&
        text[i + 2] === "\u200d" &&
        text[i + 3] in FM_CONSONANTS
      ) {
        repha = true;
        i += 3;
        ch = text[i];
      }

      let base = FM_CONSONANTS[ch];
      if (base === "\u00d5" && !has(0xd5)) {
        base = ".";
      } else if (base === "~" && !has(0x7e)) {
        base = "v";
      }

      if (
        ch === "\u0d9a" &&
        i + 3 < n &&
        text[i + 1] === "\u0dca" &&
        text[i + 2] === "\u200d" &&
        text[i + 3] === "\u0dc2" &&
        has(0xcb)
      ) {
        base = "\u00cb";
        i += 3;
      }

      let medial = "";
      if (i + 3 < n && text[i + 1] === "\u0dca" && text[i + 2] === "\u200d") {
        const nxt = text[i + 3];
        if (nxt === "\u0dba") {
          medial = "H";
          i += 3;
        } else if (nxt === "\u0dbb") {
          medial = "%";
          i += 3;
        }
      }

      const sign = i + 1 < n ? text[i + 1] : "";
      const prefix = repha ? "`" : "";

      if (sign === "\u0dca") {
        out.push(prefix + base + medial + "a");
        i += 2;
        continue;
      }

      if (!FM_VOWEL_SIGNS.has(sign)) {
        out.push(prefix + base + medial);
        i += 1;
        continue;
      }

      const ligKey = ch + sign;
      if (!medial && ligKey in FM_PRECOMPOSED_LIGATURES) {
        const ligCp = FM_PRECOMPOSED_LIGATURES[ligKey];
        if (has(ligCp)) {
          out.push(prefix + String.fromCharCode(ligCp));
          i += 2;
          continue;
        }
      }

      if (sign === "\u0dcf") {
        out.push(prefix + base + medial + "d");
      } else if (sign === "\u0dd0") {
        out.push(prefix + base + medial + "e");
      } else if (sign === "\u0dd1") {
        out.push(prefix + base + medial + "E");
      } else if (sign === "\u0dd2") {
        out.push(prefix + base + medial + "s");
      } else if (sign === "\u0dd3") {
        out.push(prefix + base + medial + "S");
      } else if (sign === "\u0dd4") {
        out.push(prefix + base + medial + "q");
      } else if (sign === "\u0dd6") {
        out.push(prefix + base + medial + "Q");
      } else if (sign === "\u0dd8") {
        out.push(prefix + base + medial + "D");
      } else if (sign === "\u0df2") {
        out.push(prefix + base + medial + "DD");
      } else if (sign === "\u0dd9") {
        out.push("f" + prefix + base + medial);
      } else if (sign === "\u0dda") {
        out.push("f" + prefix + base + medial + "a");
      } else if (sign === "\u0ddb") {
        out.push("ff" + prefix + base + medial);
      } else if (sign === "\u0ddc") {
        out.push("f" + prefix + base + medial + "d");
      } else if (sign === "\u0ddd") {
        out.push("f" + prefix + base + medial + "da");
      } else if (sign === "\u0dde") {
        out.push("f" + prefix + base + medial + "D");
      }
      i += 2;
      continue;
    }

    if (ch !== "\u200d" && ch !== "\u200c") {
      out.push(ch);
    }
    i += 1;
  }

  return out.join("");
}

function formatTextForFontEntry(text: string, font?: FontEntry | null): string {
  if (!text || !font || font.encoding !== "wijesekara_fm") {
    return text;
  }
  return unicodeToWijesekaraFm(text, font.supported_codepoints);
}

interface RenderedVideoItem {
  id: string;
  filename: string;
  job_id: string;
  location: "downloads" | "outputs" | "draft";
  is_draft?: boolean;
  has_project_state?: boolean;
  size_mb: number;
  created_at: number;
  created_label: string;
  saved_in_downloads: boolean;
  downloads_path: string;
  stream_url: string;
  download_url: string;
}

function getCssFontFamily(family: string): string {
  const clean = (family || "Abhaya Libre Bold").trim();
  const low = clean.toLowerCase();
  if (low === "georgia") {
    return `"Georgia", "Sinhala MN", "Iskoola Pota", "Nirmala UI", serif`;
  }
  if (low === "arial") {
    return `"Arial", "Sinhala Sangam MN", "Nirmala UI", "Iskoola Pota", sans-serif`;
  }
  return `"${clean}", "Sinhala Sangam MN", "Nirmala UI", sans-serif`;
}

interface OnlineCatalogFont {
  id: string;
  label: string;
  family: string;
  ass_family?: string;
  filename: string;
  style_tag?: string;
  sample_si?: string;
  url: string;
}

interface WordTiming {
  word: string;
  start_ms: number;
  end_ms: number;
}

interface LyricLine {
  start: number;
  end: number;
  text: string;
  words?: WordTiming[];
}

interface JobStatusResponse {
  job_id: string;
  stage:
  | "queued"
  | "extracting_media"
  | "fetching_lyrics"
  | "aligning_audio"
  | "generating_subtitles"
  | "rendering_video"
  | "completed"
  | "failed";
  progress: number;
  message: string;
  is_draft?: boolean;
  output_filename?: string | null;
  promoted_from_draft?: string | null;
  lyrics_tier?: "tier_1_lrclib" | "tier_2_spotify" | "tier_3_forced_alignment";
  detected_title?: string;
  detected_artist?: string;
  lyrics_source?: string;
  video_url?: string;
  lrc_content?: string;
  lyrics_preview?: LyricLine[];
  song_duration?: number;
  error?: string;
}

const REFERENCE_SINHALA_LYRICS = `මගේ අභියස ලෝකයක් තියා
වටිනම දෙය තෝරගන් කියා
කිව්වෝතින් එක පාරින්
නුඹ වෙත එනවා
මගේ හදවත මාලයක් තනා
නුඹේ හදවතෙහි ලඟින් තියා
හැමදාටම
පලඳන්නෙමි මා
සැරසී දෑතේ
මෙලෙසින් එහා
හැමදේදී ලඟින්
ඉන්නවා`;

const PIPELINE_STEPS = [
  {
    id: "extracting_media",
    label: "Step A: AI Visuals & Song Extraction",
    desc: "Download song audio & smoothly loop short AI video clips to song duration",
  },
  {
    id: "fetching_lyrics",
    label: "Step B: Sinhala Vocal-ASR & Breath Sync",
    desc: "Transcribe/align Sinhala vocals (si-LK) to exact singing timestamps",
  },
  {
    id: "generating_subtitles",
    label: "Step C: Custom Font & Word-Fade .ASS",
    desc: "Apply custom Sinhala font, word-by-word fade & @watermark",
  },
  {
    id: "rendering_video",
    label: "Step D: 9:16 Rounded Card & FFmpeg Render",
    desc: "Apply color grade, smooth crossfade replay, blur transitions & burn-in",
  },
];

function formatMmSs(sec: number): string {
  const safe = Math.max(0, Math.round(sec * 10) / 10);
  const mins = Math.floor(safe / 60);
  const rem = safe - mins * 60;
  const wholeSecs = Math.floor(rem);
  const tenths = Math.round((rem - wholeSecs) * 10);
  const base = `${mins}:${String(wholeSecs).padStart(2, "0")}`;
  return tenths > 0 ? `${base}.${tenths}` : base;
}

function parseTimeInputToSeconds(raw: string): number {
  const cleaned = raw.trim();
  if (!cleaned) return 0;
  if (cleaned.includes(":")) {
    const parts = cleaned.split(":").map((p) => parseFloat(p));
    if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
      return Math.max(0, Number((parts[0] * 60 + parts[1]).toFixed(1)));
    }
    if (parts.length === 3 && parts.every((p) => !isNaN(p))) {
      return Math.max(0, Number((parts[0] * 3600 + parts[1] * 60 + parts[2]).toFixed(1)));
    }
  }
  const num = parseFloat(cleaned);
  return isNaN(num) ? 0 : Math.max(0, Number(num.toFixed(1)));
}

function formatTimecode(sec: number): string {
  const safe = Math.max(0, sec || 0);
  const mins = Math.floor(safe / 60);
  const secs = Math.floor(safe % 60);
  const tenths = Math.floor((safe % 1) * 10);
  return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}.${tenths}`;
}

/**
 * Preserve and proportionally rescale per-word timings when a caption's start/end
 * changes, or synthesize smooth progressive word-reveal timings if missing or edited.
 */
function buildWordTimingsForLine(
  text: string,
  startSec: number,
  endSec: number,
  prevLine?: LyricLine
): WordTiming[] {
  const words = text.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return [];

  const newDurMs = Math.max(400, Math.round((endSec - startSec) * 1000));
  const prevWords = prevLine?.words;
  const sameText =
    prevLine &&
    prevLine.text.trim().replace(/\s+/g, " ") === words.join(" ") &&
    Array.isArray(prevWords) &&
    prevWords.length === words.length;

  if (sameText && prevWords) {
    const oldDurMs = Math.max(
      400,
      Math.round(((prevLine?.end ?? endSec) - (prevLine?.start ?? startSec)) * 1000)
    );
    const ratio = newDurMs / oldDurMs;
    return prevWords.map((wt, idx) => {
      const scaledStart = Math.round(wt.start_ms * ratio);
      const scaledEnd = Math.round(wt.end_ms * ratio);
      const clampedStart = Math.max(0, Math.min(scaledStart, Math.max(0, newDurMs - 140)));
      const clampedEnd = Math.max(
        clampedStart + 100,
        Math.min(scaledEnd, Math.max(clampedStart + 100, newDurMs - 60))
      );
      return {
        word: words[idx],
        start_ms: clampedStart,
        end_ms: clampedEnd,
      };
    });
  }

  const fadeOutMs = Math.min(240, Math.floor(newDurMs / 4));
  const maxEndMs = Math.max(250, newDurMs - fadeOutMs);
  const revealSpanMs = Math.min(
    maxEndMs,
    Math.max(Math.round(newDurMs * 0.68), words.length * 140)
  );
  const stepMs = Math.max(80, Math.floor(revealSpanMs / words.length));
  const wordFadeDur = Math.max(Math.min(stepMs + 110, 320), 140);

  return words.map((word, idx) => {
    const tStart = Math.min(idx * stepMs, Math.max(0, maxEndMs - 110));
    const tEnd = Math.max(tStart + 100, Math.min(tStart + wordFadeDur, maxEndMs));
    return { word, start_ms: tStart, end_ms: tEnd };
  });
}

export default function TikTokLyricsVideoEditor() {
  // Navigation & Workspace UI State
  const [activeSidebarTab, setActiveSidebarTab] = useState<SidebarTab>("media");
  const [timelineViewMode, setTimelineViewMode] = useState<TimelineViewMode>("visual");
  const [timelineZoom, setTimelineZoom] = useState<number>(1.5);
  const [selectedCaptionIdx, setSelectedCaptionIdx] = useState<number | null>(null);
  const [isFullscreenModal, setIsFullscreenModal] = useState<boolean>(false);
  const [showTikTokSafeZone, setShowTikTokSafeZone] = useState<boolean>(false);
  const [isDraggingClip, setIsDraggingClip] = useState<boolean>(false);
  const [showShortcutsHelp, setShowShortcutsHelp] = useState<boolean>(false);
  const [studioToast, setStudioToast] = useState<string | null>(null);
  const [waveformBars, setWaveformBars] = useState<number[]>([]);
  const [detectedLyricsSource, setDetectedLyricsSource] = useState<string | null>(null);
  const toastTimerRef = useRef<NodeJS.Timeout | null>(null);
  const previewStageRef = useRef<HTMLDivElement | null>(null);
  const previewCardRef = useRef<HTMLDivElement | null>(null);
  const [previewCardWidth, setPreviewCardWidth] = useState<number>(0);
  const previewVideoRef = useRef<HTMLVideoElement | null>(null);
  const captionsTrackRef = useRef<HTMLDivElement | null>(null);
  const [dragCaptionState, setDragCaptionState] = useState<{
    mode: "move" | "resize-start" | "resize-end";
    idx: number;
    startX: number;
    origStart: number;
    origEnd: number;
    moved: boolean;
  } | null>(null);
  const [isScrubbingRuler, setIsScrubbingRuler] = useState<boolean>(false);

  // 1. Visual Track State (AI Animated Video Clips)
  const [visualClips, setVisualClips] = useState<File[]>([]);
  const [previewClipUrl, setPreviewClipUrl] = useState<string | null>(null);
  const [clipPreviewUrls, setClipPreviewUrls] = useState<string[]>([]);
  const [selectedSingleClipIdx, setSelectedSingleClipIdx] = useState<number | null>(null);
  const [detectedClipDuration, setDetectedClipDuration] = useState<number>(0);

  // 2. Simple Video Editing Tools State
  const [clipSpeed, setClipSpeed] = useState<number>(1.0);
  const [clipTrimStart, setClipTrimStart] = useState<number>(0);
  const [clipTrimEnd, setClipTrimEnd] = useState<number>(0);
  const [videoSegments, setVideoSegments] = useState<{ start: number; end: number }[]>([]);
  const [selectedVideoSegmentIdx, setSelectedVideoSegmentIdx] = useState<number | null>(null);
  const [isVideoBladeMode, setIsVideoBladeMode] = useState<boolean>(false);
  const [loopMode, setLoopMode] = useState<LoopMode>("crossfade");
  const [videoZoom, setVideoZoom] = useState<number>(1.0);
  const [mirrorFlip, setMirrorFlip] = useState<boolean>(false);
  const [brightness, setBrightness] = useState<number>(-0.3);
  const [contrast, setContrast] = useState<number>(1.0);
  const [saturation, setSaturation] = useState<number>(1.0);
  const [vignette, setVignette] = useState<boolean>(false);

  // 3. Song / Audio Track & Live Sync State
  const [songSourceMode, setSongSourceMode] = useState<SongSourceMode>("url");
  const [songUrl, setSongUrl] = useState<string>("");
  const [songFile, setSongFile] = useState<File | null>(null);
  const [audioStartSec, setAudioStartSec] = useState<number>(0);
  const [audioStartText, setAudioStartText] = useState<string>("0:00");
  const [audioMaxDurationSec, setAudioMaxDurationSec] = useState<number>(0);
  const [audioFade, setAudioFade] = useState<boolean>(true);
  const [cachedSongId, setCachedSongId] = useState<string | null>(null);
  const [songPreviewUrl, setSongPreviewUrl] = useState<string | null>(null);
  const [fetchedSongDuration, setFetchedSongDuration] = useState<number | null>(null);
  const [rawSongDuration, setRawSongDuration] = useState<number | null>(null);
  const [appliedAudioStartSec, setAppliedAudioStartSec] = useState<number>(0);
  const [isFetchingSong, setIsFetchingSong] = useState<boolean>(false);
  const fetchSongAbortRef = useRef<AbortController | null>(null);
  const [isTrimmingSong, setIsTrimmingSong] = useState<boolean>(false);
  const [isSyncingLyrics, setIsSyncingLyrics] = useState<boolean>(false);
  const [songFetchMsg, setSongFetchMsg] = useState<string | null>(null);

  // Audio playback & Live Tap-to-Sync State
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [currentAudioTime, setCurrentAudioTime] = useState<number>(0);
  const [isAudioPlaying, setIsAudioPlaying] = useState<boolean>(false);
  const [isTapSyncActive, setIsTapSyncActive] = useState<boolean>(false);
  const [tapLineCursor, setTapLineCursor] = useState<number>(0);
  const [lyricsOffsetSec, setLyricsOffsetSec] = useState<number>(0.0);

  // 4. Lyrics & Metadata State (Defaults to Auto-Caption mode with empty manualLyrics)
  const [captionMode, setCaptionMode] = useState<"auto" | "manual">("auto");
  const [audioTabLyricsView, setAudioTabLyricsView] = useState<"synced" | "text">("synced");
  const [isEditingFullLyricsText, setIsEditingFullLyricsText] = useState<boolean>(false);
  const [songName, setSongName] = useState<string>("");
  const [artistName, setArtistName] = useState<string>("");
  const [manualLyrics, setManualLyrics] = useState<string>("");
  const [language, setLanguage] = useState<string>("si");
  const [editableTimeline, setEditableTimeline] = useState<LyricLine[]>([]);

  // Sri Lankan Lyrics Hub Search State (lyrihub.lk / lyrics-lk.com / Smule slug)
  const [lyricsSearchQuery, setLyricsSearchQuery] = useState<string>("");
  const [isSearchingWebLyrics, setIsSearchingWebLyrics] = useState<boolean>(false);
  const [webLyricsStatus, setWebLyricsStatus] = useState<string | null>(null);
  const [webLyricsCandidates, setWebLyricsCandidates] = useState<
    { source: string; url: string; title: string; artist: string; lyrics_text: string }[]
  >([]);

  // 5. Custom Sinhala Font Manager & Typography Controls
  const [installedFonts, setInstalledFonts] = useState<FontEntry[]>([
    {
      family: "Sinhala Sangam MN",
      label: "Sinhala Sangam MN (The_Black Hart Default)",
      filename: "",
      style_tag: "The_Black Hart Original",
      sample_si: "මගේ අභියස ලෝකයක් තියා",
      url: "/api/fonts/system/sinhala-sangam-mn-regular.ttf",
      regular_url: "/api/fonts/system/sinhala-sangam-mn-regular.ttf",
      bold_url: "/api/fonts/system/sinhala-sangam-mn-bold.ttf",
      source: "system",
    },
    {
      family: "Abhaya Libre Bold",
      label: "Abhaya Libre Bold (අභය බෝල්ඩ් - Classic Aesthetic Serif)",
      filename: "AbhayaLibre-Bold.ttf",
      style_tag: "Bold Serif",
      sample_si: "මගේ අභියස ලෝකයක් තියා",
      url: "/api/fonts/file/AbhayaLibre-Bold.ttf",
      bold_url: "/api/fonts/file/AbhayaLibre-Bold.ttf",
      source: "custom",
    },
    {
      family: "Abhaya Libre Regular",
      label: "Abhaya Libre Regular (අභය - Clean Book Style)",
      filename: "AbhayaLibre-Regular.ttf",
      style_tag: "Book Serif",
      sample_si: "වටිනම දෙය තෝරගන් කියා",
      url: "/api/fonts/file/AbhayaLibre-Regular.ttf",
      regular_url: "/api/fonts/file/AbhayaLibre-Regular.ttf",
      source: "custom",
    },
    {
      family: "Gemunu Libre",
      label: "Gemunu Libre (ගැමුණු - Modern Rounded Sinhala)",
      filename: "GemunuLibre-Bold.ttf",
      style_tag: "Rounded Modern",
      sample_si: "කිව්වෝතින් එක පාරින් නුඹ වෙත එනවා",
      url: "/api/fonts/file/GemunuLibre-Regular.ttf",
      regular_url: "/api/fonts/file/GemunuLibre-Regular.ttf",
      bold_url: "/api/fonts/file/GemunuLibre-Bold.ttf",
      source: "custom",
    },
    {
      family: "Noto Serif Sinhala",
      label: "Noto Serif Sinhala (නොටෝ සෙරිෆ් - Poetic & Lyric Style)",
      filename: "NotoSerifSinhala-Regular.ttf",
      style_tag: "Poetic Serif",
      sample_si: "මගේ හදවත මාලයක් තනා",
      url: "/api/fonts/file/NotoSerifSinhala-Regular.ttf",
      regular_url: "/api/fonts/file/NotoSerifSinhala-Regular.ttf",
      bold_url: "/api/fonts/file/NotoSerifSinhala-Bold.ttf",
      source: "custom",
    },
    {
      family: "Noto Sans Sinhala",
      label: "Noto Sans Sinhala (නොටෝ සෑන්ස් - Clean Modern Sans)",
      filename: "NotoSansSinhala-Regular.ttf",
      style_tag: "Clean Sans",
      sample_si: "නුඹේ හදවතෙහි ලඟින් තියා",
      url: "/api/fonts/file/NotoSansSinhala-Regular.ttf",
      regular_url: "/api/fonts/file/NotoSansSinhala-Regular.ttf",
      bold_url: "/api/fonts/file/NotoSansSinhala-Bold.ttf",
      source: "custom",
    },
    {
      family: "Yaldevi",
      label: "Yaldevi Colombo (යාල්දේවි - Stylish Display Sinhala)",
      filename: "Yaldevi-Bold.ttf",
      style_tag: "Display Wide",
      sample_si: "හැමදාටම පලඳන්නෙමි මා",
      url: "/api/fonts/file/Yaldevi-Regular.ttf",
      regular_url: "/api/fonts/file/Yaldevi-Regular.ttf",
      bold_url: "/api/fonts/file/Yaldevi-Bold.ttf",
      source: "custom",
    },
    {
      family: "Sinhala MN",
      label: "Sinhala MN (Classic Sinhala Serif)",
      filename: "",
      style_tag: "System Serif",
      sample_si: "වටිනම දෙය තෝරගන් කියා",
      url: "/api/fonts/system/sinhala-mn-regular.ttf",
      regular_url: "/api/fonts/system/sinhala-mn-regular.ttf",
      bold_url: "/api/fonts/system/sinhala-mn-bold.ttf",
      source: "system",
    },
    {
      family: "Georgia",
      label: "Georgia Serif (English / Aesthetic)",
      filename: "",
      style_tag: "English Serif",
      sample_si: "Aesthetic Midnight Vibes",
      source: "system",
    },
    {
      family: "Arial",
      label: "Arial Bold Sans (CapCut / TikTok)",
      filename: "",
      style_tag: "Bold Sans",
      sample_si: "TikTok Pop Captions",
      source: "system",
    },
  ]);
  const [onlineFontCatalog, setOnlineFontCatalog] = useState<OnlineCatalogFont[]>([]);
  const [defaultFontFamily, setDefaultFontFamily] = useState<string>("Sinhala Sangam MN");
  const [selectedFontFamily, setSelectedFontFamily] = useState<string>("Sinhala Sangam MN");
  const [fontPreviewSampleText, setFontPreviewSampleText] = useState<string>(
    "මගේ අභියස ලෝකයක් තියා"
  );
  const [fontPreviewAnimKey, setFontPreviewAnimKey] = useState<number>(0);
  const [showServerFontRender, setShowServerFontRender] = useState<boolean>(true);
  const [customFontUrl, setCustomFontUrl] = useState<string>("");
  const [fontStatusMsg, setFontStatusMsg] = useState<string | null>(null);
  const [isLoadingFont, setIsLoadingFont] = useState<boolean>(false);
  const [starredFonts, setStarredFonts] = useState<string[]>([]);
  const [newlyAddedFonts, setNewlyAddedFonts] = useState<string[]>([]);

  // Rendered Videos & Drafts Library State (auto-saved in Downloads/Titok video meker & /draft)
  const [renderedVideos, setRenderedVideos] = useState<RenderedVideoItem[]>([]);
  const [renderedVideosFolder, setRenderedVideosFolder] = useState<string>(
    "~/Downloads/Titok video meker"
  );
  const [draftsFolder, setDraftsFolder] = useState<string>(
    "~/Downloads/Titok video meker/draft"
  );
  const [activeDraftFilename, setActiveDraftFilename] = useState<string | null>(null);
  const [loadingDraftFilename, setLoadingDraftFilename] = useState<string | null>(null);
  const [videoLibraryFilter, setVideoLibraryFilter] = useState<"all" | "final" | "draft">("all");
  const [promotingDraftFilename, setPromotingDraftFilename] = useState<string | null>(null);
  const [isLoadingRenderedVideos, setIsLoadingRenderedVideos] = useState<boolean>(false);
  const [selectedRenderedVideoUrl, setSelectedRenderedVideoUrl] = useState<string | null>(null);
  const [selectedRenderedVideoLabel, setSelectedRenderedVideoLabel] = useState<string | null>(null);
  const [renderedVideoError, setRenderedVideoError] = useState<string | null>(null);
  const renderedVideoRef = useRef<HTMLVideoElement | null>(null);

  // Undo / Redo History State
  const undoPastRef = useRef<string[]>([]);
  const undoFutureRef = useRef<string[]>([]);
  const committedUndoJsonRef = useRef<string | null>(null);
  const isApplyingUndoRedoRef = useRef<boolean>(false);
  const [undoCount, setUndoCount] = useState<number>(0);
  const [redoCount, setRedoCount] = useState<number>(0);

  const [fontSize, setFontSize] = useState<number>(40);
  const [fontThickness, setFontThickness] = useState<number>(0);
  const [fontBrightness, setFontBrightness] = useState<number>(1.35);
  const [selectedStartLineIdx, setSelectedStartLineIdx] = useState<number>(-1);
  const [primaryColorHex, setPrimaryColorHex] = useState<string>("#FFFFFF");
  const [outlineWidth, setOutlineWidth] = useState<number>(1.8);
  const [shadowDepth, setShadowDepth] = useState<number>(2.8);
  const [textPosYPercent, setTextPosYPercent] = useState<number>(50);
  const [boldText, setBoldText] = useState<boolean>(true);
  const [italicText, setItalicText] = useState<boolean>(false);

  // 6. Aesthetic Framing & Transitions
  const [frameLayout, setFrameLayout] = useState<FrameLayout>("rounded_square_card");
  const [colorGrade, setColorGrade] = useState<ColorGradePreset>("golden_sunset");
  const [stylePreset, setStylePreset] = useState<StylePreset>("rw_aesthetic_sinhala");
  const [blurInterval, setBlurInterval] = useState<number>(0);
  const [cornerRadius, setCornerRadius] = useState<number>(48);
  const [watermarkText, setWatermarkText] = useState<string>("The_Black Hart 🖤");
  const [watermarkSize, setWatermarkSize] = useState<number>(15);
  const [watermarkPosXPercent, setWatermarkPosXPercent] = useState<number>(50);
  const [watermarkPosYPercent, setWatermarkPosYPercent] = useState<number>(98);
  const [fadeInOut, setFadeInOut] = useState<boolean>(true);

  // 7. Preview & Render Job State
  const [activePreviewTab, setActivePreviewTab] = useState<"canvas" | "rendered">("canvas");
  const [previewLineIndex, setPreviewLineIndex] = useState<number>(0);
  const [job, setJob] = useState<JobStatusResponse | null>(null);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [uiError, setUiError] = useState<string | null>(null);
  const [isProjectHydrated, setIsProjectHydrated] = useState<boolean>(false);

  const pollTimerRef = useRef<NodeJS.Timeout | null>(null);
  const mediaHydratedRef = useRef<boolean>(false);
  const userModifiedMediaRef = useRef<boolean>(false);
  const latestProjectJsonRef = useRef<string | null>(null);

  const registerBrowserFontFace = async (
    family: string,
    fontUrl: string,
    weightDescriptor: string = "100 900"
  ) => {
    if (typeof window === "undefined" || !("FontFace" in window) || !fontUrl) return;
    try {
      const fullUrl =
        fontUrl.startsWith("http") || fontUrl.startsWith("blob:")
          ? fontUrl
          : `${API_BASE}${fontUrl}`;
      const fontFace = new FontFace(family, `url("${fullUrl}")`, {
        weight: weightDescriptor,
      });
      const loaded = await fontFace.load();
      document.fonts.add(loaded);
    } catch {
      // Fallback handled by injected @font-face <style> tag
    }
  };

  const registerFontEntryFaces = (f: FontEntry) => {
    const regUrl = f.regular_url || "";
    const boldUrl = f.bold_url || "";
    if (regUrl && boldUrl && regUrl !== boldUrl) {
      registerBrowserFontFace(f.family, regUrl, "400");
      registerBrowserFontFace(f.family, boldUrl, "700");
    } else if (regUrl || boldUrl || f.url) {
      registerBrowserFontFace(f.family, (regUrl || boldUrl || f.url)!, "100 900");
    }
  };

  const handleSelectFont = (font: FontEntry) => {
    setSelectedFontFamily(font.family);
    const famLow = font.family.toLowerCase();
    if (famLow.includes("regular") && !famLow.includes("bold")) {
      setBoldText(false);
    } else if (famLow.includes("bold")) {
      setBoldText(true);
    }
    setActivePreviewTab("canvas");
    setFontPreviewAnimKey((prev) => prev + 1);
  };

  const toggleStarFont = (family: string) => {
    setStarredFonts((prev) => {
      const exists = prev.includes(family);
      const next = exists ? prev.filter((f) => f !== family) : [family, ...prev];
      if (typeof window !== "undefined") {
        try {
          window.localStorage.setItem(STARRED_FONTS_STORAGE_KEY, JSON.stringify(next));
        } catch {
          // Ignore storage quota errors
        }
      }
      return next;
    });
  };

  const handleDeleteFont = async (font: FontEntry) => {
    if (font.source === "system") return;
    if (
      typeof window !== "undefined" &&
      !window.confirm(`Delete custom font "${font.family}"?`)
    ) {
      return;
    }
    try {
      const targetId = font.family || font.filename;
      const res = await fetch(`${API_BASE}/api/fonts/${encodeURIComponent(targetId)}`, {
        method: "DELETE",
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.detail || "Failed to delete font");
      }
      if (data.all_fonts?.installed) {
        setInstalledFonts(data.all_fonts.installed);
      } else {
        await refreshFontsFromBackend();
      }
      setNewlyAddedFonts((prev) => prev.filter((f) => f !== font.family));
      setStarredFonts((prev) => {
        const next = prev.filter((f) => f !== font.family);
        if (typeof window !== "undefined") {
          try {
            window.localStorage.setItem(STARRED_FONTS_STORAGE_KEY, JSON.stringify(next));
          } catch {
            // Ignore storage errors
          }
        }
        return next;
      });
      if (selectedFontFamily === font.family) {
        const nextFallback =
          data.all_fonts?.default_font ||
          data.all_fonts?.installed?.[0]?.family ||
          defaultFontFamily;
        setSelectedFontFamily(nextFallback);
      }
      setFontStatusMsg(`Deleted custom font: ${font.family}`);
      showToast(`Deleted font: ${font.family}`);
    } catch (err: any) {
      setFontStatusMsg(`Delete failed: ${err.message}`);
    }
  };

  const fetchRenderedVideos = async () => {
    setIsLoadingRenderedVideos(true);
    try {
      const res = await fetch(`${API_BASE}/api/rendered-videos`);
      if (!res.ok) return;
      const data = await res.json();
      if (Array.isArray(data.videos)) {
        setRenderedVideos(data.videos);
      }
      if (data.folder) {
        setRenderedVideosFolder(data.folder);
      }
      if (data.draft_folder) {
        setDraftsFolder(data.draft_folder);
      }
    } catch {
      // Backend might not be running yet
    } finally {
      setIsLoadingRenderedVideos(false);
    }
  };

  const handlePromoteDraft = async (video: RenderedVideoItem) => {
    setPromotingDraftFilename(video.filename);
    try {
      const res = await fetch(
        `${API_BASE}/api/rendered-videos/draft/${encodeURIComponent(video.filename)}/render`,
        { method: "POST" }
      );
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `Server responded with ${res.status}`);
      }
      const data = await res.json();
      if (activeDraftFilename === video.filename) {
        setActiveDraftFilename(null);
      }
      if (data.stream_url) {
        setSelectedRenderedVideoUrl(data.stream_url);
        setSelectedRenderedVideoLabel(data.filename || video.filename);
      }
      setJob((prev) =>
        prev && prev.output_filename === video.filename
          ? {
            ...prev,
            is_draft: false,
            promoted_from_draft: video.filename,
            output_filename: data.filename || video.filename,
            video_url: data.stream_url || prev.video_url,
            message: `Rendered & moved '${video.filename}' from draft to original folder!`,
          }
          : prev
      );
      await fetchRenderedVideos();
      showToast(`Rendered & moved '${video.filename}' from draft to original folder!`);
    } catch (err: any) {
      showToast(`Failed to render draft: ${err.message || "Unknown error"}`);
    } finally {
      setPromotingDraftFilename(null);
    }
  };

  const handleLoadDraftForReEdit = async (video: RenderedVideoItem) => {
    setLoadingDraftFilename(video.filename);
    try {
      const res = await fetch(
        `${API_BASE}/api/rendered-videos/draft/${encodeURIComponent(video.filename)}/project`
      );
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(
          errData.detail ||
          "Draft project settings not found (re-save the draft once to store its project bundle)."
        );
      }
      const data = await res.json();
      const cfg = data.edit_config || {};

      // Restore visual video clips from the draft bundle
      const rawClipEntries = Array.isArray(data.visual_clip_urls) ? data.visual_clip_urls : [];
      const loadedFiles: File[] = [];
      for (let i = 0; i < rawClipEntries.length; i++) {
        const entry = rawClipEntries[i];
        const relUrl = typeof entry === "string" ? entry : entry?.url;
        const clipFilename =
          (typeof entry === "object" && entry?.filename) || `draft_clip_${i + 1}.mp4`;
        if (!relUrl) continue;
        try {
          const clipRes = await fetch(`${API_BASE}${relUrl}`);
          if (clipRes.ok) {
            const blob = await clipRes.blob();
            loadedFiles.push(
              new File([blob], clipFilename, {
                type: blob.type || "video/mp4",
              })
            );
          }
        } catch {
          // ignore single clip fetch errors
        }
      }
      if (loadedFiles.length > 0) {
        userModifiedMediaRef.current = true;
        setVisualClips(loadedFiles);
      } else {
        userModifiedMediaRef.current = true;
        setVisualClips([]);
      }
      setSelectedSingleClipIdx(null);
      setSelectedStartLineIdx(-1);

      // Restore song & lyrics timeline
      if (typeof data.song_name === "string") setSongName(data.song_name);
      if (typeof data.artist_name === "string") setArtistName(data.artist_name);
      if (typeof data.song_url === "string") {
        setSongUrl(data.song_url);
        setSongSourceMode(data.song_url.trim() ? "url" : "upload");
      }
      if (typeof data.language === "string") setLanguage(data.language);
      if (typeof data.lyrics_source === "string") {
        setDetectedLyricsSource(data.lyrics_source || null);
      }
      if (typeof data.manual_lyrics === "string") {
        setManualLyrics(data.manual_lyrics);
        if (data.manual_lyrics.trim()) {
          setCaptionMode("manual");
        }
      }
      if (Array.isArray(data.aligned_lines) && data.aligned_lines.length > 0) {
        setEditableTimeline(data.aligned_lines);
        setSelectedCaptionIdx(0);
      } else {
        setEditableTimeline([]);
      }
      const restoredDur =
        typeof data.duration === "number" && data.duration > 0
          ? data.duration
          : typeof data.song_duration === "number" && data.song_duration > 0
          ? data.song_duration
          : null;
      if (restoredDur !== null) {
        setFetchedSongDuration(restoredDur);
        setRawSongDuration(restoredDur);
      }
      const previewPath = data.audio_preview_url || data.song_preview_url;
      if (typeof data.cached_song_id === "string" && data.cached_song_id) {
        setCachedSongId(data.cached_song_id);
        setSongPreviewUrl(
          previewPath
            ? `${API_BASE}${previewPath}`
            : `${API_BASE}/api/song-preview/${data.cached_song_id}`
        );
      }

      // Restore all VideoEditConfig settings
      if (typeof cfg.frame_layout === "string") setFrameLayout(cfg.frame_layout as FrameLayout);
      if (typeof cfg.color_grade === "string") setColorGrade(cfg.color_grade as ColorGradePreset);
      if (typeof cfg.style_preset === "string") setStylePreset(cfg.style_preset as StylePreset);
      if (typeof cfg.font_family === "string") setSelectedFontFamily(cfg.font_family);
      if (typeof cfg.font_size === "number") setFontSize(cfg.font_size);
      if (typeof cfg.font_thickness === "number") setFontThickness(cfg.font_thickness);
      if (typeof cfg.font_brightness === "number") setFontBrightness(cfg.font_brightness);
      if (typeof cfg.primary_color_hex === "string") setPrimaryColorHex(cfg.primary_color_hex);
      if (typeof cfg.outline_width === "number") setOutlineWidth(cfg.outline_width);
      if (typeof cfg.shadow_depth === "number") setShadowDepth(cfg.shadow_depth);
      if (typeof cfg.text_pos_y_percent === "number") setTextPosYPercent(cfg.text_pos_y_percent);
      if (typeof cfg.bold_text === "boolean") setBoldText(cfg.bold_text);
      if (typeof cfg.italic_text === "boolean") setItalicText(cfg.italic_text);
      if (typeof cfg.lyrics_offset_sec === "number") setLyricsOffsetSec(cfg.lyrics_offset_sec);
      if (typeof cfg.clip_trim_start === "number") setClipTrimStart(cfg.clip_trim_start);
      if (typeof cfg.clip_trim_end === "number") setClipTrimEnd(cfg.clip_trim_end);
      if (typeof cfg.clip_segments_json === "string" && cfg.clip_segments_json.trim()) {
        try {
          const parsedSegs = JSON.parse(cfg.clip_segments_json);
          if (Array.isArray(parsedSegs)) setVideoSegments(parsedSegs);
        } catch {
          setVideoSegments([]);
        }
      } else {
        setVideoSegments([]);
      }
      if (typeof cfg.loop_mode === "string") setLoopMode(cfg.loop_mode as LoopMode);
      if (typeof cfg.video_zoom === "number") setVideoZoom(cfg.video_zoom);
      if (typeof cfg.mirror_flip === "boolean") setMirrorFlip(cfg.mirror_flip);
      if (typeof cfg.brightness === "number") setBrightness(cfg.brightness);
      if (typeof cfg.contrast === "number") setContrast(cfg.contrast);
      if (typeof cfg.saturation === "number") setSaturation(cfg.saturation);
      if (typeof cfg.vignette === "boolean") setVignette(cfg.vignette);
      if (typeof cfg.blur_transition_interval === "number") {
        setBlurInterval(cfg.blur_transition_interval);
      }
      if (typeof cfg.clip_speed === "number") setClipSpeed(cfg.clip_speed);
      if (typeof cfg.corner_radius === "number") setCornerRadius(cfg.corner_radius);
      if (typeof cfg.watermark_text === "string") setWatermarkText(cfg.watermark_text);
      if (typeof cfg.watermark_size === "number") setWatermarkSize(cfg.watermark_size);
      if (typeof cfg.watermark_pos_x_percent === "number") {
        setWatermarkPosXPercent(cfg.watermark_pos_x_percent);
      }
      if (typeof cfg.watermark_pos_y_percent === "number") {
        setWatermarkPosYPercent(cfg.watermark_pos_y_percent);
      }
      if (typeof cfg.audio_start_sec === "number") {
        setAudioStartSec(cfg.audio_start_sec);
        setAppliedAudioStartSec(cfg.audio_start_sec);
        setAudioStartText(formatMmSs(cfg.audio_start_sec));
      }
      if (typeof cfg.audio_max_duration_sec === "number") {
        setAudioMaxDurationSec(cfg.audio_max_duration_sec);
      }
      if (typeof cfg.fade_in_out === "boolean") setFadeInOut(cfg.fade_in_out);
      if (typeof cfg.audio_fade === "boolean") setAudioFade(cfg.audio_fade);

      undoPastRef.current = [];
      undoFutureRef.current = [];
      committedUndoJsonRef.current = null;
      setUndoCount(0);
      setRedoCount(0);
      setActiveDraftFilename(video.filename);
      setActivePreviewTab("canvas");
      setActiveSidebarTab(loadedFiles.length > 0 ? "captions" : "media");
      showToast(
        loadedFiles.length > 0
          ? `✏️ Loaded draft "${video.filename}" for re-editing! Click "Render Video" when ready to save to Render folder.`
          : `✏️ Loaded draft settings for "${video.filename}"! Please add a video clip in Media tab before rendering.`
      );
    } catch (err: any) {
      showToast(`Could not load draft project: ${err.message || "Unknown error"}`);
    } finally {
      setLoadingDraftFilename(null);
    }
  };

  const handleDeleteRenderedVideo = async (video: RenderedVideoItem) => {
    try {
      const res = await fetch(
        `${API_BASE}/api/rendered-videos/${encodeURIComponent(video.location)}/${encodeURIComponent(
          video.filename
        )}`,
        { method: "DELETE" }
      );
      if (res.ok) {
        if (selectedRenderedVideoUrl === video.stream_url) {
          setSelectedRenderedVideoUrl(null);
          setSelectedRenderedVideoLabel(null);
        }
        if (video.location === "draft" && activeDraftFilename === video.filename) {
          setActiveDraftFilename(null);
        }
        await fetchRenderedVideos();
        showToast(`Deleted ${video.filename}`);
      }
    } catch {
      showToast("Failed to delete video.");
    }
  };

  const refreshFontsFromBackend = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/fonts`);
      if (!res.ok) return;
      const data = await res.json();
      if (data.installed && data.installed.length > 0) {
        const installedList = data.installed as FontEntry[];
        const resolvedDefault =
          (data.default_font as string) || installedList[0].family;
        setDefaultFontFamily(resolvedDefault);
        setInstalledFonts(installedList);
        const installedNames = new Set(installedList.map((f) => f.family));
        setSelectedFontFamily((cur) =>
          installedNames.has(cur) ? cur : resolvedDefault
        );
        const backendNew = installedList
          .filter((f) => f.is_new)
          .map((f) => f.family);
        if (backendNew.length > 0) {
          setNewlyAddedFonts((prev) => Array.from(new Set([...backendNew, ...prev])));
        }
        installedList.forEach((f: FontEntry) => {
          registerFontEntryFaces(f);
        });
      }
      if (data.online_catalog) {
        setOnlineFontCatalog(data.online_catalog);
      }
    } catch {
      // Backend might not be running yet; pre-seeded fonts & @font-face CSS still work
    }
  };

  useEffect(() => {
    if (typeof window !== "undefined") {
      try {
        const rawStars = window.localStorage.getItem(STARRED_FONTS_STORAGE_KEY);
        if (rawStars) {
          const parsed = JSON.parse(rawStars);
          if (Array.isArray(parsed)) setStarredFonts(parsed);
        }
      } catch {
        // Ignore parse error
      }
    }
    installedFonts.forEach((f) => registerFontEntryFaces(f));
    refreshFontsFromBackend();
    fetchRenderedVideos();
  }, []);

  useEffect(() => {
    const el = previewCardRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const updateWidth = () => {
      const w = el.getBoundingClientRect().width;
      if (w > 0) setPreviewCardWidth(w);
    };
    updateWidth();
    const ro = new ResizeObserver(updateWidth);
    ro.observe(el);
    return () => ro.disconnect();
  }, [activePreviewTab, frameLayout, isFullscreenModal]);

  // Restore saved studio workspace across browser refreshes
  useEffect(() => {
    let cancelled = false;
    try {
      const raw =
        typeof window !== "undefined" ? window.localStorage.getItem(PROJECT_STORAGE_KEY) : null;
      if (raw) {
        const s = JSON.parse(raw);
        if (s && typeof s === "object") {
          if (typeof s.activeSidebarTab === "string") setActiveSidebarTab(s.activeSidebarTab);
          if (typeof s.timelineViewMode === "string") setTimelineViewMode(s.timelineViewMode);
          if (typeof s.timelineZoom === "number") setTimelineZoom(s.timelineZoom);
          if (typeof s.selectedCaptionIdx === "number" || s.selectedCaptionIdx === null) {
            setSelectedCaptionIdx(s.selectedCaptionIdx);
          }
          if (typeof s.detectedLyricsSource === "string" || s.detectedLyricsSource === null) {
            setDetectedLyricsSource(s.detectedLyricsSource);
          }
          if (typeof s.clipSpeed === "number") setClipSpeed(s.clipSpeed);
          if (typeof s.clipTrimStart === "number") setClipTrimStart(s.clipTrimStart);
          if (typeof s.clipTrimEnd === "number") setClipTrimEnd(s.clipTrimEnd);
          if (Array.isArray(s.videoSegments)) setVideoSegments(s.videoSegments);
          if (typeof s.loopMode === "string") setLoopMode(s.loopMode);
          if (typeof s.videoZoom === "number") setVideoZoom(s.videoZoom);
          if (typeof s.mirrorFlip === "boolean") setMirrorFlip(s.mirrorFlip);
          if (s.defaultsVersion === 2 && typeof s.brightness === "number") {
            setBrightness(s.brightness);
          } else {
            setBrightness(-0.3);
          }
          if (typeof s.contrast === "number") setContrast(s.contrast);
          if (typeof s.saturation === "number") setSaturation(s.saturation);
          if (typeof s.vignette === "boolean") setVignette(s.vignette);
          if (typeof s.songSourceMode === "string") setSongSourceMode(s.songSourceMode);
          if (typeof s.songUrl === "string") setSongUrl(s.songUrl);
          if (typeof s.audioStartSec === "number") setAudioStartSec(s.audioStartSec);
          if (typeof s.audioStartText === "string") setAudioStartText(s.audioStartText);
          if (typeof s.audioMaxDurationSec === "number") {
            setAudioMaxDurationSec(s.audioMaxDurationSec);
          }
          if (typeof s.audioFade === "boolean") setAudioFade(s.audioFade);
          if (typeof s.fetchedSongDuration === "number" || s.fetchedSongDuration === null) {
            setFetchedSongDuration(s.fetchedSongDuration);
          }
          if (typeof s.rawSongDuration === "number" || s.rawSongDuration === null) {
            setRawSongDuration(s.rawSongDuration);
          }
          if (typeof s.appliedAudioStartSec === "number") {
            setAppliedAudioStartSec(s.appliedAudioStartSec);
          }
          if (typeof s.songFetchMsg === "string" || s.songFetchMsg === null) {
            setSongFetchMsg(s.songFetchMsg);
          }
          if (typeof s.lyricsOffsetSec === "number") setLyricsOffsetSec(s.lyricsOffsetSec);
          if (s.captionMode === "auto" || s.captionMode === "manual") {
            setCaptionMode(s.captionMode);
          }
          if (typeof s.songName === "string") setSongName(s.songName);
          if (typeof s.artistName === "string") setArtistName(s.artistName);
          if (typeof s.manualLyrics === "string") setManualLyrics(s.manualLyrics);
          if (typeof s.language === "string") setLanguage(s.language);
          if (Array.isArray(s.editableTimeline)) setEditableTimeline(s.editableTimeline);
          if (typeof s.lyricsSearchQuery === "string") setLyricsSearchQuery(s.lyricsSearchQuery);
          if (typeof s.selectedFontFamily === "string") setSelectedFontFamily(s.selectedFontFamily);
          if (typeof s.fontPreviewSampleText === "string") {
            setFontPreviewSampleText(s.fontPreviewSampleText);
          }
          if (typeof s.fontSize === "number") setFontSize(s.fontSize);
          if (typeof s.fontThickness === "number") setFontThickness(s.fontThickness);
          if (s.defaultsVersion === 2 && typeof s.fontBrightness === "number") {
            setFontBrightness(s.fontBrightness);
          } else {
            setFontBrightness(1.35);
          }
          if (typeof s.primaryColorHex === "string") setPrimaryColorHex(s.primaryColorHex);
          if (typeof s.outlineWidth === "number") setOutlineWidth(s.outlineWidth);
          if (typeof s.shadowDepth === "number") setShadowDepth(s.shadowDepth);
          if (typeof s.textPosYPercent === "number") setTextPosYPercent(s.textPosYPercent);
          if (typeof s.boldText === "boolean") setBoldText(s.boldText);
          if (typeof s.italicText === "boolean") setItalicText(s.italicText);
          if (typeof s.frameLayout === "string") setFrameLayout(s.frameLayout);
          if (typeof s.colorGrade === "string") setColorGrade(s.colorGrade);
          if (typeof s.stylePreset === "string") setStylePreset(s.stylePreset);
          if (typeof s.blurInterval === "number") setBlurInterval(s.blurInterval);
          if (typeof s.cornerRadius === "number") setCornerRadius(s.cornerRadius);
          if (typeof s.watermarkText === "string") setWatermarkText(s.watermarkText);
          if (typeof s.watermarkSize === "number") setWatermarkSize(s.watermarkSize);
          if (typeof s.watermarkPosXPercent === "number") setWatermarkPosXPercent(s.watermarkPosXPercent);
          if (typeof s.watermarkPosYPercent === "number") setWatermarkPosYPercent(s.watermarkPosYPercent);
          if (typeof s.fadeInOut === "boolean") setFadeInOut(s.fadeInOut);
          if (typeof s.activeDraftFilename === "string" || s.activeDraftFilename === null) {
            setActiveDraftFilename(s.activeDraftFilename);
          }
          if (s.activePreviewTab === "canvas" || s.activePreviewTab === "rendered") {
            setActivePreviewTab(s.activePreviewTab);
          }

          if (typeof s.cachedSongId === "string" && s.cachedSongId) {
            const restoredId = s.cachedSongId;
            setCachedSongId(restoredId);
            const previewUrl =
              typeof s.songPreviewUrl === "string" && !s.songPreviewUrl.startsWith("blob:")
                ? s.songPreviewUrl
                : `${API_BASE}/api/song-preview/${restoredId}`;
            setSongPreviewUrl(previewUrl);
            // Verify cached audio still exists on backend (only clear if server returns 404)
            fetch(`${API_BASE}/api/song-preview/${restoredId}`, {
              headers: { Range: "bytes=0-0" },
            })
              .then((res) => {
                if (!cancelled && res.status === 404) {
                  setCachedSongId(null);
                  setSongPreviewUrl(null);
                }
              })
              .catch(() => { });
          }

          if (s.job && typeof s.job === "object" && typeof s.job.job_id === "string") {
            const restoredJob = s.job as JobStatusResponse;
            fetch(`${API_BASE}/api/status/${restoredJob.job_id}`)
              .then((res) => (res.ok ? res.json() : null))
              .then((freshJob) => {
                if (!cancelled && freshJob) {
                  setJob(freshJob);
                } else if (!cancelled && restoredJob.stage !== "completed") {
                  setJob(null);
                }
              })
              .catch(() => { });
          }
        }
      }
    } catch {
      // Ignore malformed saved state
    } finally {
      setIsProjectHydrated(true);
    }

    loadMediaFilesFromIDB().then(({ visualClips: savedClips, songFile: savedSong }) => {
      if (cancelled || userModifiedMediaRef.current) {
        mediaHydratedRef.current = true;
        return;
      }
      if (savedClips.length > 0) {
        setVisualClips(savedClips);
      }
      if (savedSong) {
        setSongFile(savedSong);
      }
      mediaHydratedRef.current = true;
    });

    return () => {
      cancelled = true;
    };
  }, []);

  // Auto-save studio settings & timeline captions to localStorage
  useEffect(() => {
    if (!isProjectHydrated) return;
    const payload = {
      defaultsVersion: 2,
      activeSidebarTab,
      timelineViewMode,
      timelineZoom,
      selectedCaptionIdx,
      detectedLyricsSource,
      clipSpeed,
      clipTrimStart,
      clipTrimEnd,
      videoSegments,
      loopMode,
      videoZoom,
      mirrorFlip,
      brightness,
      contrast,
      saturation,
      vignette,
      songSourceMode,
      songUrl,
      audioStartSec,
      audioStartText,
      audioMaxDurationSec,
      audioFade,
      cachedSongId,
      songPreviewUrl: songPreviewUrl && !songPreviewUrl.startsWith("blob:") ? songPreviewUrl : null,
      fetchedSongDuration,
      rawSongDuration,
      appliedAudioStartSec,
      songFetchMsg,
      lyricsOffsetSec,
      captionMode,
      songName,
      artistName,
      manualLyrics,
      language,
      editableTimeline,
      lyricsSearchQuery,
      selectedFontFamily,
      fontPreviewSampleText,
      fontSize,
      fontThickness,
      fontBrightness,
      primaryColorHex,
      outlineWidth,
      shadowDepth,
      textPosYPercent,
      boldText,
      italicText,
      frameLayout,
      colorGrade,
      stylePreset,
      blurInterval,
      cornerRadius,
      watermarkText,
      watermarkSize,
      watermarkPosXPercent,
      watermarkPosYPercent,
      fadeInOut,
      activeDraftFilename,
      activePreviewTab,
      job,
    };
    const serialized = JSON.stringify(payload);
    latestProjectJsonRef.current = serialized;
    const timer = setTimeout(() => {
      try {
        window.localStorage.setItem(PROJECT_STORAGE_KEY, serialized);
      } catch {
        // Ignore quota errors
      }
    }, 200);
    return () => clearTimeout(timer);
  }, [
    isProjectHydrated,
    activeSidebarTab,
    timelineViewMode,
    timelineZoom,
    selectedCaptionIdx,
    detectedLyricsSource,
    clipSpeed,
    clipTrimStart,
    clipTrimEnd,
    videoSegments,
    loopMode,
    videoZoom,
    mirrorFlip,
    brightness,
    contrast,
    saturation,
    vignette,
    songSourceMode,
    songUrl,
    audioStartSec,
    audioStartText,
    audioMaxDurationSec,
    audioFade,
    cachedSongId,
    songPreviewUrl,
    fetchedSongDuration,
    rawSongDuration,
    appliedAudioStartSec,
    songFetchMsg,
    lyricsOffsetSec,
    captionMode,
    songName,
    artistName,
    manualLyrics,
    language,
    editableTimeline,
    lyricsSearchQuery,
    selectedFontFamily,
    fontPreviewSampleText,
    fontSize,
    fontThickness,
    fontBrightness,
    primaryColorHex,
    outlineWidth,
    shadowDepth,
    textPosYPercent,
    boldText,
    italicText,
    frameLayout,
    colorGrade,
    stylePreset,
    blurInterval,
    cornerRadius,
    watermarkText,
    watermarkSize,
    watermarkPosXPercent,
    watermarkPosYPercent,
    fadeInOut,
    activeDraftFilename,
    activePreviewTab,
    job,
  ]);

  // Build a serializable undo/redo snapshot of all user-editable creative settings & captions
  const serializeUndoSnapshot = () =>
    JSON.stringify({
      selectedCaptionIdx,
      clipSpeed,
      clipTrimStart,
      clipTrimEnd,
      videoSegments,
      loopMode,
      videoZoom,
      mirrorFlip,
      brightness,
      contrast,
      saturation,
      vignette,
      audioStartSec,
      audioStartText,
      audioMaxDurationSec,
      audioFade,
      lyricsOffsetSec,
      captionMode,
      songName,
      artistName,
      manualLyrics,
      language,
      editableTimeline,
      selectedFontFamily,
      fontSize,
      fontThickness,
      fontBrightness,
      primaryColorHex,
      outlineWidth,
      shadowDepth,
      textPosYPercent,
      boldText,
      italicText,
      frameLayout,
      colorGrade,
      stylePreset,
      blurInterval,
      cornerRadius,
      watermarkText,
      watermarkSize,
      watermarkPosXPercent,
      watermarkPosYPercent,
      fadeInOut,
    });

  const applyUndoSnapshotJson = (rawJson: string) => {
    try {
      const s = JSON.parse(rawJson);
      if (!s || typeof s !== "object") return;
      isApplyingUndoRedoRef.current = true;
      committedUndoJsonRef.current = rawJson;
      setSelectedCaptionIdx(s.selectedCaptionIdx ?? null);
      setClipSpeed(s.clipSpeed ?? 1.0);
      setClipTrimStart(s.clipTrimStart ?? 0);
      setClipTrimEnd(s.clipTrimEnd ?? 0);
      setVideoSegments(Array.isArray(s.videoSegments) ? s.videoSegments : []);
      setLoopMode(s.loopMode ?? "crossfade");
      setVideoZoom(s.videoZoom ?? 1.0);
      setMirrorFlip(Boolean(s.mirrorFlip));
      setBrightness(s.brightness ?? -0.3);
      setContrast(s.contrast ?? 1.0);
      setSaturation(s.saturation ?? 1.0);
      setVignette(Boolean(s.vignette));
      setAudioStartSec(s.audioStartSec ?? 0);
      setAudioStartText(s.audioStartText ?? "0:00");
      setAudioMaxDurationSec(s.audioMaxDurationSec ?? 0);
      setAudioFade(s.audioFade ?? true);
      setLyricsOffsetSec(s.lyricsOffsetSec ?? 0);
      setCaptionMode(s.captionMode ?? "auto");
      setSongName(s.songName ?? "");
      setArtistName(s.artistName ?? "");
      setManualLyrics(s.manualLyrics ?? "");
      setLanguage(s.language ?? "si");
      setEditableTimeline(Array.isArray(s.editableTimeline) ? s.editableTimeline : []);
      setSelectedFontFamily(s.selectedFontFamily ?? defaultFontFamily);
      setFontSize(s.fontSize ?? 40);
      setFontThickness(s.fontThickness ?? 0);
      setFontBrightness(s.fontBrightness ?? 1.35);
      setPrimaryColorHex(s.primaryColorHex ?? "#FFFFFF");
      setOutlineWidth(s.outlineWidth ?? 1.8);
      setShadowDepth(s.shadowDepth ?? 2.8);
      setTextPosYPercent(s.textPosYPercent ?? 50);
      setBoldText(s.boldText ?? true);
      setItalicText(Boolean(s.italicText));
      setFrameLayout(s.frameLayout ?? "rounded_square_card");
      setColorGrade(s.colorGrade ?? "golden_sunset");
      setStylePreset(s.stylePreset ?? "rw_aesthetic_sinhala");
      setBlurInterval(s.blurInterval ?? 0);
      setCornerRadius(s.cornerRadius ?? 48);
      setWatermarkText(s.watermarkText ?? "The_Black Hart 🖤");
      setWatermarkSize(s.watermarkSize ?? 15);
      setWatermarkPosXPercent(s.watermarkPosXPercent ?? 50);
      setWatermarkPosYPercent(s.watermarkPosYPercent ?? 98);
      setFadeInOut(s.fadeInOut ?? true);
      setTimeout(() => {
        isApplyingUndoRedoRef.current = false;
      }, 60);
    } catch {
      isApplyingUndoRedoRef.current = false;
    }
  };

  const syncUndoCounts = () => {
    setUndoCount(undoPastRef.current.length);
    setRedoCount(undoFutureRef.current.length);
  };

  // Debounced editor history tracker (groups rapid slider drags/typing into one step)
  useEffect(() => {
    if (!isProjectHydrated || isApplyingUndoRedoRef.current || dragCaptionState !== null) {
      return;
    }
    const nextJson = serializeUndoSnapshot();
    if (committedUndoJsonRef.current === null) {
      committedUndoJsonRef.current = nextJson;
      return;
    }
    if (nextJson === committedUndoJsonRef.current) return;

    const timer = setTimeout(() => {
      if (isApplyingUndoRedoRef.current || committedUndoJsonRef.current === null) return;
      if (nextJson === committedUndoJsonRef.current) return;
      undoPastRef.current = [...undoPastRef.current.slice(-49), committedUndoJsonRef.current];
      committedUndoJsonRef.current = nextJson;
      undoFutureRef.current = [];
      syncUndoCounts();
    }, 320);

    return () => clearTimeout(timer);
  }, [
    isProjectHydrated,
    dragCaptionState,
    selectedCaptionIdx,
    clipSpeed,
    clipTrimStart,
    clipTrimEnd,
    videoSegments,
    loopMode,
    videoZoom,
    mirrorFlip,
    brightness,
    contrast,
    saturation,
    vignette,
    audioStartSec,
    audioStartText,
    audioMaxDurationSec,
    audioFade,
    lyricsOffsetSec,
    captionMode,
    songName,
    artistName,
    manualLyrics,
    language,
    editableTimeline,
    selectedFontFamily,
    fontSize,
    fontThickness,
    fontBrightness,
    primaryColorHex,
    outlineWidth,
    shadowDepth,
    textPosYPercent,
    boldText,
    italicText,
    frameLayout,
    colorGrade,
    stylePreset,
    blurInterval,
    cornerRadius,
    watermarkText,
    watermarkSize,
    watermarkPosXPercent,
    watermarkPosYPercent,
    fadeInOut,
  ]);

  const handleUndo = () => {
    // Commit any unsaved un-debounced change before stepping back so nothing is skipped
    const liveJson = serializeUndoSnapshot();
    if (
      committedUndoJsonRef.current !== null &&
      liveJson !== committedUndoJsonRef.current
    ) {
      undoPastRef.current = [...undoPastRef.current.slice(-49), committedUndoJsonRef.current];
      committedUndoJsonRef.current = liveJson;
    }
    if (undoPastRef.current.length === 0) return;
    const prevJson = undoPastRef.current[undoPastRef.current.length - 1];
    undoPastRef.current = undoPastRef.current.slice(0, -1);
    if (committedUndoJsonRef.current !== null) {
      undoFutureRef.current = [committedUndoJsonRef.current, ...undoFutureRef.current.slice(0, 49)];
    }
    applyUndoSnapshotJson(prevJson);
    syncUndoCounts();
    showToast("↶ Undid last change");
  };

  const handleRedo = () => {
    if (undoFutureRef.current.length === 0) return;
    const nextJson = undoFutureRef.current[0];
    undoFutureRef.current = undoFutureRef.current.slice(1);
    if (committedUndoJsonRef.current !== null) {
      undoPastRef.current = [...undoPastRef.current.slice(-49), committedUndoJsonRef.current];
    }
    applyUndoSnapshotJson(nextJson);
    syncUndoCounts();
    showToast("↷ Redid change");
  };

  // Flush latest snapshot synchronously right before page refresh/unload
  useEffect(() => {
    const flushNow = () => {
      if (latestProjectJsonRef.current) {
        try {
          window.localStorage.setItem(PROJECT_STORAGE_KEY, latestProjectJsonRef.current);
        } catch {
          // Ignore quota errors
        }
      }
    };
    window.addEventListener("pagehide", flushNow);
    window.addEventListener("beforeunload", flushNow);
    return () => {
      window.removeEventListener("pagehide", flushNow);
      window.removeEventListener("beforeunload", flushNow);
    };
  }, []);

  // Persist binary video clips and uploaded audio File in IndexedDB
  useEffect(() => {
    if (!mediaHydratedRef.current) return;
    saveMediaFilesToIDB(visualClips, songFile);
  }, [visualClips, songFile]);

  const handleResetProject = async () => {
    const hasWork =
      visualClips.length > 0 ||
      editableTimeline.length > 0 ||
      Boolean(cachedSongId || songUrl.trim() || songFile || manualLyrics.trim());
    if (hasWork && !window.confirm("Start a new project and clear current clips, song, and captions?")) {
      return;
    }
    if (fetchSongAbortRef.current) {
      fetchSongAbortRef.current.abort();
      fetchSongAbortRef.current = null;
      setIsFetchingSong(false);
    }
    latestProjectJsonRef.current = null;
    await clearPersistedProject();
    userModifiedMediaRef.current = true;
    setVisualClips([]);
    setClipPreviewUrls([]);
    setSelectedSingleClipIdx(null);
    setPreviewClipUrl(null);
    setDetectedClipDuration(0);
    setIsEditingFullLyricsText(false);
    setClipSpeed(1.0);
    setClipTrimStart(0);
    setClipTrimEnd(0);
    setVideoSegments([]);
    setSelectedVideoSegmentIdx(null);
    setIsVideoBladeMode(false);
    setLoopMode("crossfade");
    setVideoZoom(1.0);
    setMirrorFlip(false);
    setBrightness(-0.3);
    setFontBrightness(1.35);
    setSelectedStartLineIdx(-1);
    setContrast(1.0);
    setSaturation(1.0);
    setVignette(false);
    setSongSourceMode("url");
    setSongUrl("");
    setSongFile(null);
    setAudioStartSec(0);
    setAudioStartText("0:00");
    setAudioMaxDurationSec(0);
    setAudioFade(true);
    setCachedSongId(null);
    setSongPreviewUrl(null);
    setFetchedSongDuration(null);
    setRawSongDuration(null);
    setAppliedAudioStartSec(0);
    setSongFetchMsg(null);
    setDetectedLyricsSource(null);
    setCurrentAudioTime(0);
    setIsAudioPlaying(false);
    setIsTapSyncActive(false);
    setTapLineCursor(0);
    setLyricsOffsetSec(0);
    setCaptionMode("auto");
    setSongName("");
    setArtistName("");
    setManualLyrics("");
    setEditableTimeline([]);
    setSelectedCaptionIdx(null);
    setLyricsSearchQuery("");
    setWebLyricsCandidates([]);
    setWebLyricsStatus(null);
    setActivePreviewTab("canvas");
    setActiveDraftFilename(null);
    setJob(null);
    setUiError(null);
    undoPastRef.current = [];
    undoFutureRef.current = [];
    committedUndoJsonRef.current = null;
    setUndoCount(0);
    setRedoCount(0);
    showToast("Started a clean new project");
  };

  const handleFontFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setIsLoadingFont(true);
    setFontStatusMsg(null);
    // Register instant local blob URL preview even before backend finishes
    const localBlobUrl = URL.createObjectURL(file);
    const tempFamily = file.name.replace(/\.(ttf|otf)$/i, "").replace(/[-_]/g, " ");
    await registerBrowserFontFace(tempFamily, localBlobUrl);
    try {
      const formData = new FormData();
      formData.append("font_file", file);
      const res = await fetch(`${API_BASE}/api/fonts/upload`, {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Font upload failed");
      if (data.font?.family) {
        await registerBrowserFontFace(data.font.family, localBlobUrl);
      }
      await refreshFontsFromBackend();
      if (data.font) {
        setNewlyAddedFonts((prev) => Array.from(new Set([data.font.family, ...prev])));
        handleSelectFont(data.font);
        setFontStatusMsg(`✓ Added & previewing custom font: ${data.font.family}`);
      }
    } catch (err: any) {
      setSelectedFontFamily(tempFamily);
      setFontStatusMsg(`Local preview active (${tempFamily}): ${err.message}`);
    } finally {
      setIsLoadingFont(false);
    }
  };

  const handleDownloadFont = async (catalogId?: string, directUrl?: string) => {
    setIsLoadingFont(true);
    setFontStatusMsg(null);
    try {
      const formData = new FormData();
      if (catalogId) formData.append("catalog_id", catalogId);
      if (directUrl) formData.append("font_url", directUrl);
      const res = await fetch(`${API_BASE}/api/fonts/download`, {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to download font");
      await refreshFontsFromBackend();
      if (data.font) {
        setNewlyAddedFonts((prev) => Array.from(new Set([data.font.family, ...prev])));
        handleSelectFont(data.font);
        setFontStatusMsg(`✓ Activated Sinhala font: ${data.font.family}`);
      }
      setCustomFontUrl("");
    } catch (err: any) {
      setFontStatusMsg(`Download error: ${err.message}`);
    } finally {
      setIsLoadingFont(false);
    }
  };

  useEffect(() => {
    if (visualClips.length > 0) {
      const urls = visualClips.map((c) => URL.createObjectURL(c));
      setClipPreviewUrls(urls);
      return () => {
        urls.forEach((u) => URL.revokeObjectURL(u));
      };
    } else {
      setClipPreviewUrls([]);
      setPreviewClipUrl(null);
      setSelectedSingleClipIdx(null);
      setDetectedClipDuration(0);
    }
  }, [visualClips]);

  // Keep selectedSingleClipIdx valid when clips are removed and compute active previewClipUrl
  useEffect(() => {
    if (
      selectedSingleClipIdx !== null &&
      (selectedSingleClipIdx < 0 || selectedSingleClipIdx >= visualClips.length)
    ) {
      setSelectedSingleClipIdx(null);
      return;
    }
    if (clipPreviewUrls.length === 0) {
      setPreviewClipUrl(null);
      return;
    }
    if (selectedSingleClipIdx !== null && clipPreviewUrls[selectedSingleClipIdx]) {
      setPreviewClipUrl(clipPreviewUrls[selectedSingleClipIdx]);
      return;
    }
    if (clipPreviewUrls.length === 1) {
      setPreviewClipUrl(clipPreviewUrls[0]);
      return;
    }
    // Mix & Blend All Clips mode across the song timeline
    const songDur = Math.max(12, fetchedSongDuration || 30);
    const slotDur = Math.max(3.5, songDur / clipPreviewUrls.length);
    const activeSlot =
      Math.floor(Math.max(0, currentAudioTime) / slotDur) % clipPreviewUrls.length;
    const nextUrl = clipPreviewUrls[activeSlot] || clipPreviewUrls[0];
    setPreviewClipUrl((prev) => (prev === nextUrl ? prev : nextUrl));
  }, [
    clipPreviewUrls,
    selectedSingleClipIdx,
    currentAudioTime,
    fetchedSongDuration,
    visualClips.length,
  ]);

  // Cycle through preview lines when song audio is NOT actively playing
  useEffect(() => {
    if (isAudioPlaying) return;
    const lines =
      editableTimeline.length > 0
        ? editableTimeline.map((l) => l.text)
        : manualLyrics
          .split("\n")
          .map((l) => l.trim())
          .filter(Boolean);
    if (lines.length === 0) return;
    const timer = setInterval(() => {
      setPreviewLineIndex((prev) => (prev + 1) % lines.length);
    }, 2400);
    return () => clearInterval(timer);
  }, [manualLyrics, editableTimeline, isAudioPlaying]);

  const showToast = (msg: string) => {
    setStudioToast(msg);
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    toastTimerRef.current = setTimeout(() => setStudioToast(null), 2400);
  };

  const [engineHealth, setEngineHealth] = useState<{
    ready: boolean;
    engine: string;
    customFonts: number;
  }>({
    ready: true,
    engine: "FFmpeg + HarfBuzz",
    customFonts: 0,
  });

  useEffect(() => {
    let active = true;
    const checkHealth = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/health`);
        if (!res.ok) return;
        const data = await res.json();
        if (active && data?.status === "ok") {
          setEngineHealth({
            ready: Boolean(data.ffmpeg_ready),
            engine: data.engine || "FFmpeg + HarfBuzz",
            customFonts: Number(data.custom_fonts_count || 0),
          });
        }
      } catch {
        if (active) {
          setEngineHealth((prev) => ({ ...prev, ready: false }));
        }
      }
    };
    checkHealth();
    const id = setInterval(checkHealth, 25000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, []);

  // Decode fetched audio into a real 140-bar normalized amplitude waveform
  useEffect(() => {
    if (!songPreviewUrl) {
      setWaveformBars([]);
      return;
    }
    let cancelled = false;
    const decodeWaveform = async () => {
      try {
        const res = await fetch(songPreviewUrl);
        if (!res.ok) return;
        const buf = await res.arrayBuffer();
        const AudioCtx =
          window.AudioContext ||
          (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
        if (!AudioCtx) return;
        const ctx = new AudioCtx();
        const audioBuf = await ctx.decodeAudioData(buf);
        const channel = audioBuf.getChannelData(0);
        const barCount = 140;
        const step = Math.max(1, Math.floor(channel.length / barCount));
        const bars: number[] = [];
        let maxPeak = 0.01;
        for (let b = 0; b < barCount; b++) {
          let peak = 0;
          const start = b * step;
          const end = Math.min(channel.length, start + step);
          const stride = Math.max(1, Math.floor((end - start) / 64));
          for (let i = start; i < end; i += stride) {
            const v = Math.abs(channel[i]);
            if (v > peak) peak = v;
          }
          bars.push(peak);
          if (peak > maxPeak) maxPeak = peak;
        }
        const normalized = bars.map((v) =>
          Math.max(12, Math.min(96, Math.round((v / maxPeak) * 92)))
        );
        if (!cancelled) setWaveformBars(normalized);
        ctx.close().catch(() => { });
      } catch {
        // Fallback smoothly to deterministic ambient wave when browser decode fails
      }
    };
    decodeWaveform();
    return () => {
      cancelled = true;
    };
  }, [songPreviewUrl]);

  // Poll job status while FFmpeg pipeline is running
  useEffect(() => {
    if (!job || job.stage === "completed" || job.stage === "failed") {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
      return;
    }

    pollTimerRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/status/${job.job_id}`);
        if (res.ok) {
          const data: JobStatusResponse = await res.json();
          setJob(data);
          if (data.detected_title && !songName.trim()) setSongName(data.detected_title);
          if (data.detected_artist && !artistName.trim()) setArtistName(data.detected_artist);
          if (data.lyrics_source) setDetectedLyricsSource(data.lyrics_source);
          if (
            data.lyrics_preview &&
            data.lyrics_preview.length > 0 &&
            editableTimeline.length === 0
          ) {
            setEditableTimeline(data.lyrics_preview);
          }
          if (data.stage === "completed") {
            setIsSubmitting(false);
            if (data.video_url) {
              setSelectedRenderedVideoUrl(data.video_url);
              setSelectedRenderedVideoLabel(
                data.output_filename || songName.trim() || `Render ${data.job_id}`
              );
            }
            if (data.is_draft) {
              if (data.output_filename) {
                setActiveDraftFilename(data.output_filename);
              }
              showToast("Draft video saved to draft folder!");
            } else {
              setActiveDraftFilename(null);
              showToast(
                data.promoted_from_draft
                  ? "Final video rendered & draft removed from draft folder!"
                  : "Rendered 9:16 TikTok video saved to Downloads/Titok video meker!"
              );
            }
            setActivePreviewTab("rendered");
            fetchRenderedVideos();
            if (pollTimerRef.current) clearInterval(pollTimerRef.current);
          } else if (data.stage === "failed") {
            setIsSubmitting(false);
            if (pollTimerRef.current) clearInterval(pollTimerRef.current);
          }
        } else if (res.status === 404) {
          setIsSubmitting(false);
          setJob(null);
          if (pollTimerRef.current) clearInterval(pollTimerRef.current);
        }
      } catch (err) {
        console.error("Status polling error:", err);
      }
    }, 1200);

    return () => {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    };
  }, [job, editableTimeline.length]);

  const handleAddVisualClips = (files: FileList | null) => {
    if (!files) return;
    userModifiedMediaRef.current = true;
    setVisualClips((prev) => [...prev, ...Array.from(files)]);
  };

  const handleStopFetchSong = () => {
    if (fetchSongAbortRef.current) {
      fetchSongAbortRef.current.abort();
      fetchSongAbortRef.current = null;
    }
    setIsFetchingSong(false);
    setIsSyncingLyrics(false);
    setSongFetchMsg("Song fetching stopped by user.");
    showToast("Stopped song fetching.");
  };

  // Instant Song Fetcher + Automatic Sinhala ASR Auto-Captioning or Manual Vocal-Anchor Alignment
  const handleFetchSong = async (overrideMode?: "auto" | "manual") => {
    setSongFetchMsg(null);
    setUiError(null);
    if (songSourceMode === "url" && !songUrl.trim() && !songName.trim()) {
      const msg = "Please paste a TikTok/YouTube link or enter a Song Title first.";
      setSongFetchMsg(`Error: ${msg}`);
      showToast(msg);
      setActiveSidebarTab("audio");
      return;
    }
    if (songSourceMode === "upload" && !songFile) {
      const msg = "Please choose an audio/video file to upload first.";
      setSongFetchMsg(`Error: ${msg}`);
      showToast(msg);
      setActiveSidebarTab("audio");
      return;
    }

    const activeMode = overrideMode || captionMode;
    const effectiveManual = activeMode === "manual" ? manualLyrics.trim() : "";

    if (fetchSongAbortRef.current) {
      fetchSongAbortRef.current.abort();
    }
    const controller = new AbortController();
    fetchSongAbortRef.current = controller;

    setIsFetchingSong(true);
    setEditableTimeline([]);
    try {
      const formData = new FormData();
      if (songSourceMode === "url") {
        formData.append("song_url", songUrl.trim());
      } else if (songFile) {
        formData.append("song_file", songFile);
      }
      formData.append("song_name", songName.trim());
      formData.append("artist_name", artistName.trim());
      formData.append("manual_lyrics", effectiveManual);
      formData.append("language", language);
      formData.append("audio_start_sec", String(audioStartSec));
      formData.append("audio_max_duration_sec", String(audioMaxDurationSec));

      const res = await fetch(`${API_BASE}/api/fetch-song`, {
        method: "POST",
        body: formData,
        signal: controller.signal,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.detail || `Failed to fetch song (HTTP ${res.status}).`);
      }

      setCachedSongId(data.song_id);
      setSongPreviewUrl(`${API_BASE}${data.audio_preview_url}`);
      setFetchedSongDuration(data.duration);
      setRawSongDuration(data.raw_duration ?? data.duration);
      const effectiveStart = typeof data.audio_start_sec === "number" ? data.audio_start_sec : audioStartSec;
      setAppliedAudioStartSec(effectiveStart);
      setAudioStartSec(effectiveStart);
      setAudioStartText(formatMmSs(effectiveStart));
      setCurrentAudioTime(0);
      setDetectedLyricsSource(data.lyrics_source || null);
      const isPlaceholderTitle = (val: string) => {
        const lower = val.trim().toLowerCase();
        return !lower || lower === "fetched song" || lower.startsWith("original sound");
      };
      if (data.title && (isPlaceholderTitle(songName) || (data.lyrics_source && data.title !== songName.trim()))) {
        setSongName(data.title);
      }
      if (data.artist && !artistName.trim()) {
        setArtistName(data.artist);
      }

      if (data.aligned_lines && data.aligned_lines.length > 0) {
        setEditableTimeline(data.aligned_lines);
        setSelectedCaptionIdx(0);
      }
      setSelectedStartLineIdx(-1);
      if (typeof data.fetched_lyrics === "string" && data.fetched_lyrics.trim()) {
        setManualLyrics(data.fetched_lyrics);
      }

      const sourceInfo = data.lyrics_source
        ? `via ${data.lyrics_source}`
        : activeMode === "auto"
          ? "auto-synced"
          : "vocal-anchored";
      const msg = `Synced "${data.title}" (${data.duration}s) ${sourceInfo} with ${data.aligned_lines?.length || 0
        } caption lines!`;
      setSongFetchMsg(msg);
      showToast(msg);
    } catch (err: any) {
      if (err?.name === "AbortError") {
        setSongFetchMsg("Song fetching stopped by user.");
        return;
      }
      const errMsg = err?.message || "Could not connect to backend at localhost:8000";
      setSongFetchMsg(`Error: ${errMsg}`);
      setUiError(`Song Fetch Error: ${errMsg}`);
      showToast(`Fetch failed: ${errMsg}`);
    } finally {
      if (fetchSongAbortRef.current === controller) {
        fetchSongAbortRef.current = null;
        setIsFetchingSong(false);
      }
    }
  };

  // Cut any custom start/duration range from an already-fetched song without re-downloading
  const handleTrimCachedSong = async (
    overrideStartSec?: number,
    overrideMaxDurationSec?: number
  ) => {
    if (!cachedSongId) {
      await handleFetchSong();
      return;
    }
    const startVal = Math.max(0, overrideStartSec ?? audioStartSec);
    const maxDurVal = Math.max(0, overrideMaxDurationSec ?? audioMaxDurationSec);
    const effectiveManual = captionMode === "manual" ? manualLyrics.trim() : "";

    setIsTrimmingSong(true);
    setSongFetchMsg(null);
    try {
      const formData = new FormData();
      formData.append("cached_song_id", cachedSongId);
      formData.append("audio_start_sec", String(startVal));
      formData.append("audio_max_duration_sec", String(maxDurVal));
      formData.append("manual_lyrics", effectiveManual);
      formData.append("song_name", songName.trim());
      formData.append("artist_name", artistName.trim());
      formData.append("language", language);

      const res = await fetch(`${API_BASE}/api/trim-cached-song`, {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Failed to cut song segment.");
      }

      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current.currentTime = 0;
      }
      setIsAudioPlaying(false);
      setCurrentAudioTime(0);
      setSongPreviewUrl(`${API_BASE}${data.audio_preview_url}`);
      setFetchedSongDuration(data.duration);
      setRawSongDuration(data.raw_duration ?? rawSongDuration ?? data.duration);
      const effectiveStart = typeof data.audio_start_sec === "number" ? data.audio_start_sec : startVal;
      setAppliedAudioStartSec(effectiveStart);
      setAudioStartSec(effectiveStart);
      setAudioStartText(formatMmSs(effectiveStart));
      if (data.lyrics_source) {
        setDetectedLyricsSource(data.lyrics_source);
      }
      if (data.aligned_lines && data.aligned_lines.length > 0) {
        setEditableTimeline(data.aligned_lines);
        setSelectedCaptionIdx(0);
      }
      setSelectedStartLineIdx(-1);
      if (typeof data.fetched_lyrics === "string" && data.fetched_lyrics.trim()) {
        setManualLyrics(data.fetched_lyrics);
      }
      const msg = `Cut song from ${formatMmSs(effectiveStart)} (${data.duration}s) & synced ${data.aligned_lines?.length || 0
        } captions!`;
      setSongFetchMsg(msg);
      showToast(msg);
    } catch (err: any) {
      setSongFetchMsg(`Error: ${err.message || "Song segment cut failed"}`);
    } finally {
      setIsTrimmingSong(false);
    }
  };

  // Re-run Sinhala ASR Auto-Captioning or Manual Vocal-Anchor Alignment on cached song
  const handleAutoSyncLyricsToAudio = async (
    overrideMode?: "auto" | "manual",
    overrideManualText?: string,
    overrideStartLineIdx?: number
  ) => {
    const activeMode = overrideMode || captionMode;
    const fallbackLyrics =
      manualLyrics.trim() || editableTimeline.map((l) => l.text).join("\n");
    const rawText =
      overrideManualText !== undefined ? overrideManualText : fallbackLyrics;
    const effectiveManual =
      overrideMode === "auto" && overrideManualText === undefined
        ? ""
        : rawText.trim();
    const effectiveStartIdx =
      overrideStartLineIdx !== undefined ? overrideStartLineIdx : selectedStartLineIdx;

    if (!cachedSongId) {
      await handleFetchSong(activeMode);
      return;
    }

    if (fetchSongAbortRef.current) {
      fetchSongAbortRef.current.abort();
    }
    const controller = new AbortController();
    fetchSongAbortRef.current = controller;

    setIsSyncingLyrics(true);
    setSongFetchMsg(null);
    try {
      const formData = new FormData();
      formData.append("cached_song_id", cachedSongId);
      formData.append("manual_lyrics", effectiveManual);
      formData.append("song_name", songName.trim());
      formData.append("artist_name", artistName.trim());
      formData.append("language", language);
      formData.append("start_line_index", String(effectiveStartIdx));

      const res = await fetch(`${API_BASE}/api/sync-lyrics`, {
        method: "POST",
        body: formData,
        signal: controller.signal,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Lyrics sync failed");
      if (data.title && (!songName.trim() || songName.trim().toLowerCase().startsWith("original sound"))) {
        setSongName(data.title);
      }
      if (data.artist && !artistName.trim()) {
        setArtistName(data.artist);
      }
      if (data.lyrics_source) {
        setDetectedLyricsSource(data.lyrics_source);
      }
      if (typeof data.fetched_lyrics === "string" && data.fetched_lyrics.trim()) {
        const curLines = manualLyrics.split("\n").filter((l) => l.trim()).length;
        const newLines = data.fetched_lyrics.split("\n").filter((l: string) => l.trim()).length;
        if (!manualLyrics.trim() || newLines >= curLines) {
          setManualLyrics(data.fetched_lyrics);
        }
      }
      if (data.aligned_lines && data.aligned_lines.length > 0) {
        setEditableTimeline(data.aligned_lines);
        setSelectedCaptionIdx(0);
      }
      const syncNotice =
        effectiveStartIdx >= 0
          ? `Synced ${data.aligned_lines?.length || 0} caption lines starting from Line #${effectiveStartIdx + 1} to song vocals!`
          : `Synced ${data.aligned_lines?.length || 0} caption lines (${data.lyrics_source || "Vocal Sync"}) to song vocals!`;
      setSongFetchMsg(syncNotice);
      showToast(syncNotice);
    } catch (err: any) {
      if (err?.name === "AbortError") {
        setSongFetchMsg("Song syncing stopped by user.");
        return;
      }
      setSongFetchMsg(`Error: ${err.message}`);
      showToast(`Sync Error: ${err.message}`);
    } finally {
      if (fetchSongAbortRef.current === controller) {
        fetchSongAbortRef.current = null;
      }
      setIsSyncingLyrics(false);
    }
  };

  // Fetch clean Sinhala lyrics by Song Title or URL from LyriHub.lk / Lyrics-LK.com
  const handleSearchSinhalaLyrics = async (overrideQuery?: string) => {
    const q = (overrideQuery ?? (lyricsSearchQuery || songName)).trim();
    if (!q) {
      setWebLyricsStatus("Error: Enter a song title (e.g. Sanda Mithuri / සඳ මිතුරි) or lyrics URL.");
      return;
    }

    setIsSearchingWebLyrics(true);
    setWebLyricsStatus(null);
    setWebLyricsCandidates([]);
    try {
      const formData = new FormData();
      formData.append("query", q);
      formData.append("artist_name", artistName.trim());
      const res = await fetch(`${API_BASE}/api/search-sinhala-lyrics`, {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "No Sinhala lyrics found.");
      }

      const best = data.best_match;
      setManualLyrics(best.lyrics_text);
      setCaptionMode("manual");
      if (best.title && !songName.trim()) setSongName(best.title);
      if (best.artist && !artistName.trim()) setArtistName(best.artist);
      if (Array.isArray(data.candidates)) {
        setWebLyricsCandidates(data.candidates);
      }
      setWebLyricsStatus(
        `✓ Loaded ${best.line_count} Sinhala lines from ${best.source} (${best.title || q})`
      );

      // If audio is already loaded, immediately sync the fetched lyrics to the vocals!
      if (cachedSongId) {
        await handleAutoSyncLyricsToAudio("manual", best.lyrics_text);
      } else {
        setEditableTimeline([]);
      }
    } catch (err: any) {
      setWebLyricsStatus(`Error: ${err.message}`);
    } finally {
      setIsSearchingWebLyrics(false);
    }
  };

  // Live Tap-to-Sync Mode
  const startLiveTapToSync = () => {
    const sourceLines =
      editableTimeline.length > 0
        ? editableTimeline.map((l) => l.text)
        : manualLyrics
          .split("\n")
          .map((l) => l.trim())
          .filter((l) => l && !(l.startsWith("[") && l.endsWith("]")));
    if (sourceLines.length === 0) return;

    const totalDur = fetchedSongDuration || 30;
    const initial: LyricLine[] = sourceLines.map((txt, idx) => {
      const start = Number((idx * 2.5).toFixed(2));
      const end = Number(Math.min((idx + 1) * 2.5, totalDur).toFixed(2));
      const prevLine = editableTimeline[idx];
      return {
        start,
        end,
        text: txt,
        words: buildWordTimingsForLine(txt, start, end, prevLine),
      };
    });
    setEditableTimeline(initial);
    setTapLineCursor(0);
    setSelectedCaptionIdx(0);
    setIsTapSyncActive(true);

    if (audioRef.current) {
      audioRef.current.currentTime = 0;
      audioRef.current.play().catch(() => { });
    }
  };

  const handleTapCurrentLine = () => {
    if (!isTapSyncActive) return;
    const now = Number((audioRef.current?.currentTime || 0).toFixed(2));
    const totalDur = fetchedSongDuration || 30;

    setEditableTimeline((prev) => {
      const next = [...prev];
      if (tapLineCursor < next.length) {
        if (tapLineCursor > 0) {
          const prevItem = next[tapLineCursor - 1];
          const nextPrevEnd = Math.max(prevItem.start + 0.4, Number((now - 0.06).toFixed(2)));
          next[tapLineCursor - 1] = {
            ...prevItem,
            end: nextPrevEnd,
            words: buildWordTimingsForLine(prevItem.text, prevItem.start, nextPrevEnd, prevItem),
          };
        }
        const curItem = next[tapLineCursor];
        const nextCurEnd = Number(Math.min(now + 2.8, totalDur).toFixed(2));
        next[tapLineCursor] = {
          ...curItem,
          start: now,
          end: nextCurEnd,
          words: buildWordTimingsForLine(curItem.text, now, nextCurEnd, curItem),
        };
      }
      return next;
    });

    if (tapLineCursor + 1 >= editableTimeline.length) {
      setIsTapSyncActive(false);
    } else {
      setSelectedCaptionIdx(tapLineCursor + 1);
      setTapLineCursor((c) => c + 1);
    }
  };

  const handleRemoveVisualClip = (index: number) => {
    userModifiedMediaRef.current = true;
    setVisualClips((prev) => {
      const next = prev.filter((_, idx) => idx !== index);
      if (next.length === 0) {
        setVideoSegments([]);
        setSelectedVideoSegmentIdx(null);
        setIsVideoBladeMode(false);
        setClipTrimStart(0);
        setClipTrimEnd(0);
      }
      return next;
    });
    showToast("🗑️ Deleted video clip");
  };

  const baseRawClipDur = Math.max(2, detectedClipDuration || 6);
  const effectiveVideoSegments: { start: number; end: number }[] =
    videoSegments.length > 0
      ? videoSegments
      : [
        {
          start: Math.max(0, Math.min(clipTrimStart || 0, baseRawClipDur - 0.5)),
          end:
            clipTrimEnd > 0
              ? Math.min(baseRawClipDur, (clipTrimStart || 0) + clipTrimEnd)
              : baseRawClipDur,
        },
      ];

  const syncTrimFromSegments = (segs: { start: number; end: number }[]) => {
    setVideoSegments(segs);
    if (segs.length === 1) {
      const s = Number(segs[0].start.toFixed(2));
      const dur = Number(Math.max(0.4, segs[0].end - segs[0].start).toFixed(2));
      setClipTrimStart(s);
      setClipTrimEnd(dur >= baseRawClipDur - 0.15 && s === 0 ? 0 : dur);
    } else if (segs.length > 1) {
      setClipTrimStart(Number(segs[0].start.toFixed(2)));
      setClipTrimEnd(0);
    }
  };

  const mapTimelineSecToVideoSourceSec = (timelineSec: number): number => {
    const segs = effectiveVideoSegments;
    if (!segs.length) return 0;
    const speed = Math.max(0.5, clipSpeed || 1.0);
    const totalKept = Math.max(
      0.3,
      segs.reduce((acc, s) => acc + Math.max(0.2, (s.end - s.start) / speed), 0)
    );
    const loopOffset = ((timelineSec % totalKept) + totalKept) % totalKept;
    let accum = 0;
    for (let i = 0; i < segs.length; i++) {
      const segDur = Math.max(0.2, (segs[i].end - segs[i].start) / speed);
      if (loopOffset <= accum + segDur + 0.01 || i === segs.length - 1) {
        const localOff = Math.max(0, Math.min(segDur, loopOffset - accum));
        return Number((segs[i].start + localOff * speed).toFixed(2));
      }
      accum += segDur;
    }
    return segs[0].start;
  };

  const handleBladeVideoSegmentAtSourceTime = (segIdx: number, rawCutSourceSec: number) => {
    if (visualClips.length === 0) {
      showToast("Upload a video clip first to use the Cut / Blade tool");
      return;
    }
    const segs = [...effectiveVideoSegments];
    const targetIdx = segs[segIdx] ? segIdx : 0;
    const chosenSeg = segs[targetIdx];
    if (!chosenSeg || chosenSeg.end - chosenSeg.start < 0.6) {
      showToast("Selected video part is too short to split further");
      return;
    }
    let cutSourceSec = Number(rawCutSourceSec.toFixed(2));
    if (cutSourceSec <= chosenSeg.start + 0.25 || cutSourceSec >= chosenSeg.end - 0.25) {
      cutSourceSec = Number(((chosenSeg.start + chosenSeg.end) / 2).toFixed(2));
    }
    const leftPart = { start: chosenSeg.start, end: cutSourceSec };
    const rightPart = { start: cutSourceSec, end: chosenSeg.end };
    const nextSegs = [...segs.slice(0, targetIdx), leftPart, rightPart, ...segs.slice(targetIdx + 1)];
    syncTrimFromSegments(nextSegs);
    setSelectedVideoSegmentIdx(targetIdx);
    setIsVideoBladeMode(false);
    showToast(
      `✂️ Cut video at ${cutSourceSec.toFixed(1)}s → Part #${targetIdx + 1} (${leftPart.start.toFixed(1)}s–${leftPart.end.toFixed(1)}s) & Part #${targetIdx + 2} (${rightPart.start.toFixed(1)}s–${rightPart.end.toFixed(1)}s). Click unwanted part & press 🗑️ Delete!`
    );
  };

  const handleBladeVideoAtTime = (explicitTimelineSec?: number) => {
    if (visualClips.length === 0) {
      showToast("Upload a video clip first to use the Blade tool");
      return;
    }
    const segs = [...effectiveVideoSegments];
    const speed = Math.max(0.5, clipSpeed || 1.0);
    const totalKeptTimelineDur = Math.max(
      0.5,
      segs.reduce((acc, s) => acc + Math.max(0.2, (s.end - s.start) / speed), 0)
    );
    const hasActiveAudioPlayhead = (audioRef.current?.currentTime ?? currentAudioTime ?? 0) > 0.15;
    const rawPlayhead =
      explicitTimelineSec !== undefined
        ? explicitTimelineSec
        : hasActiveAudioPlayhead
          ? Number((audioRef.current?.currentTime ?? currentAudioTime ?? 0).toFixed(2))
          : Number((previewVideoRef.current?.currentTime ?? 0).toFixed(2));
    const loopOffset = ((rawPlayhead % totalKeptTimelineDur) + totalKeptTimelineDur) % totalKeptTimelineDur;

    let accum = 0;
    let targetIdx = selectedVideoSegmentIdx !== null && segs[selectedVideoSegmentIdx] ? selectedVideoSegmentIdx : 0;
    let cutSourceSec: number | null = null;

    for (let i = 0; i < segs.length; i++) {
      const segDurTimeline = Math.max(0.2, (segs[i].end - segs[i].start) / speed);
      if (loopOffset >= accum + 0.15 && loopOffset <= accum + segDurTimeline - 0.15) {
        targetIdx = i;
        cutSourceSec = Number((segs[i].start + (loopOffset - accum) * speed).toFixed(2));
        break;
      }
      accum += segDurTimeline;
    }

    const chosenSeg = segs[targetIdx] || segs[0];
    if (
      cutSourceSec === null ||
      cutSourceSec <= chosenSeg.start + 0.25 ||
      cutSourceSec >= chosenSeg.end - 0.25
    ) {
      const liveVidTime = previewVideoRef.current?.currentTime ?? 0;
      if (liveVidTime > chosenSeg.start + 0.25 && liveVidTime < chosenSeg.end - 0.25) {
        cutSourceSec = Number(liveVidTime.toFixed(2));
      } else {
        cutSourceSec = Number(((chosenSeg.start + chosenSeg.end) / 2).toFixed(2));
      }
    }

    handleBladeVideoSegmentAtSourceTime(targetIdx, cutSourceSec);
  };

  const handleDeleteVideoSegment = (explicitIdx?: number) => {
    if (visualClips.length === 0) {
      showToast("No main video clip to delete");
      return;
    }
    const segs = [...effectiveVideoSegments];
    const targetIdx =
      explicitIdx !== undefined
        ? explicitIdx
        : selectedVideoSegmentIdx !== null && segs[selectedVideoSegmentIdx]
          ? selectedVideoSegmentIdx
          : 0;

    if (segs.length <= 1) {
      handleRemoveVisualClip(0);
      return;
    }

    const removed = segs[targetIdx];
    const nextSegs = segs.filter((_, i) => i !== targetIdx);
    syncTrimFromSegments(nextSegs);
    const nextSel = Math.min(targetIdx, nextSegs.length - 1);
    setSelectedVideoSegmentIdx(nextSel);
    setIsVideoBladeMode(false);
    if (previewVideoRef.current && nextSegs[nextSel]) {
      previewVideoRef.current.currentTime = nextSegs[nextSel].start;
    }
    showToast(
      `🗑️ Deleted video part #${targetIdx + 1} (${removed.start.toFixed(1)}s–${removed.end.toFixed(1)}s)`
    );
  };

  const handleRestoreFullVideo = () => {
    setVideoSegments([]);
    setSelectedVideoSegmentIdx(null);
    setClipTrimStart(0);
    setClipTrimEnd(0);
    showToast("🔄 Restored full original main video");
  };

  const handleMoveClip = (index: number, direction: -1 | 1) => {
    userModifiedMediaRef.current = true;
    setVisualClips((prev) => {
      const next = [...prev];
      const target = index + direction;
      if (target < 0 || target >= next.length) return prev;
      const temp = next[index];
      next[index] = next[target];
      next[target] = temp;
      return next;
    });
  };

  const handleTimelineChange = (
    idx: number,
    field: "start" | "end" | "text",
    value: string
  ) => {
    setEditableTimeline((prev) =>
      prev.map((item, i) => {
        if (i !== idx) return item;
        if (field === "text") {
          return {
            ...item,
            text: value,
            words: buildWordTimingsForLine(value, item.start, item.end, item),
          };
        }
        const num = parseFloat(value);
        const parsed = isNaN(num) ? 0 : Number(num.toFixed(2));
        const nextStart = field === "start" ? parsed : item.start;
        const nextEnd = field === "end" ? parsed : item.end;
        return {
          ...item,
          start: nextStart,
          end: nextEnd,
          words: buildWordTimingsForLine(item.text, nextStart, nextEnd, item),
        };
      })
    );
  };

  const handleAddCaptionLine = () => {
    const start = Number((currentAudioTime || 0).toFixed(2));
    const end = Number((start + 2.5).toFixed(2));
    const text = "නව පද පේළිය...";
    const newLine: LyricLine = {
      start,
      end,
      text,
      words: buildWordTimingsForLine(text, start, end),
    };
    setEditableTimeline((prev) => {
      const next = [...prev, newLine];
      next.sort((a, b) => a.start - b.start);
      return next;
    });
    setSelectedCaptionIdx(editableTimeline.length);
  };

  const handleDeleteCaptionLine = (idx: number) => {
    setEditableTimeline((prev) => prev.filter((_, i) => i !== idx));
    setSelectedCaptionIdx(null);
  };

  const toggleMasterPlayPause = () => {
    if (activePreviewTab === "rendered" && renderedVideoRef.current) {
      if (renderedVideoRef.current.paused) {
        audioRef.current?.pause();
        renderedVideoRef.current.play().catch(() => { });
      } else {
        renderedVideoRef.current.pause();
      }
      return;
    }
    if (!audioRef.current) return;
    if (audioRef.current.paused) {
      audioRef.current.play().catch(() => { });
      previewVideoRef.current?.play().catch(() => { });
    } else {
      audioRef.current.pause();
    }
  };

  const seekAudioTo = (sec: number) => {
    if (activePreviewTab === "rendered" && renderedVideoRef.current) {
      const dur = renderedVideoRef.current.duration || fetchedSongDuration || 180;
      const clampedVid = Math.max(0, Math.min(sec, dur));
      renderedVideoRef.current.currentTime = clampedVid;
      return;
    }
    const clamped = Math.max(0, Math.min(sec, fetchedSongDuration || 180));
    setCurrentAudioTime(clamped);
    if (audioRef.current) {
      audioRef.current.currentTime = clamped;
      audioRef.current.play().catch(() => { });
    }
    if (previewVideoRef.current && detectedClipDuration > 0) {
      previewVideoRef.current.currentTime = mapTimelineSecToVideoSourceSec(clamped);
      previewVideoRef.current.play().catch(() => { });
    }
  };

  const snapLineStartToCurrentAudio = (idx: number) => {
    const now = Number((audioRef.current?.currentTime || currentAudioTime || 0).toFixed(2));
    setEditableTimeline((prev) =>
      prev.map((item, i) => {
        if (i === idx - 1) {
          const nextPrevEnd = Math.max(item.start + 0.4, Number((now - 0.05).toFixed(2)));
          return {
            ...item,
            end: nextPrevEnd,
            words: buildWordTimingsForLine(item.text, item.start, nextPrevEnd, item),
          };
        }
        if (i === idx) {
          const nextEnd = Math.max(now + 1.2, item.end);
          return {
            ...item,
            start: now,
            end: nextEnd,
            words: buildWordTimingsForLine(item.text, now, nextEnd, item),
          };
        }
        return item;
      })
    );
    showToast(`Snapped line #${idx + 1} start to ${now.toFixed(2)}s`);
  };

  const nudgeCaptionTime = (idx: number, field: "start" | "end", delta: number) => {
    setEditableTimeline((prev) =>
      prev.map((item, i) => {
        if (i !== idx) return item;
        const nextVal = Math.max(0, Number((item[field] + delta).toFixed(2)));
        if (field === "start" && nextVal >= item.end - 0.25) return item;
        if (field === "end" && nextVal <= item.start + 0.25) return item;
        const nextStart = field === "start" ? nextVal : item.start;
        const nextEnd = field === "end" ? nextVal : item.end;
        return {
          ...item,
          start: nextStart,
          end: nextEnd,
          words: buildWordTimingsForLine(item.text, nextStart, nextEnd, item),
        };
      })
    );
  };

  const snapLineEndToCurrentAudio = (idx: number) => {
    const now = Number((audioRef.current?.currentTime || currentAudioTime || 0).toFixed(2));
    setEditableTimeline((prev) =>
      prev.map((item, i) => {
        if (i === idx) {
          const clampedEnd = Math.max(item.start + 0.35, now);
          return {
            ...item,
            end: clampedEnd,
            words: buildWordTimingsForLine(item.text, item.start, clampedEnd, item),
          };
        }
        return item;
      })
    );
    showToast(`Snapped line #${idx + 1} end to ${now.toFixed(2)}s`);
  };

  const handleSplitCaptionAtPlayhead = (explicitIdx?: number) => {
    const playhead = Number((audioRef.current?.currentTime ?? currentAudioTime ?? 0).toFixed(2));
    const cutRawTime = Number((playhead - lyricsOffsetSec).toFixed(2));
    const targetIdx =
      explicitIdx !== undefined
        ? explicitIdx
        : selectedCaptionIdx !== null &&
          editableTimeline[selectedCaptionIdx] &&
          cutRawTime > editableTimeline[selectedCaptionIdx].start + 0.25 &&
          cutRawTime < editableTimeline[selectedCaptionIdx].end - 0.25
          ? selectedCaptionIdx
          : editableTimeline.findIndex(
            (l) => cutRawTime > l.start + 0.25 && cutRawTime < l.end - 0.25
          );

    if (targetIdx === -1 || !editableTimeline[targetIdx]) {
      showToast("Move the playhead inside a caption block (away from edges) to split it.");
      return;
    }

    const line = editableTimeline[targetIdx];
    const ratio = (cutRawTime - line.start) / Math.max(0.1, line.end - line.start);
    const words = line.text.trim().split(/\s+/).filter(Boolean);
    let leftText = line.text.trim();
    let rightText = line.text.trim();
    if (words.length >= 2) {
      const splitWordIdx = Math.max(1, Math.min(words.length - 1, Math.round(words.length * ratio)));
      leftText = words.slice(0, splitWordIdx).join(" ");
      rightText = words.slice(splitWordIdx).join(" ");
    }

    const leftLine: LyricLine = {
      start: line.start,
      end: cutRawTime,
      text: leftText,
      words: buildWordTimingsForLine(leftText, line.start, cutRawTime),
    };
    const rightLine: LyricLine = {
      start: cutRawTime,
      end: line.end,
      text: rightText,
      words: buildWordTimingsForLine(rightText, cutRawTime, line.end),
    };

    setEditableTimeline((prev) => [
      ...prev.slice(0, targetIdx),
      leftLine,
      rightLine,
      ...prev.slice(targetIdx + 1),
    ]);
    setSelectedCaptionIdx(targetIdx + 1);
    showToast(`Split caption #${targetIdx + 1} at ${formatTimecode(playhead)}`);
  };

  const handleDropClips = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDraggingClip(false);
    const dropped = Array.from(e.dataTransfer.files || []).filter((f) =>
      f.type.startsWith("video/") || /\.(mp4|mov|webm|mkv)$/i.test(f.name)
    );
    if (dropped.length > 0) {
      userModifiedMediaRef.current = true;
      setVisualClips((prev) => [...prev, ...dropped]);
      showToast(`Added ${dropped.length} AI video clip(s)`);
    }
  };

  // Keyboard shortcuts for undo/redo, editor transport & micro-timing (disabled while typing)
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) {
          handleRedo();
        } else {
          handleUndo();
        }
        return;
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "y") {
        e.preventDefault();
        handleRedo();
        return;
      }
      if (e.code === "Space") {
        e.preventDefault();
        toggleMasterPlayPause();
      } else if (e.code === "ArrowLeft") {
        e.preventDefault();
        seekAudioTo((audioRef.current?.currentTime ?? currentAudioTime) - (e.shiftKey ? 0.2 : 2));
      } else if (e.code === "ArrowRight") {
        e.preventDefault();
        seekAudioTo((audioRef.current?.currentTime ?? currentAudioTime) + (e.shiftKey ? 0.2 : 2));
      } else if (e.key.toLowerCase() === "s" && selectedCaptionIdx !== null) {
        e.preventDefault();
        snapLineStartToCurrentAudio(selectedCaptionIdx);
      } else if (
        (e.key === "Delete" || e.key === "Backspace") &&
        selectedVideoSegmentIdx !== null &&
        effectiveVideoSegments.length > 1
      ) {
        e.preventDefault();
        handleDeleteVideoSegment(selectedVideoSegmentIdx);
      } else if (e.key.toLowerCase() === "f") {
        e.preventDefault();
        setIsFullscreenModal((prev) => !prev);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  const toggleFullscreenPreview = async () => {
    if (typeof document !== "undefined" && previewStageRef.current) {
      if (!document.fullscreenElement) {
        try {
          await previewStageRef.current.requestFullscreen();
          return;
        } catch {
          // Fallback to modal theater fullscreen if browser blocks native fullscreen
        }
      } else {
        try {
          await document.exitFullscreen();
          return;
        } catch {
          // ignore
        }
      }
    }
    setIsFullscreenModal((prev) => !prev);
  };

  const handleSubmit = async (e?: React.FormEvent, asDraft = false) => {
    if (e) e.preventDefault();
    setUiError(null);

    if (visualClips.length === 0 && !songUrl.trim()) {
      setUiError(
        "Please upload at least one AI animated video clip (or provide a video URL)."
      );
      setActiveSidebarTab("media");
      return;
    }

    setIsSubmitting(true);
    setJob(null);
    setActiveSidebarTab("export");

    try {
      const formData = new FormData();

      if (selectedSingleClipIdx !== null && visualClips[selectedSingleClipIdx]) {
        formData.append("visual_files", visualClips[selectedSingleClipIdx]);
      } else {
        visualClips.forEach((clip) => {
          formData.append("visual_files", clip);
        });
      }

      if (songSourceMode === "url" && songUrl.trim()) {
        formData.append("song_url", songUrl.trim());
      } else if (songSourceMode === "upload" && songFile) {
        formData.append("song_file", songFile);
      }
      if (cachedSongId) {
        formData.append("cached_song_id", cachedSongId);
      }

      const effectiveManual = captionMode === "manual" ? manualLyrics.trim() : "";

      formData.append("song_name", songName.trim());
      formData.append("artist_name", artistName.trim());
      formData.append("manual_lyrics", effectiveManual);
      formData.append("language", language);

      if (editableTimeline.length > 0) {
        formData.append("custom_lines_json", JSON.stringify(editableTimeline));
      }

      formData.append("frame_layout", frameLayout);
      formData.append("color_grade", colorGrade);
      formData.append("style_preset", stylePreset);

      formData.append("font_family", selectedFontFamily);
      formData.append("font_size", String(fontSize));
      formData.append("font_thickness", String(fontThickness));
      formData.append("font_brightness", String(fontBrightness));
      formData.append("primary_color_hex", primaryColorHex);
      formData.append("outline_width", String(outlineWidth));
      formData.append("shadow_depth", String(shadowDepth));
      formData.append("text_pos_y_percent", String(textPosYPercent));
      formData.append("bold_text", String(boldText));
      formData.append("italic_text", String(italicText));
      formData.append("lyrics_offset_sec", String(lyricsOffsetSec));

      formData.append("clip_trim_start", String(clipTrimStart));
      formData.append("clip_trim_end", String(clipTrimEnd));
      if (videoSegments.length > 0) {
        formData.append("clip_segments_json", JSON.stringify(videoSegments));
      }
      formData.append("loop_mode", loopMode);
      formData.append("video_zoom", String(videoZoom));
      formData.append("mirror_flip", String(mirrorFlip));
      formData.append("brightness", String(brightness));
      formData.append("contrast", String(contrast));
      formData.append("saturation", String(saturation));
      formData.append("vignette", String(vignette));

      formData.append("blur_transition_interval", String(blurInterval));
      formData.append("clip_speed", String(clipSpeed));
      formData.append("corner_radius", String(cornerRadius));
      formData.append("watermark_text", watermarkText);
      formData.append("watermark_size", String(watermarkSize));
      formData.append("watermark_pos_x_percent", String(watermarkPosXPercent));
      formData.append("watermark_pos_y_percent", String(watermarkPosYPercent));
      formData.append("audio_start_sec", String(audioStartSec));
      formData.append("audio_max_duration_sec", String(audioMaxDurationSec));
      formData.append("fade_in_out", String(fadeInOut));
      formData.append("audio_fade", String(audioFade));
      formData.append("is_draft", String(asDraft));
      if (activeDraftFilename) {
        formData.append("source_draft_filename", activeDraftFilename);
      }
      formData.append("async_mode", "true");

      const response = await fetch(`${API_BASE}/api/process-video`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        throw new Error(errData.detail || `Server responded with ${response.status}`);
      }

      const initialJob: JobStatusResponse = await response.json();
      setJob(initialJob);
    } catch (err: any) {
      setUiError(err.message || "Failed to start video rendering.");
      setIsSubmitting(false);
    }
  };

  const handleDownloadLrc = () => {
    if (!job?.lrc_content) return;
    const blob = new Blob([job.lrc_content], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `synced_lyrics_${job.job_id}.lrc`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const previewLines =
    editableTimeline.length > 0
      ? editableTimeline.map((l) => l.text)
      : manualLyrics
        .split("\n")
        .map((l) => l.trim())
        .filter(Boolean);

  const activeSyncedLine: LyricLine | null =
    editableTimeline.length > 0 && (isAudioPlaying || currentAudioTime > 0)
      ? editableTimeline.find(
        (l) =>
          currentAudioTime >= l.start + lyricsOffsetSec &&
          currentAudioTime <= l.end + lyricsOffsetSec
      ) || null
      : null;

  const fallbackPreviewText =
    previewLines[previewLineIndex % Math.max(previewLines.length, 1)] ||
    "ජීවිතය විඳගන්න";

  const isBlurPulseActive =
    blurInterval > 0 &&
    currentAudioTime > 1.0 &&
    currentAudioTime % blurInterval < 0.42;

  const buildLiveCssFilter = () => {
    const b = 1.0 + brightness;
    const c = contrast;
    const s = saturation;
    let baseFilter = `contrast(${c.toFixed(2)}) brightness(${b.toFixed(2)}) saturate(${s.toFixed(2)})`;
    if (colorGrade === "golden_sunset") {
      baseFilter = `contrast(${(1.12 * c).toFixed(2)}) brightness(${(0.98 * b).toFixed(2)}) saturate(${(1.35 * s).toFixed(2)}) sepia(0.18)`;
    } else if (colorGrade === "moody_cinema") {
      baseFilter = `contrast(${(1.24 * c).toFixed(2)}) brightness(${(0.94 * b).toFixed(2)}) saturate(${(1.08 * s).toFixed(2)})`;
    } else if (colorGrade === "dreamy_pastel") {
      baseFilter = `contrast(${(0.95 * c).toFixed(2)}) brightness(${(1.04 * b).toFixed(2)}) saturate(${(1.15 * s).toFixed(2)})`;
    } else if (colorGrade === "black_and_white") {
      baseFilter = `contrast(${(1.15 * c).toFixed(2)}) brightness(${b.toFixed(2)}) grayscale(1)`;
    }
    return isBlurPulseActive ? `${baseFilter} blur(3.5px)` : baseFilter;
  };

  const totalTimelineDuration = Math.max(
    fetchedSongDuration || 0,
    job?.song_duration || 0,
    editableTimeline.length > 0 ? editableTimeline[editableTimeline.length - 1].end + 2 : 0,
    detectedClipDuration || 0,
    30
  );

  // Smooth direct drag-move & edge-resize for caption blocks on Track 1 + ruler scrubbing
  useEffect(() => {
    if (!dragCaptionState && !isScrubbingRuler) return;

    const onPointerMove = (e: PointerEvent) => {
      const trackEl = captionsTrackRef.current;
      if (!trackEl) return;
      const rect = trackEl.getBoundingClientRect();
      if (rect.width <= 0) return;

      if (isScrubbingRuler) {
        const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
        const clamped = Math.max(0, Math.min(ratio * totalTimelineDuration, totalTimelineDuration));
        setCurrentAudioTime(clamped);
        if (audioRef.current) {
          audioRef.current.currentTime = clamped;
        }
        if (previewVideoRef.current && detectedClipDuration > 0) {
          previewVideoRef.current.currentTime = mapTimelineSecToVideoSourceSec(clamped);
        }
        return;
      }

      if (!dragCaptionState) return;
      const dx = e.clientX - dragCaptionState.startX;
      if (!dragCaptionState.moved && Math.abs(dx) < 3) return;

      const deltaSec = (dx / rect.width) * totalTimelineDuration;
      const { mode, idx, origStart, origEnd } = dragCaptionState;
      const origDur = Math.max(0.35, origEnd - origStart);

      if (!dragCaptionState.moved) {
        setDragCaptionState((prev) => (prev ? { ...prev, moved: true } : prev));
      }

      setEditableTimeline((prev) =>
        prev.map((item, i) => {
          if (i !== idx) return item;
          if (mode === "move") {
            const clampedStart = Math.max(
              0,
              Math.min(totalTimelineDuration - origDur, origStart + deltaSec)
            );
            const nextStart = Number(clampedStart.toFixed(2));
            const nextEnd = Number((nextStart + origDur).toFixed(2));
            return {
              ...item,
              start: nextStart,
              end: nextEnd,
              words: buildWordTimingsForLine(item.text, nextStart, nextEnd, item),
            };
          }
          if (mode === "resize-start") {
            const nextStart = Number(
              Math.max(0, Math.min(origEnd - 0.35, origStart + deltaSec)).toFixed(2)
            );
            return {
              ...item,
              start: nextStart,
              words: buildWordTimingsForLine(item.text, nextStart, item.end, item),
            };
          }
          const nextEnd = Number(
            Math.max(origStart + 0.35, Math.min(totalTimelineDuration, origEnd + deltaSec)).toFixed(2)
          );
          return {
            ...item,
            end: nextEnd,
            words: buildWordTimingsForLine(item.text, item.start, nextEnd, item),
          };
        })
      );
    };

    const onPointerUp = () => {
      if (dragCaptionState?.moved) {
        const draggedIdx = dragCaptionState.idx;
        setEditableTimeline((prev) => {
          const target = prev[draggedIdx];
          const next = [...prev].sort((a, b) => a.start - b.start);
          if (target) {
            const newIdx = next.indexOf(target);
            if (newIdx !== -1 && newIdx !== draggedIdx) {
              setTimeout(() => setSelectedCaptionIdx(newIdx), 0);
            }
          }
          return next;
        });
      }
      setDragCaptionState(null);
      setIsScrubbingRuler(false);
    };

    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerUp);
    return () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
      window.removeEventListener("pointercancel", onPointerUp);
    };
  }, [dragCaptionState, isScrubbingRuler, totalTimelineDuration, detectedClipDuration]);

  const effectiveClipLen = Math.max(
    1.0,
    Number(
      effectiveVideoSegments
        .reduce(
          (acc, s) => acc + Math.max(0.2, (s.end - s.start) / Math.max(0.5, clipSpeed || 1.0)),
          0
        )
        .toFixed(2)
    )
  );
  const loopCountOnTimeline = Math.ceil(totalTimelineDuration / effectiveClipLen);

  // Exact timeline timestamps for periodic blur-pulse, fade-in/out & active effects
  const blurPulseWindows: { start: number; end: number }[] = [];
  if (blurInterval > 0 && totalTimelineDuration > 1.0) {
    const maxPulses = Math.min(200, Math.ceil(totalTimelineDuration / blurInterval) + 1);
    for (let n = 0; n <= maxPulses; n++) {
      const winStart = Math.max(1.0, Number((n * blurInterval).toFixed(2)));
      const winEnd = Math.min(
        totalTimelineDuration,
        Number((n * blurInterval + 0.42).toFixed(2))
      );
      if (winEnd > winStart && winStart < totalTimelineDuration) {
        blurPulseWindows.push({ start: winStart, end: winEnd });
      }
    }
  }

  const hasVideoFades = fadeInOut && totalTimelineDuration > 2.5;
  const videoFadeInEnd = hasVideoFades ? 0.6 : 0;
  const videoFadeOutStart = hasVideoFades
    ? Math.max(totalTimelineDuration - 1.1, 1.0)
    : totalTimelineDuration;
  const hasAudioFades = audioFade && totalTimelineDuration > 2.5;
  const audioFadeOutStart = hasAudioFades
    ? Math.max(totalTimelineDuration - 1.2, 1.0)
    : totalTimelineDuration;

  const isFadeInActiveNow = hasVideoFades && currentAudioTime <= videoFadeInEnd;
  const isFadeOutActiveNow = hasVideoFades && currentAudioTime >= videoFadeOutStart;

  const colorGradeLabels: Record<ColorGradePreset, string> = {
    golden_sunset: "Golden Sunset",
    moody_cinema: "Moody Cinema",
    dreamy_pastel: "Dreamy Glow",
    black_and_white: "Black & White",
    original: "Original",
  };
  const stylePresetLabels: Record<StylePreset, string> = {
    rw_aesthetic_sinhala: "RW Aesthetic",
    soft_line_fade: "Soft Fade",
    tiktok_yellow: "TikTok Yellow",
    capcut_white_pop: "CapCut Pop",
    neon_cyan: "Neon Cyan",
  };

  const activeTimelineEffects: {
    id: string;
    label: string;
    tab: SidebarTab;
    badgeClass: string;
  }[] = [
      ...(blurInterval > 0
        ? [
          {
            id: "blur",
            label: `💧 Blur ${blurInterval}s`,
            tab: "effects" as SidebarTab,
            badgeClass: isBlurPulseActive
              ? "border-fuchsia-300 bg-fuchsia-500 text-white shadow-[0_0_10px_rgba(217,70,239,0.75)] animate-pulse"
              : "border-fuchsia-500/50 bg-fuchsia-500/20 text-fuchsia-200 hover:bg-fuchsia-500/30",
          },
        ]
        : []),
      ...(colorGrade !== "original"
        ? [
          {
            id: "grade",
            label: `🎨 ${colorGradeLabels[colorGrade]}`,
            tab: "effects" as SidebarTab,
            badgeClass: "border-amber-500/40 bg-amber-500/15 text-amber-200 hover:bg-amber-500/25",
          },
        ]
        : []),
      ...(vignette
        ? [
          {
            id: "vignette",
            label: "🕶️ Vignette",
            tab: "effects" as SidebarTab,
            badgeClass: "border-zinc-500/50 bg-zinc-800 text-zinc-200 hover:bg-zinc-700",
          },
        ]
        : []),
      ...(hasVideoFades
        ? [
          {
            id: "vfade",
            label: "🌅 Video Fade",
            tab: "effects" as SidebarTab,
            badgeClass: "border-sky-500/40 bg-sky-500/15 text-sky-200 hover:bg-sky-500/25",
          },
        ]
        : []),
      ...(hasAudioFades
        ? [
          {
            id: "afade",
            label: "🔊 Audio Fade",
            tab: "audio" as SidebarTab,
            badgeClass: "border-purple-500/40 bg-purple-500/15 text-purple-200 hover:bg-purple-500/25",
          },
        ]
        : []),
      ...(brightness !== 0 || contrast !== 1 || saturation !== 1
        ? [
          {
            id: "eq",
            label: "🎚️ Color EQ",
            tab: "effects" as SidebarTab,
            badgeClass: "border-amber-500/40 bg-amber-500/15 text-amber-200 hover:bg-amber-500/25",
          },
        ]
        : []),
      ...(videoZoom > 1.01
        ? [
          {
            id: "zoom",
            label: `🔍 Zoom ${videoZoom.toFixed(2)}x`,
            tab: "media" as SidebarTab,
            badgeClass: "border-cyan-500/40 bg-cyan-500/15 text-cyan-200 hover:bg-cyan-500/25",
          },
        ]
        : []),
      ...(mirrorFlip
        ? [
          {
            id: "mirror",
            label: "🪞 Mirror",
            tab: "media" as SidebarTab,
            badgeClass: "border-cyan-500/40 bg-cyan-500/15 text-cyan-200 hover:bg-cyan-500/25",
          },
        ]
        : []),
      ...(clipSpeed !== 1.0
        ? [
          {
            id: "speed",
            label: `⚡ ${clipSpeed}x`,
            tab: "media" as SidebarTab,
            badgeClass: "border-cyan-500/40 bg-cyan-500/15 text-cyan-200 hover:bg-cyan-500/25",
          },
        ]
        : []),
      ...(visualClips.length > 0 && loopCountOnTimeline > 1
        ? [
          {
            id: "loop",
            label:
              loopMode === "crossfade"
                ? "✨ xfade Loop"
                : loopMode === "pingpong"
                  ? "🔄 Boomerang"
                  : "🔁 Smooth Loop",
            tab: "media" as SidebarTab,
            badgeClass: "border-sky-500/40 bg-sky-500/15 text-sky-200 hover:bg-sky-500/25",
          },
        ]
        : []),
      {
        id: "captionfx",
        label: `✍️ ${stylePresetLabels[stylePreset]}`,
        tab: "fonts" as SidebarTab,
        badgeClass: "border-emerald-500/40 bg-emerald-500/15 text-emerald-200 hover:bg-emerald-500/25",
      },
    ];

  const SIDEBAR_ITEMS: { id: SidebarTab; icon: React.ReactNode; label: string; badge?: string }[] = [
    {
      id: "media",
      icon: <Film className="h-4 w-4" />,
      label: "Media & Loop",
      badge: visualClips.length > 0 ? String(visualClips.length) : undefined,
    },
    {
      id: "audio",
      icon: <Music className="h-4 w-4" />,
      label: "Song & Audio",
      badge: cachedSongId ? "✓" : undefined,
    },
    {
      id: "captions",
      icon: <MessageSquareQuote className="h-4 w-4" />,
      label: "Captions Sync",
      badge: editableTimeline.length > 0 ? String(editableTimeline.length) : captionMode === "auto" ? "Auto" : undefined,
    },
    {
      id: "fonts",
      icon: <Type className="h-4 w-4" />,
      label: "Sinhala Fonts",
    },
    {
      id: "effects",
      icon: <Palette className="h-4 w-4" />,
      label: "Canvas & Grade",
    },
    {
      id: "export",
      icon: <Rocket className="h-4 w-4" />,
      label: "Render & Export",
      badge: job ? `${job.progress}%` : undefined,
    },
    {
      id: "videos",
      icon: <Video className="h-4 w-4" />,
      label: "Rendered Videos",
      badge: renderedVideos.length > 0 ? String(renderedVideos.length) : undefined,
    },
  ];

  const isFontNew = (f: FontEntry) =>
    Boolean(f.is_new) || newlyAddedFonts.includes(f.family);

  // Order Font Gallery: 1) Starred fonts at very top, 2) Newly added fonts next, 3) Remaining fonts
  const sortedInstalledFonts = [...installedFonts].sort((a, b) => {
    const aStar = starredFonts.indexOf(a.family);
    const bStar = starredFonts.indexOf(b.family);
    if (aStar !== -1 && bStar === -1) return -1;
    if (aStar === -1 && bStar !== -1) return 1;
    if (aStar !== -1 && bStar !== -1) return aStar - bStar;
    const aNew = isFontNew(a);
    const bNew = isFontNew(b);
    if (aNew && !bNew) return -1;
    if (!aNew && bNew) return 1;
    if (aNew && bNew) return (b.added_at || 0) - (a.added_at || 0);
    return 0;
  });

  const activeRenderedPlaybackUrl =
    selectedRenderedVideoUrl || job?.video_url || renderedVideos[0]?.stream_url || null;

  const playRenderedVideoInStage = (streamUrl: string, label: string) => {
    audioRef.current?.pause();
    setRenderedVideoError(null);
    setSelectedRenderedVideoUrl(streamUrl);
    setSelectedRenderedVideoLabel(label);
    setActivePreviewTab("rendered");
  };

  const WORKFLOW_STEPS: { num: number; label: string; done: boolean; tab: SidebarTab }[] = [
    { num: 1, label: "AI Video", done: visualClips.length > 0, tab: "media" },
    { num: 2, label: "Song Audio", done: Boolean(cachedSongId || songUrl.trim() || songFile), tab: "audio" },
    { num: 3, label: "Sinhala Sync", done: editableTimeline.length > 0, tab: "captions" },
    { num: 4, label: "Export MP4", done: Boolean(job?.video_url), tab: "export" },
  ];

  const toAbsoluteFontUrl = (rawUrl: string) =>
    rawUrl.startsWith("http") || rawUrl.startsWith("blob:")
      ? rawUrl
      : `${API_BASE}${rawUrl}`;

  const dynamicFontFaceCss = installedFonts
    .filter((f) => Boolean(f.regular_url || f.bold_url || f.url))
    .flatMap((f) => {
      const regUrl = f.regular_url || "";
      const boldUrl = f.bold_url || "";
      const mkRule = (rawUrl: string, weightSpec: string) => {
        const fullUrl = toAbsoluteFontUrl(rawUrl);
        const fmt = fullUrl.toLowerCase().endsWith(".otf") ? "opentype" : "truetype";
        return `@font-face { font-family: "${f.family}"; src: url("${fullUrl}") format("${fmt}"); font-weight: ${weightSpec}; font-style: normal; font-display: swap; }`;
      };
      if (regUrl && boldUrl && regUrl !== boldUrl) {
        return [mkRule(regUrl, "400"), mkRule(boldUrl, "700")];
      }
      const singleUrl = regUrl || boldUrl || f.url!;
      return [mkRule(singleUrl, "100 900")];
    })
    .join("\n");

  const baseCaptionColorHex =
    stylePreset === "tiktok_yellow" && primaryColorHex.toUpperCase() === "#FFFFFF"
      ? "#FACC15"
      : stylePreset === "neon_cyan" && primaryColorHex.toUpperCase() === "#FFFFFF"
        ? "#22D3EE"
        : primaryColorHex;

  const applyFontBrightnessToHex = (hexStr: string, fb: number): string => {
    const cleaned = (hexStr || "#FFFFFF").trim().replace(/^#/, "");
    if (cleaned.length !== 6) return "#FFFFFF";
    let r = parseInt(cleaned.slice(0, 2), 16);
    let g = parseInt(cleaned.slice(2, 4), 16);
    let b = parseInt(cleaned.slice(4, 6), 16);
    if (isNaN(r) || isNaN(g) || isNaN(b)) return "#FFFFFF";
    const clampedFb = Math.max(0.4, Math.min(2.0, fb));
    if (clampedFb < 1.0) {
      r = Math.round(Math.max(0, Math.min(255, r * clampedFb)));
      g = Math.round(Math.max(0, Math.min(255, g * clampedFb)));
      b = Math.round(Math.max(0, Math.min(255, b * clampedFb)));
    } else if (clampedFb > 1.0) {
      const lift = Math.min(1.0, (clampedFb - 1.0) * 0.65);
      r = Math.round(Math.max(0, Math.min(255, r + (255 - r) * lift)));
      g = Math.round(Math.max(0, Math.min(255, g + (255 - g) * lift)));
      b = Math.round(Math.max(0, Math.min(255, b + (255 - b) * lift)));
    }
    return `#${r.toString(16).padStart(2, "0")}${g.toString(16).padStart(2, "0")}${b.toString(16).padStart(2, "0")}`.toUpperCase();
  };

  const effectiveLiveCaptionColor = applyFontBrightnessToHex(baseCaptionColorHex, fontBrightness);

  const isCapCutPopPreset =
    stylePreset === "tiktok_yellow" ||
    stylePreset === "capcut_white_pop" ||
    stylePreset === "neon_cyan";

  const referenceRenderWidth = frameLayout === "fullscreen_vertical" ? 1080 : 920;
  const liveCanvasScale =
    previewCardWidth > 0
      ? previewCardWidth / referenceRenderWidth
      : isFullscreenModal
        ? 0.45
        : 0.23;

  const activeFontEntry: FontEntry | undefined = installedFonts.find(
    (f) => f.family === selectedFontFamily
  );

  // Match backend resolve_ass_font_spec: single-weight custom Regular fonts don't synthesize bold
  const hasRealBoldFace =
    !activeFontEntry ||
    activeFontEntry.source === "system" ||
    Boolean(activeFontEntry.bold_url) ||
    (activeFontEntry.subfamily || "").toLowerCase().includes("bold") ||
    activeFontEntry.family.toLowerCase().includes("bold");
  const effectiveLiveFontWeight =
    boldText && hasRealBoldFace && !selectedFontFamily.toLowerCase().includes("regular")
      ? 700
      : 400;

  const liveCaptionFontSizePx = Math.max(
    6,
    Number((fontSize * liveCanvasScale).toFixed(2))
  );
  const showLiveHardOutline = isCapCutPopPreset || outlineWidth > 2.2;
  const scaledShadowPx = Math.max(
    1,
    Number((shadowDepth * liveCanvasScale).toFixed(1))
  );
  const scaledShadowBlurPx = Math.max(
    1.5,
    Number((shadowDepth * 1.4 * liveCanvasScale).toFixed(1))
  );
  const baseCaptionShadow = showLiveHardOutline
    ? `0 ${scaledShadowPx}px ${scaledShadowBlurPx}px rgba(0,0,0,0.95), -1px -1px 0 rgba(0,0,0,0.85), 1px -1px 0 rgba(0,0,0,0.85), -1px 1px 0 rgba(0,0,0,0.85), 1px 1px 0 rgba(0,0,0,0.85)`
    : `0 ${scaledShadowPx}px ${scaledShadowBlurPx}px rgba(0,0,0,0.88)`;
  const fontGlowBoost = Math.max(0, Math.min(1.0, fontBrightness - 1.0));
  const liveCaptionGlowShadow =
    fontGlowBoost > 0.02
      ? `0 0 ${Math.max(2, Number(((3 + fontGlowBoost * 9) * liveCanvasScale * 2.2).toFixed(1)))}px ${effectiveLiveCaptionColor}, 0 0 ${Math.max(4, Number(((6 + fontGlowBoost * 16) * liveCanvasScale * 2.2).toFixed(1)))}px rgba(255,255,255,${(0.25 + fontGlowBoost * 0.55).toFixed(2)}), `
      : "";
  const liveCaptionTextShadow = `${liveCaptionGlowShadow}${baseCaptionShadow}`;
  const liveCaptionPaddingInline =
    frameLayout === "fullscreen_vertical"
      ? Math.max(12, Math.round(110 * liveCanvasScale))
      : Math.max(4, Math.round(30 * liveCanvasScale));
  const liveCaptionTopPct =
    frameLayout === "fullscreen_vertical"
      ? 8.333 + (textPosYPercent / 100) * 83.333
      : textPosYPercent;

  const effectiveFontSampleText =
    fontPreviewSampleText.trim() ||
    activeSyncedLine?.text ||
    fallbackPreviewText ||
    "මගේ අභියස ලෝකයක් තියා";

  const formatActiveFontText = (text: string): string =>
    formatTextForFontEntry(text, activeFontEntry);

  const editableUiFontFamily =
    activeFontEntry?.encoding === "wijesekara_fm" ||
      activeFontEntry?.encoding === "isi"
      ? '"Sinhala Sangam MN", "Nirmala UI", "Iskoola Pota", "Noto Sans Sinhala", sans-serif'
      : getCssFontFamily(selectedFontFamily);

  const liveCaptionTextStroke =
    fontThickness > 0
      ? `${(fontThickness * 2 * liveCanvasScale).toFixed(2)}px ${effectiveLiveCaptionColor}`
      : undefined;

  const ffmpegPreviewImgUrl = `${API_BASE}/api/fonts/render-preview?font_family=${encodeURIComponent(
    selectedFontFamily
  )}&sample_text=${encodeURIComponent(
    effectiveFontSampleText
  )}&font_size=${fontSize}&font_thickness=${fontThickness}&font_brightness=${fontBrightness}&primary_color_hex=${encodeURIComponent(
    primaryColorHex
  )}&outline_width=${outlineWidth}&shadow_depth=${shadowDepth}&bold_text=${boldText}&italic_text=${italicText}`;

  return (
    <div className="flex min-h-screen lg:h-screen flex-col bg-zinc-950 text-zinc-100 lg:overflow-hidden">
      {/* Dynamic @font-face declarations for all installed .TTF / .OTF Sinhala fonts */}
      <style dangerouslySetInnerHTML={{ __html: dynamicFontFaceCss }} />

      {/* Hidden Master Audio Element for Synchronized Preview & Timeline Playback */}
      {songPreviewUrl && (
        <audio
          ref={audioRef}
          src={songPreviewUrl}
          onTimeUpdate={(e) => setCurrentAudioTime(e.currentTarget.currentTime)}
          onPlay={() => setIsAudioPlaying(true)}
          onPause={() => setIsAudioPlaying(false)}
          onEnded={() => {
            setIsAudioPlaying(false);
            setIsTapSyncActive(false);
          }}
          className="hidden"
        />
      )}

      {/* FLOATING STUDIO TOAST NOTIFICATION */}
      {studioToast && (
        <div className="animate-studio-pop pointer-events-none fixed bottom-20 left-1/2 z-50 -translate-x-1/2 rounded-full border border-amber-400/40 bg-zinc-900/95 px-4 py-1.5 text-xs font-semibold text-amber-300 shadow-2xl backdrop-blur">
          <span className="inline-flex items-center gap-1.5">
            <Sparkles className="h-3.5 w-3.5 text-amber-400" />
            {studioToast}
          </span>
        </div>
      )}

      {/* TOP STUDIO HEADER BAR */}
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-zinc-800/90 bg-gradient-to-b from-zinc-900/95 to-zinc-950/95 px-4 py-2 shadow-[0_4px_20px_rgba(0,0,0,0.45)] backdrop-blur-md">
        <div className="flex items-center gap-3">
          <div className="relative flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-amber-300 via-amber-400 to-orange-500 font-black text-black shadow-lg shadow-amber-500/25 ring-1 ring-amber-200/40">
            TK
            <span
              title={engineHealth.ready ? "Backend Render Engine Online" : "Backend Render Engine Offline"}
              className={`absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full ring-2 ring-zinc-950 ${engineHealth.ready ? "bg-emerald-400" : "bg-rose-500"
                }`}
            />
          </div>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-sm font-extrabold tracking-tight text-white sm:text-base">
                TikTok Sinhala Lyrics <span className="text-amber-400">Studio Pro</span>
              </h1>
              <span className="hidden items-center gap-1.5 rounded-full border border-zinc-700/80 bg-zinc-900/90 px-2.5 py-0.5 text-[10px] font-semibold text-zinc-300 sm:inline-flex">
                <span
                  className={`h-1.5 w-1.5 rounded-full ${engineHealth.ready ? "bg-emerald-400 animate-pulse" : "bg-rose-400"
                    }`}
                />
                {engineHealth.ready ? "Engine Ready • BT.709" : "Backend Offline"}
              </span>
              {songName.trim() && (
                <span className="hidden max-w-[240px] truncate rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-0.5 text-[10px] font-semibold text-emerald-300 md:inline-block">
                  {songName}{artistName.trim() ? ` — ${artistName}` : ""}
                  {detectedLyricsSource ? ` • ${detectedLyricsSource}` : ""}
                </span>
              )}
            </div>
            <p className="text-[11px] text-zinc-400">
              Shazam Song Catch • Unicode &amp; Wijesekara (<code className="text-amber-300/90">FM</code>) • HarfBuzz 9:16 Mastering
            </p>
          </div>
        </div>

        {/* Interactive 4-Step Studio Workflow Stepper */}
        <div className="hidden items-center gap-1 rounded-xl border border-zinc-800/90 bg-zinc-900/70 p-1 xl:flex">
          {WORKFLOW_STEPS.map((st, idx) => {
            const isTabActive = activeSidebarTab === st.tab;
            return (
              <React.Fragment key={st.tab}>
                <button
                  type="button"
                  onClick={() => setActiveSidebarTab(st.tab)}
                  className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[11px] font-semibold transition ${isTabActive
                    ? "bg-zinc-800 text-amber-300 shadow-sm"
                    : st.done
                      ? "text-emerald-300 hover:bg-zinc-800/60"
                      : "text-zinc-400 hover:bg-zinc-800/50 hover:text-zinc-200"
                    }`}
                >
                  <span
                    className={`flex h-4 w-4 items-center justify-center rounded-full text-[9px] font-bold ${st.done
                      ? "bg-emerald-500 text-black"
                      : isTabActive
                        ? "bg-amber-400 text-black"
                        : "bg-zinc-800 text-zinc-400"
                      }`}
                  >
                    {st.done ? <Check className="h-2.5 w-2.5 stroke-[3]" /> : st.num}
                  </span>
                  <span>{st.label}</span>
                </button>
                {idx < WORKFLOW_STEPS.length - 1 && (
                  <span className="text-[10px] text-zinc-700">/</span>
                )}
              </React.Fragment>
            );
          })}
        </div>

        {/* Top-Right Primary Action Buttons */}
        <div className="flex items-center gap-2">
          <div className="inline-flex items-center rounded-xl border border-zinc-700/80 bg-zinc-900/90 p-0.5">
            <button
              type="button"
              onClick={handleUndo}
              disabled={undoCount === 0}
              title={`Undo (Ctrl/Cmd + Z)${undoCount > 0 ? ` • ${undoCount} step(s)` : ""}`}
              className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-semibold text-zinc-200 transition hover:bg-zinc-800 hover:text-amber-300 disabled:cursor-not-allowed disabled:opacity-35"
            >
              <Undo2 className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Undo</span>
            </button>
            <span className="h-3.5 w-px bg-zinc-800" />
            <button
              type="button"
              onClick={handleRedo}
              disabled={redoCount === 0}
              title={`Redo (Ctrl/Cmd + Shift + Z)${redoCount > 0 ? ` • ${redoCount} step(s)` : ""}`}
              className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-semibold text-zinc-200 transition hover:bg-zinc-800 hover:text-amber-300 disabled:cursor-not-allowed disabled:opacity-35"
            >
              <Redo2 className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Redo</span>
            </button>
          </div>

          <button
            type="button"
            onClick={handleResetProject}
            title="Start a new project (clears auto-saved clips, song, and captions)"
            className="inline-flex items-center gap-1 rounded-xl border border-zinc-700/80 bg-zinc-900/90 px-2.5 py-1.5 text-[11px] font-semibold text-zinc-300 transition hover:border-rose-500/50 hover:text-rose-300"
          >
            <RotateCcw className="h-3 w-3" />
            New
          </button>

          {isFetchingSong || isSyncingLyrics ? (
            <button
              type="button"
              onClick={handleStopFetchSong}
              title="Stop fetching/syncing song"
              className="inline-flex items-center gap-1.5 rounded-xl border border-rose-500/50 bg-rose-500/20 px-3 py-1.5 text-xs font-bold text-rose-200 transition hover:bg-rose-500/35"
            >
              <span className="h-2 w-2 rounded-xs bg-rose-400 animate-pulse" />
              ⏹ Stop Fetch
            </button>
          ) : (
            <button
              type="button"
              onClick={() => handleFetchSong()}
              className="inline-flex items-center gap-1.5 rounded-xl border border-amber-400/40 bg-amber-400/10 px-3 py-1.5 text-xs font-bold text-amber-300 transition hover:bg-amber-400/20"
            >
              <Zap className="h-3.5 w-3.5" />
              Fetch &amp; Sync Song
            </button>
          )}

          <button
            type="button"
            disabled={isSubmitting}
            onClick={() => handleSubmit(undefined, true)}
            title="Save rendered video into the draft folder inside your video save folder"
            className="inline-flex items-center gap-1.5 rounded-xl border border-violet-400/40 bg-violet-500/15 px-3 py-1.5 text-xs font-bold text-violet-200 transition hover:bg-violet-500/25 disabled:opacity-50"
          >
            📝 Save Draft
          </button>

          <button
            type="button"
            disabled={isSubmitting}
            onClick={() => handleSubmit(undefined, false)}
            className="inline-flex items-center gap-1.5 rounded-xl bg-gradient-to-r from-amber-400 to-orange-400 px-4 py-1.5 text-xs font-extrabold text-black shadow-lg shadow-amber-500/20 transition hover:brightness-105 disabled:opacity-50"
          >
            {isSubmitting ? (
              <>
                <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-black border-t-transparent" />
                Rendering ({job?.progress || 5}%)
              </>
            ) : (
              <>
                <Rocket className="h-3.5 w-3.5" />
                Render Video
              </>
            )}
          </button>
        </div>
      </header>

      {/* MIDDLE WORKSPACE: LEFT SIDEBAR (TOOLS & ACTIONS) + CENTER VIDEO PREVIEW STAGE */}
      <div className="flex flex-1 flex-col lg:flex-row lg:overflow-hidden">
        {/* 1. FAR-LEFT ICON NAVIGATION RAIL */}
        <nav className="flex shrink-0 flex-row overflow-x-auto border-b border-zinc-800/90 bg-zinc-950/95 px-2 py-1.5 lg:w-[84px] lg:flex-col lg:items-center lg:gap-1.5 lg:border-b-0 lg:border-r lg:px-2 lg:py-3">
          {SIDEBAR_ITEMS.map((item) => {
            const active = activeSidebarTab === item.id;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => setActiveSidebarTab(item.id)}
                className={`group relative flex shrink-0 flex-col items-center justify-center rounded-xl px-3 py-2.5 text-center transition-all lg:w-full ${active
                  ? "bg-gradient-to-b from-amber-400 to-amber-500 text-black font-bold shadow-md shadow-amber-500/20 ring-1 ring-amber-300/60"
                  : "text-zinc-400 hover:bg-zinc-900 hover:text-zinc-100"
                  }`}
              >
                {active && (
                  <span className="hidden lg:block absolute left-0 top-2.5 bottom-2.5 w-1 rounded-r-full bg-black/50" />
                )}
                <span className="text-base leading-none transition-transform group-hover:scale-105">{item.icon}</span>
                <span className="mt-1 text-[10px] tracking-tight leading-tight">{item.label}</span>
                {item.badge && (
                  <span
                    className={`absolute right-1.5 top-1 rounded-full px-1.5 py-0.2 text-[9px] font-bold ${active ? "bg-black text-amber-300" : "bg-amber-400/20 text-amber-300"
                      }`}
                  >
                    {item.badge}
                  </span>
                )}
              </button>
            );
          })}
        </nav>

        {/* 2. LEFT ACTION INSPECTOR SIDEBAR PANEL */}
        <aside className="w-full shrink-0 border-b border-zinc-800/90 bg-zinc-900/65 p-4 lg:w-[410px] xl:w-[440px] lg:overflow-y-auto lg:border-b-0 lg:border-r backdrop-blur-sm">
          {uiError && (
            <div className="mb-4 rounded-xl border border-red-500/40 bg-red-500/10 px-3.5 py-2.5 text-xs text-red-300">
              {uiError}
            </div>
          )}

          {/* TAB 1: MEDIA & VIDEO LOOP TOOLS */}
          {activeSidebarTab === "media" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-sm font-bold text-white">
                    🎬 AI Video Clips &amp; Smooth Replay
                  </h2>
                  <p className="text-xs text-zinc-400">
                    If your video is shorter than the song, it smoothly replays to match the full song duration!
                  </p>
                </div>
                <span className="rounded-md bg-amber-400/15 px-2 py-0.5 text-xs font-bold text-amber-300">
                  {visualClips.length} clip(s)
                </span>
              </div>

              <label
                onDragOver={(e) => {
                  e.preventDefault();
                  setIsDraggingClip(true);
                }}
                onDragLeave={() => setIsDraggingClip(false)}
                onDrop={handleDropClips}
                className={`flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed px-4 py-6 text-center transition-all ${isDraggingClip
                  ? "border-amber-400 bg-amber-400/10 scale-[1.01]"
                  : "border-zinc-700/80 bg-zinc-950/80 hover:border-amber-400/70 hover:bg-zinc-900/60"
                  }`}
              >
                <Upload className="mb-2 h-5 w-5 text-amber-400" />
                <span className="text-xs font-bold text-amber-300">
                  Drag &amp; Drop or Upload AI Video Clip(s)
                </span>
                <span className="mt-1 text-[11px] text-zinc-400">
                  MP4, MOV, WebM • Short 4s–10s clips smoothly replay to match full song length
                </span>
                <input
                  type="file"
                  accept="video/*"
                  multiple
                  onChange={(e) => handleAddVisualClips(e.target.files)}
                  className="hidden"
                />
              </label>

              {visualClips.length > 0 && (
                <div className="space-y-2">
                  {visualClips.length > 1 && (
                    <div className="rounded-xl border border-amber-400/35 bg-amber-400/10 p-2 text-[11px] space-y-1.5">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-bold text-amber-200">
                          {selectedSingleClipIdx === null
                            ? `✨ Multi-Video Mix & Blend (${visualClips.length} clips across song timeline)`
                            : `🔁 Single-Clip Repeat Mode (Only Clip #${selectedSingleClipIdx + 1})`}
                        </span>
                        {selectedSingleClipIdx !== null && (
                          <button
                            type="button"
                            onClick={() => {
                              setSelectedSingleClipIdx(null);
                              showToast(
                                `✨ Mixing & blending all ${visualClips.length} videos across the song timeline`
                              );
                            }}
                            className="shrink-0 rounded-lg bg-amber-400 px-2 py-0.5 text-[10px] font-extrabold text-black hover:bg-amber-300"
                          >
                            ✨ Mix All {visualClips.length} Clips
                          </button>
                        )}
                      </div>
                      <p className="text-[10px] text-amber-100/80">
                        {selectedSingleClipIdx === null
                          ? "All uploaded videos will smoothly mix & crossfade-blend across the song timeline. Click '🔁 Repeat Only This' on any single video below if you want only that video to loop."
                          : `Only Clip #${selectedSingleClipIdx + 1} (${visualClips[selectedSingleClipIdx]?.name}) will repeat across the entire song duration.`}
                      </p>
                    </div>
                  )}
                  <div className="space-y-1.5 max-h-44 overflow-y-auto pr-1">
                    {visualClips.map((clip, index) => {
                      const isOnlySelected = selectedSingleClipIdx === index;
                      const isMutedBySingleSelect =
                        selectedSingleClipIdx !== null && selectedSingleClipIdx !== index;
                      return (
                        <div
                          key={`${clip.name}-${index}`}
                          className={`flex items-center justify-between rounded-xl border px-3 py-2 text-xs transition ${isOnlySelected
                            ? "border-amber-400 bg-amber-400/15"
                            : isMutedBySingleSelect
                              ? "border-zinc-800/60 bg-zinc-950/50 opacity-55"
                              : "border-zinc-800 bg-zinc-950"
                            }`}
                        >
                          <div className="flex items-center gap-2 min-w-0">
                            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-amber-400/20 font-mono text-[10px] font-bold text-amber-300">
                              {index + 1}
                            </span>
                            <span className="truncate font-medium text-zinc-200">{clip.name}</span>
                            <span className="shrink-0 font-mono text-[10px] text-zinc-500">
                              {(clip.size / (1024 * 1024)).toFixed(1)}MB
                            </span>
                          </div>
                          <div className="flex items-center gap-1">
                            {visualClips.length > 1 && (
                              <button
                                type="button"
                                onClick={() => {
                                  if (isOnlySelected) {
                                    setSelectedSingleClipIdx(null);
                                    showToast(
                                      `✨ Mixing & blending all ${visualClips.length} clips across song timeline`
                                    );
                                  } else {
                                    setSelectedSingleClipIdx(index);
                                    showToast(
                                      `🔁 Repeating only Clip #${index + 1} (${clip.name}) across song duration`
                                    );
                                  }
                                }}
                                title={
                                  isOnlySelected
                                    ? "Click to switch back to Mix & Blend All Clips"
                                    : "Click to repeat ONLY this clip for the entire song duration"
                                }
                                className={`inline-flex items-center gap-1 rounded-lg border px-2 py-0.5 text-[10px] font-bold transition ${isOnlySelected
                                  ? "border-amber-300 bg-amber-400 text-black"
                                  : "border-amber-400/35 bg-amber-400/10 text-amber-300 hover:bg-amber-400/25"
                                  }`}
                              >
                                {isOnlySelected ? "🔁 Repeating Only This" : "🔁 Repeat Only"}
                              </button>
                            )}
                            {index === 0 && (
                              <button
                                type="button"
                                onClick={() => handleBladeVideoAtTime()}
                                title="Blade / Split main video at current playhead"
                                className="inline-flex items-center gap-1 rounded-lg border border-sky-500/40 bg-sky-500/15 px-2 py-0.5 text-[10px] font-bold text-sky-300 hover:bg-sky-500/25"
                              >
                                <Scissors className="h-3 w-3" />
                                Blade
                              </button>
                            )}
                            <button
                              type="button"
                              onClick={() => handleMoveClip(index, -1)}
                              disabled={index === 0}
                              className="rounded border border-zinc-800 px-1.5 py-0.5 text-zinc-400 hover:text-white disabled:opacity-30"
                            >
                              ↑
                            </button>
                            <button
                              type="button"
                              onClick={() => handleMoveClip(index, 1)}
                              disabled={index === visualClips.length - 1}
                              className="rounded border border-zinc-800 px-1.5 py-0.5 text-zinc-400 hover:text-white disabled:opacity-30"
                            >
                              ↓
                            </button>
                            <button
                              type="button"
                              onClick={() => handleRemoveVisualClip(index)}
                              title="Delete this video clip"
                              className="ml-0.5 inline-flex items-center gap-1 rounded-lg border border-red-500/35 bg-red-500/10 px-2 py-0.5 text-[10px] font-bold text-red-300 hover:bg-red-500/25"
                            >
                              <Trash2 className="h-3 w-3" />
                              Delete
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  {/* Interactive Main Video Blade & Delete Segments Inspector */}
                  <div className="rounded-xl border border-sky-500/30 bg-zinc-950/90 p-2.5 space-y-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="inline-flex items-center gap-1.5 text-[11px] font-bold text-sky-300">
                        <Scissors className="h-3.5 w-3.5" />
                        Main Video Blade &amp; Cut ({effectiveVideoSegments.length} part
                        {effectiveVideoSegments.length > 1 ? "s" : ""})
                      </span>
                      <div className="flex items-center gap-1">
                        <button
                          type="button"
                          onClick={() => handleBladeVideoAtTime()}
                          className="inline-flex items-center gap-1 rounded-lg bg-sky-500/20 border border-sky-400/40 px-2 py-0.5 text-[10px] font-bold text-sky-200 hover:bg-sky-500/30"
                        >
                          <Scissors className="h-2.5 w-2.5" />
                          Blade Split
                        </button>
                        {(videoSegments.length > 1 || clipTrimStart > 0 || clipTrimEnd > 0) && (
                          <button
                            type="button"
                            onClick={handleRestoreFullVideo}
                            className="inline-flex items-center gap-1 rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-0.5 text-[10px] font-semibold text-zinc-300 hover:text-white"
                          >
                            <RotateCcw className="h-2.5 w-2.5" />
                            Full
                          </button>
                        )}
                      </div>
                    </div>
                    <div className="grid grid-cols-1 gap-1.5">
                      {effectiveVideoSegments.map((seg, sIdx) => {
                        const isSel = (selectedVideoSegmentIdx ?? 0) === sIdx;
                        return (
                          <div
                            key={`${seg.start}-${seg.end}-${sIdx}`}
                            onClick={() => {
                              setSelectedVideoSegmentIdx(sIdx);
                              if (previewVideoRef.current) {
                                previewVideoRef.current.currentTime = seg.start;
                              }
                            }}
                            className={`flex cursor-pointer items-center justify-between rounded-lg border px-2.5 py-1.5 text-[11px] transition ${isSel
                              ? "border-sky-400 bg-sky-500/20 text-white font-semibold"
                              : "border-zinc-800 bg-zinc-900/80 text-zinc-300 hover:border-zinc-700"
                              }`}
                          >
                            <span>
                              🎬 Part #{sIdx + 1}:{" "}
                              <span className="font-mono text-sky-300">
                                {seg.start.toFixed(1)}s → {seg.end.toFixed(1)}s
                              </span>{" "}
                              <span className="text-[10px] text-zinc-400">
                                ({(seg.end - seg.start).toFixed(1)}s)
                              </span>
                            </span>
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                handleDeleteVideoSegment(sIdx);
                              }}
                              title={
                                effectiveVideoSegments.length > 1
                                  ? "Delete this cut video part"
                                  : "Delete main video clip"
                              }
                              className="inline-flex items-center gap-1 rounded border border-red-500/40 bg-red-500/15 px-2 py-0.5 text-[10px] font-bold text-red-300 hover:bg-red-500/30"
                            >
                              <Trash2 className="h-2.5 w-2.5" />
                              {effectiveVideoSegments.length > 1 ? "Delete Part" : "Delete Video"}
                            </button>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>
              )}

              {/* Smooth Replay Mode & Trim Controls */}
              <div className="rounded-xl border border-zinc-800 bg-zinc-950/90 p-3.5 space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-[11px] font-bold uppercase tracking-wider text-amber-400">
                    Smooth Video Replay &amp; Cut Tools
                  </span>
                  <button
                    type="button"
                    onClick={() => {
                      setClipSpeed(1.0);
                      setClipTrimStart(0);
                      setClipTrimEnd(0);
                      setVideoSegments([]);
                      setSelectedVideoSegmentIdx(null);
                      setVideoZoom(1.0);
                      setMirrorFlip(false);
                      setLoopMode("crossfade");
                    }}
                    className="text-[11px] text-zinc-400 hover:text-white underline"
                  >
                    Reset
                  </button>
                </div>

                <div>
                  <label className="mb-1 block text-xs font-medium text-zinc-300">
                    When Video is Shorter than Song Duration:
                  </label>
                  <select
                    value={loopMode}
                    onChange={(e) => setLoopMode(e.target.value as LoopMode)}
                    className="w-full rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-xs text-zinc-100 focus:border-amber-400 focus:outline-none"
                  >
                    <option value="crossfade">
                      ✨ Smooth Crossfade Replay to Song Duration (Recommended)
                    </option>
                    <option value="pingpong">
                      🔁 Ping-Pong Boomerang Replay (Forward + Reverse)
                    </option>
                    <option value="normal">
                      ▶ Normal Repeat Replay (Soft Dissolve Seam)
                    </option>
                  </select>
                </div>

                <div className="grid grid-cols-2 gap-2.5">
                  <div>
                    <label className="mb-1 block text-[11px] text-zinc-400">
                      Clip Trim Start (s)
                    </label>
                    <input
                      type="number"
                      min="0"
                      step="0.5"
                      value={clipTrimStart}
                      onChange={(e) => setClipTrimStart(parseFloat(e.target.value) || 0)}
                      className="w-full rounded-lg border border-zinc-800 bg-zinc-900 px-2.5 py-1.5 text-xs text-zinc-100"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-[11px] text-zinc-400">
                      Clip Max Len (0=Full)
                    </label>
                    <input
                      type="number"
                      min="0"
                      step="0.5"
                      value={clipTrimEnd}
                      onChange={(e) => setClipTrimEnd(parseFloat(e.target.value) || 0)}
                      className="w-full rounded-lg border border-zinc-800 bg-zinc-900 px-2.5 py-1.5 text-xs text-zinc-100"
                    />
                  </div>
                </div>

                <div>
                  <label className="mb-1 flex justify-between text-[11px] text-zinc-400">
                    <span>Playback Speed (Slow-Mo / Fast)</span>
                    <span className="font-mono text-amber-400">{clipSpeed}x</span>
                  </label>
                  <input
                    type="range"
                    min="0.5"
                    max="1.5"
                    step="0.05"
                    value={clipSpeed}
                    onChange={(e) => setClipSpeed(parseFloat(e.target.value))}
                    className="w-full accent-amber-400"
                  />
                </div>

                <div>
                  <label className="mb-1 flex justify-between text-[11px] text-zinc-400">
                    <span>Zoom Crop (Hide AI Corner Watermark)</span>
                    <span className="font-mono text-amber-400">{videoZoom}x</span>
                  </label>
                  <input
                    type="range"
                    min="1.0"
                    max="1.5"
                    step="0.05"
                    value={videoZoom}
                    onChange={(e) => setVideoZoom(parseFloat(e.target.value))}
                    className="w-full accent-amber-400"
                  />
                </div>

                <div className="flex items-center justify-between pt-1">
                  <label className="flex items-center gap-2 text-xs text-zinc-300 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={mirrorFlip}
                      onChange={(e) => setMirrorFlip(e.target.checked)}
                      className="h-4 w-4 rounded accent-amber-400"
                    />
                    Horizontal Mirror Flip
                  </label>

                  <button
                    type="button"
                    onClick={() => setActiveSidebarTab("audio")}
                    className="rounded-lg bg-zinc-800 px-3 py-1.5 text-xs font-semibold text-amber-300 hover:bg-zinc-700"
                  >
                    Next: Song Audio →
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* TAB 2: SONG / AUDIO SOURCE */}
          {activeSidebarTab === "audio" && (
            <div className="space-y-4">
              <div>
                <h2 className="text-sm font-bold text-white">
                  🎵 Song / Audio Source &amp; Auto-Fetch
                </h2>
                <p className="text-xs text-zinc-400">
                  Paste a TikTok or YouTube link, or upload an MP3/WAV audio file.
                </p>
              </div>

              <div className="grid grid-cols-2 rounded-xl border border-zinc-800 bg-zinc-950 p-1">
                <button
                  type="button"
                  onClick={() => setSongSourceMode("url")}
                  className={`rounded-lg py-1.5 text-xs font-semibold transition ${songSourceMode === "url"
                    ? "bg-amber-400 text-black"
                    : "text-zinc-400 hover:text-white"
                    }`}
                >
                  TikTok / YouTube Link
                </button>
                <button
                  type="button"
                  onClick={() => setSongSourceMode("upload")}
                  className={`rounded-lg py-1.5 text-xs font-semibold transition ${songSourceMode === "upload"
                    ? "bg-amber-400 text-black"
                    : "text-zinc-400 hover:text-white"
                    }`}
                >
                  Upload Audio File
                </button>
              </div>

              {songSourceMode === "url" ? (
                <div className="space-y-2.5">
                  <input
                    type="text"
                    value={songUrl}
                    onChange={(e) => {
                      setSongUrl(e.target.value);
                      setCachedSongId(null);
                      setEditableTimeline([]);
                    }}
                    placeholder="Paste TikTok link (https://www.tiktok.com/@.../video/...) or YouTube link..."
                    className="w-full rounded-xl border border-zinc-800 bg-zinc-950 px-3.5 py-2.5 text-xs text-zinc-100 placeholder-zinc-500 focus:border-amber-400 focus:outline-none"
                  />
                  <div className="flex gap-2">
                    <button
                      type="button"
                      disabled={isFetchingSong}
                      onClick={() => handleFetchSong()}
                      className="flex-1 rounded-xl bg-amber-400 py-2.5 text-xs font-extrabold text-black transition hover:bg-amber-300 disabled:opacity-50"
                    >
                      {isFetchingSong ? "Fetching & Syncing Song..." : "⚡ Fetch & Auto-Sync Song"}
                    </button>
                    {isFetchingSong && (
                      <button
                        type="button"
                        onClick={handleStopFetchSong}
                        className="shrink-0 rounded-xl border border-rose-500/50 bg-rose-500/20 px-4 py-2.5 text-xs font-extrabold text-rose-200 transition hover:bg-rose-500/35"
                      >
                        ⏹ Stop
                      </button>
                    )}
                  </div>
                </div>
              ) : (
                <div className="space-y-2.5">
                  <label className="flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed border-zinc-800 bg-zinc-950 px-4 py-4 text-center transition hover:border-amber-400">
                    <span className="text-xs font-semibold text-zinc-200">
                      {songFile ? songFile.name : "+ Choose MP3, WAV, M4A, or MP4 Song File"}
                    </span>
                    <input
                      type="file"
                      accept="audio/*,video/*"
                      onChange={(e) => {
                        userModifiedMediaRef.current = true;
                        setSongFile(e.target.files?.[0] || null);
                        setCachedSongId(null);
                        setEditableTimeline([]);
                      }}
                      className="hidden"
                    />
                  </label>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      disabled={isFetchingSong || !songFile}
                      onClick={() => handleFetchSong()}
                      className="flex-1 rounded-xl bg-amber-400 py-2.5 text-xs font-extrabold text-black transition hover:bg-amber-300 disabled:opacity-50"
                    >
                      {isFetchingSong ? "Syncing Audio..." : "⚡ Load & Auto-Sync Audio"}
                    </button>
                    {isFetchingSong && (
                      <button
                        type="button"
                        onClick={handleStopFetchSong}
                        className="shrink-0 rounded-xl border border-rose-500/50 bg-rose-500/20 px-4 py-2.5 text-xs font-extrabold text-rose-200 transition hover:bg-rose-500/35"
                      >
                        ⏹ Stop
                      </button>
                    )}
                  </div>
                </div>
              )}

              {songFetchMsg && (
                <div
                  className={`rounded-xl border px-3 py-2 text-xs ${songFetchMsg.startsWith("Error:")
                    ? "border-red-500/40 bg-red-500/10 text-red-300"
                    : "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                    }`}
                >
                  {songFetchMsg}
                </div>
              )}

              <div className="rounded-xl border border-zinc-800 bg-zinc-950/90 p-3.5 space-y-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[11px] font-bold uppercase tracking-wider text-zinc-400">
                    Song Start &amp; Duration Picker
                  </span>
                  {rawSongDuration && (
                    <span className="rounded bg-amber-400/15 px-2 py-0.5 font-mono text-[10px] font-semibold text-amber-300">
                      Full: {formatMmSs(rawSongDuration)} ({rawSongDuration.toFixed(1)}s)
                    </span>
                  )}
                </div>

                {/* Start Position Slider + MM:SS / Sec Input + "Use Playhead" */}
                <div className="space-y-1.5 rounded-lg border border-zinc-800/80 bg-zinc-900/60 p-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <label className="text-[11px] font-medium text-zinc-300">
                      Start From (MM:SS or sec)
                    </label>
                    <button
                      type="button"
                      disabled={!cachedSongId}
                      onClick={() => {
                        const rawPos = Number((appliedAudioStartSec + currentAudioTime).toFixed(1));
                        const maxAllowed = rawSongDuration ? Math.max(0, rawSongDuration - 1.5) : rawPos;
                        const clamped = Math.min(rawPos, maxAllowed);
                        setAudioStartSec(clamped);
                        setAudioStartText(formatMmSs(clamped));
                      }}
                      title="Set start point to where the audio player is right now"
                      className="rounded border border-emerald-500/40 bg-emerald-500/15 px-2 py-0.5 text-[10px] font-bold text-emerald-300 hover:bg-emerald-500/25 disabled:opacity-40"
                    >
                      📍 Start Here ({formatMmSs(appliedAudioStartSec + currentAudioTime)})
                    </button>
                  </div>

                  <div className="flex items-center gap-2">
                    <input
                      type="range"
                      min="0"
                      max={Math.max(15, Math.floor((rawSongDuration || 180) - 2))}
                      step="0.5"
                      value={Math.min(audioStartSec, Math.max(15, (rawSongDuration || 180) - 2))}
                      onChange={(e) => {
                        const v = parseFloat(e.target.value) || 0;
                        setAudioStartSec(v);
                        setAudioStartText(formatMmSs(v));
                      }}
                      className="flex-1 accent-amber-400"
                    />
                    <input
                      type="text"
                      value={audioStartText}
                      onChange={(e) => {
                        setAudioStartText(e.target.value);
                        setAudioStartSec(parseTimeInputToSeconds(e.target.value));
                      }}
                      onBlur={() => setAudioStartText(formatMmSs(audioStartSec))}
                      placeholder="0:32"
                      className="w-16 rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-1 text-center font-mono text-xs text-amber-300"
                    />
                  </div>
                </div>

                {/* Clip Duration + Quick Presets */}
                <div className="space-y-1.5 rounded-lg border border-zinc-800/80 bg-zinc-900/60 p-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <label className="text-[11px] font-medium text-zinc-300">
                      Clip Length (0 = To End)
                    </label>
                    <span className="font-mono text-[10px] text-zinc-400">
                      {formatMmSs(audioStartSec)} →{" "}
                      {audioMaxDurationSec > 0
                        ? formatMmSs(
                          rawSongDuration
                            ? Math.min(rawSongDuration, audioStartSec + audioMaxDurationSec)
                            : audioStartSec + audioMaxDurationSec
                        )
                        : rawSongDuration
                          ? formatMmSs(rawSongDuration)
                          : "End"}
                    </span>
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5">
                    {[15, 30, 45, 60, 0].map((secPreset) => (
                      <button
                        key={secPreset}
                        type="button"
                        onClick={() => setAudioMaxDurationSec(secPreset)}
                        className={`rounded-md px-2 py-1 text-[10px] font-bold transition ${audioMaxDurationSec === secPreset
                          ? "bg-amber-400 text-black"
                          : "border border-zinc-800 bg-zinc-950 text-zinc-300 hover:border-amber-400/50"
                          }`}
                      >
                        {secPreset === 0 ? "Full End" : `${secPreset}s`}
                      </button>
                    ))}
                    <input
                      type="number"
                      min="0"
                      step="1"
                      value={audioMaxDurationSec}
                      onChange={(e) => setAudioMaxDurationSec(Math.max(0, parseFloat(e.target.value) || 0))}
                      className="ml-auto w-16 rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-1 text-center font-mono text-xs text-zinc-100"
                    />
                  </div>
                </div>

                {cachedSongId && (
                  <button
                    type="button"
                    disabled={isTrimmingSong || isFetchingSong}
                    onClick={() => handleTrimCachedSong()}
                    className="w-full rounded-xl border border-amber-400/50 bg-amber-400/20 py-2 text-xs font-extrabold text-amber-200 transition hover:bg-amber-400/30 disabled:opacity-50"
                  >
                    {isTrimmingSong
                      ? "Cutting Segment & Re-Syncing..."
                      : `✂️ Apply Song Cut (${formatMmSs(audioStartSec)}${audioMaxDurationSec > 0 ? ` • ${audioMaxDurationSec}s` : " → End"
                      }) & Re-Sync`}
                  </button>
                )}

                <div className="grid grid-cols-2 gap-2.5">
                  <div>
                    <label className="mb-1 block text-[11px] text-zinc-400">
                      Song Title (Optional)
                    </label>
                    <input
                      type="text"
                      value={songName}
                      onChange={(e) => setSongName(e.target.value)}
                      placeholder="For LRCLIB/Spotify"
                      className="w-full rounded-lg border border-zinc-800 bg-zinc-900 px-2.5 py-1.5 text-xs text-zinc-100"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-[11px] text-zinc-400">
                      Artist (Optional)
                    </label>
                    <input
                      type="text"
                      value={artistName}
                      onChange={(e) => setArtistName(e.target.value)}
                      placeholder="Artist name"
                      className="w-full rounded-lg border border-zinc-800 bg-zinc-900 px-2.5 py-1.5 text-xs text-zinc-100"
                    />
                  </div>
                </div>

                <label className="flex items-center gap-2 text-xs text-zinc-300 cursor-pointer pt-1">
                  <input
                    type="checkbox"
                    checked={audioFade}
                    onChange={(e) => setAudioFade(e.target.checked)}
                    className="h-3.5 w-3.5 rounded accent-amber-400"
                  />
                  Smooth Audio Fade-In &amp; Fade-Out
                </label>

                {songName.trim() && (
                  <button
                    type="button"
                    disabled={isSearchingWebLyrics}
                    onClick={() => {
                      setLyricsSearchQuery(songName.trim());
                      handleSearchSinhalaLyrics(songName.trim());
                    }}
                    className="w-full rounded-xl border border-emerald-500/40 bg-emerald-500/10 py-2 text-xs font-bold text-emerald-300 transition hover:bg-emerald-500/20 disabled:opacity-40"
                  >
                    {isSearchingWebLyrics
                      ? "Searching LyriHub.lk & Lyrics-LK..."
                      : `🌐 Fetch Sinhala Lyrics for "${songName.trim()}"`}
                  </button>
                )}
              </div>

              {/* DEDICATED FETCHED LYRICS SECTION (BELOW SONG START & DURATION PICKER) */}
              <div className="rounded-xl border border-emerald-500/30 bg-zinc-950/95 p-3.5 space-y-2.5 shadow-inner">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[11px] font-extrabold uppercase tracking-wider text-emerald-300">
                      📜 Fetched Song Lyrics
                    </span>
                    {detectedLyricsSource && (
                      <span className="rounded-full border border-emerald-400/35 bg-emerald-500/15 px-2 py-0.2 text-[9px] font-bold text-emerald-200">
                        {detectedLyricsSource}
                      </span>
                    )}
                    <span className="rounded bg-zinc-900 px-1.5 py-0.5 font-mono text-[10px] text-zinc-400">
                      {(() => {
                        const fullCount = manualLyrics
                          .split("\n")
                          .map((l) => l.trim())
                          .filter((l) => l && !(l.startsWith("[") && l.endsWith("]"))).length;
                        const syncedCount = editableTimeline.length;
                        if (fullCount > 0 && syncedCount > 0 && fullCount !== syncedCount) {
                          return `${syncedCount} synced / ${fullCount} full lines`;
                        }
                        if (fullCount > 0) return `${fullCount} full lines`;
                        return `${syncedCount} lines`;
                      })()}
                    </span>
                  </div>

                  <div className="flex items-center gap-1">
                    <div className="inline-flex rounded-lg border border-zinc-800 bg-zinc-900 p-0.5 text-[10px]">
                      <button
                        type="button"
                        onClick={() => setAudioTabLyricsView("synced")}
                        className={`rounded-md px-2 py-0.5 font-semibold transition ${audioTabLyricsView === "synced"
                          ? "bg-emerald-400 text-black"
                          : "text-zinc-400 hover:text-white"
                          }`}
                      >
                        ⏱ Synced
                      </button>
                      <button
                        type="button"
                        onClick={() => setAudioTabLyricsView("text")}
                        className={`rounded-md px-2 py-0.5 font-semibold transition ${audioTabLyricsView === "text"
                          ? "bg-emerald-400 text-black"
                          : "text-zinc-400 hover:text-white"
                          }`}
                      >
                        📝 Full Text
                      </button>
                    </div>
                    {(editableTimeline.length > 0 || manualLyrics.trim().length > 0) && (
                      <button
                        type="button"
                        onClick={() => {
                          const txt =
                            manualLyrics.trim() ||
                            editableTimeline.map((l) => l.text).join("\n");
                          navigator.clipboard?.writeText(txt).catch(() => { });
                          showToast("📋 Copied fetched lyrics to clipboard");
                        }}
                        title="Copy fetched lyrics text"
                        className="rounded-lg border border-zinc-800 bg-zinc-900 px-2 py-1 text-[10px] font-semibold text-zinc-300 hover:border-emerald-400 hover:text-emerald-300"
                      >
                        Copy
                      </button>
                    )}
                  </div>
                </div>

                {webLyricsStatus && (
                  <div
                    className={`rounded-lg px-2.5 py-1.5 text-[11px] ${webLyricsStatus.startsWith("Error:")
                      ? "bg-red-500/10 text-red-300"
                      : "bg-emerald-500/10 text-emerald-300"
                      }`}
                  >
                    {webLyricsStatus}
                  </div>
                )}

                {webLyricsCandidates.length > 1 && (
                  <div className="flex flex-wrap gap-1 pt-0.5">
                    {webLyricsCandidates.map((cand, idx) => (
                      <button
                        key={`${cand.url}-${idx}`}
                        type="button"
                        onClick={async () => {
                          setManualLyrics(cand.lyrics_text);
                          setCaptionMode("manual");
                          if (cand.title) setSongName(cand.title);
                          if (cand.artist) setArtistName(cand.artist);
                          setDetectedLyricsSource(cand.source);
                          setWebLyricsStatus(`✓ Switched to ${cand.source}: ${cand.title || cand.url}`);
                          if (cachedSongId) {
                            await handleAutoSyncLyricsToAudio("manual", cand.lyrics_text);
                          } else {
                            const rawLines = cand.lyrics_text
                              .split("\n")
                              .map((l) => l.trim())
                              .filter(Boolean);
                            const totalDur = fetchedSongDuration || 30;
                            const step = Math.max(1.8, totalDur / Math.max(rawLines.length, 1));
                            setEditableTimeline(
                              rawLines.map((text, i) => {
                                const s = Number((i * step).toFixed(2));
                                const e = Number(Math.min(totalDur, (i + 1) * step).toFixed(2));
                                return {
                                  start: s,
                                  end: e,
                                  text,
                                  words: buildWordTimingsForLine(text, s, e),
                                };
                              })
                            );
                          }
                        }}
                        className="rounded-md border border-zinc-700 bg-zinc-900 px-2 py-0.5 text-[10px] text-zinc-300 hover:border-emerald-400 hover:text-emerald-300"
                      >
                        #{idx + 1} {cand.source}: {cand.title || "Result"}
                      </button>
                    ))}
                  </div>
                )}

                {editableTimeline.length === 0 && !manualLyrics.trim() ? (
                  <div className="rounded-xl border border-dashed border-zinc-800 bg-zinc-900/50 p-4 text-center text-xs text-zinc-400">
                    No lyrics fetched yet. Click{" "}
                    <span className="font-bold text-amber-300">⚡ Fetch &amp; Auto-Sync Song</span>{" "}
                    above — the detected Sinhala/synced lyrics will appear right here.
                  </div>
                ) : (
                  <div className="space-y-2.5">
                    {(() => {
                      const fullLyricLines = (
                        manualLyrics.trim()
                          ? manualLyrics
                            .split("\n")
                            .map((l) => l.trim())
                            .filter((l) => l && !(l.startsWith("[") && l.endsWith("]")))
                          : editableTimeline.map((l) => l.text)
                      );
                      return (
                        <div className="rounded-lg border border-amber-400/30 bg-zinc-900/90 p-2 space-y-2">
                          <div className="flex flex-col gap-1">
                            <label className="text-[10px] font-bold text-amber-300">
                              🎯 Song Starts At Lyric Line (මැදින් පටන් ගන්නා පේළිය තෝරන්න):
                            </label>
                            <select
                              value={selectedStartLineIdx}
                              onChange={(e) => {
                                const nextIdx = parseInt(e.target.value, 10);
                                setSelectedStartLineIdx(nextIdx);
                                if (cachedSongId) {
                                  const fullTxt =
                                    manualLyrics.trim() ||
                                    editableTimeline.map((l) => l.text).join("\n");
                                  handleAutoSyncLyricsToAudio("manual", fullTxt, nextIdx);
                                }
                              }}
                              style={{ fontFamily: editableUiFontFamily }}
                              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-xs text-zinc-100 focus:border-amber-400 focus:outline-none"
                            >
                              <option value={-1}>
                                🤖 Auto-Detect from Song Voice (හඬ අනුව මැදින් පටන් ගන්නා තැන Auto හඳුනාගන්න)
                              </option>
                              {fullLyricLines.map((lnText, lIdx) => (
                                <option key={lIdx} value={lIdx}>
                                  Line #{lIdx + 1}: {lnText}
                                </option>
                              ))}
                            </select>
                          </div>

                          {cachedSongId && (
                            <button
                              type="button"
                              disabled={isSyncingLyrics}
                              onClick={() =>
                                handleAutoSyncLyricsToAudio(
                                  "manual",
                                  manualLyrics.trim() ||
                                  editableTimeline.map((l) => l.text).join("\n"),
                                  selectedStartLineIdx
                                )
                              }
                              className="w-full rounded-lg bg-emerald-400 py-1.5 text-xs font-extrabold text-black transition hover:bg-emerald-300 disabled:opacity-50"
                            >
                              {isSyncingLyrics
                                ? "Re-Syncing Lyrics to Song Voice..."
                                : selectedStartLineIdx >= 0
                                  ? `⚡ Apply & Re-Sync from Line #${selectedStartLineIdx + 1}`
                                  : "⚡ Apply & Re-Sync Lyrics to Song Voice"}
                            </button>
                          )}
                        </div>
                      );
                    })()}

                    {audioTabLyricsView === "synced" && editableTimeline.length > 0 ? (
                      <div className="max-h-56 space-y-1.5 overflow-y-auto pr-1">
                        {editableTimeline.map((line, idx) => {
                          const isPlayingLine =
                            currentAudioTime >= line.start + lyricsOffsetSec &&
                            currentAudioTime <= line.end + lyricsOffsetSec;
                          return (
                            <div
                              key={idx}
                              onClick={() => {
                                setSelectedCaptionIdx(idx);
                                seekAudioTo(line.start + lyricsOffsetSec);
                              }}
                              className={`group flex cursor-pointer items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs transition ${isPlayingLine
                                ? "border-emerald-400 bg-emerald-500/20 text-white shadow-sm"
                                : selectedCaptionIdx === idx
                                  ? "border-amber-400/60 bg-amber-400/10 text-zinc-100"
                                  : "border-zinc-800/90 bg-zinc-900/75 text-zinc-200 hover:border-zinc-700"
                                }`}
                            >
                              <span className="shrink-0 rounded bg-zinc-950 px-1.5 py-0.5 font-mono text-[10px] text-emerald-300">
                                {formatMmSs(line.start)} → {formatMmSs(line.end)}
                              </span>
                              <input
                                type="text"
                                value={line.text}
                                onClick={(e) => e.stopPropagation()}
                                onChange={(e) => handleTimelineChange(idx, "text", e.target.value)}
                                style={{ fontFamily: editableUiFontFamily }}
                                className="min-w-0 flex-1 bg-transparent text-xs text-white focus:outline-none"
                              />
                              {cachedSongId && (
                                <button
                                  type="button"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    const fullLines = (
                                      manualLyrics.trim()
                                        ? manualLyrics
                                          .split("\n")
                                          .map((l) => l.trim())
                                          .filter((l) => l && !(l.startsWith("[") && l.endsWith("]")))
                                        : editableTimeline.map((l) => l.text)
                                    );
                                    const matchIdx = fullLines.findIndex((fl) => fl === line.text.trim());
                                    const startIdx = matchIdx >= 0 ? matchIdx : idx;
                                    setSelectedStartLineIdx(startIdx);
                                    handleAutoSyncLyricsToAudio(
                                      "manual",
                                      fullLines.join("\n"),
                                      startIdx
                                    );
                                  }}
                                  title="Set this line as the first sung line of the song and re-sync"
                                  className="shrink-0 rounded border border-amber-400/40 bg-amber-400/10 px-1.5 py-0.5 text-[9px] font-bold text-amber-300 opacity-80 hover:bg-amber-400 hover:text-black group-hover:opacity-100"
                                >
                                  ▶ Start Here
                                </button>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      (() => {
                        const fullSongLines = (
                          manualLyrics.trim()
                            ? manualLyrics
                              .split("\n")
                              .map((l) => l.trim())
                              .filter((l) => l && !(l.startsWith("[") && l.endsWith("]")))
                            : editableTimeline.map((l) => l.text)
                        );
                        return (
                          <div className="space-y-2">
                            <div className="flex items-center justify-between gap-2 rounded-lg border border-amber-400/25 bg-amber-400/10 px-2.5 py-1.5 text-[11px] text-amber-200">
                              <span>
                                👆 Click any lyric line below to set where the song starts &amp; sync from that line forward ({fullSongLines.length} full song lines):
                              </span>
                              <button
                                type="button"
                                onClick={() => setIsEditingFullLyricsText((prev) => !prev)}
                                className="shrink-0 rounded border border-amber-400/40 bg-zinc-900 px-2 py-0.5 text-[10px] font-bold text-amber-300 hover:bg-amber-400 hover:text-black"
                              >
                                {isEditingFullLyricsText ? "📋 Line Picker" : "✏️ Edit Text"}
                              </button>
                            </div>

                            {!isEditingFullLyricsText && fullSongLines.length > 0 ? (
                              <div className="max-h-60 space-y-1 overflow-y-auto pr-1">
                                {fullSongLines.map((lnText, lIdx) => {
                                  const isSelectedStart = selectedStartLineIdx === lIdx;
                                  const isInSyncedRange =
                                    selectedStartLineIdx >= 0
                                      ? lIdx >= selectedStartLineIdx &&
                                      lIdx < selectedStartLineIdx + Math.max(1, editableTimeline.length)
                                      : editableTimeline.some((el) => el.text.trim() === lnText.trim());
                                  return (
                                    <div
                                      key={lIdx}
                                      onClick={() => {
                                        setSelectedStartLineIdx(lIdx);
                                        const fullTxt = fullSongLines.join("\n");
                                        setManualLyrics(fullTxt);
                                        setCaptionMode("manual");
                                        if (cachedSongId) {
                                          handleAutoSyncLyricsToAudio("manual", fullTxt, lIdx);
                                        } else {
                                          showToast(
                                            `🎯 Selected Line #${lIdx + 1} as song start — click Fetch/Sync when ready!`
                                          );
                                        }
                                      }}
                                      title={`Click to start song captions from Line #${lIdx + 1} and voice-sync forward`}
                                      className={`group flex cursor-pointer items-center justify-between gap-2 rounded-lg border px-2.5 py-1.5 text-xs transition ${isSelectedStart
                                        ? "border-amber-400 bg-amber-400/20 text-white font-bold shadow-sm ring-1 ring-amber-400/50"
                                        : isInSyncedRange
                                          ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-100 hover:border-amber-400"
                                          : "border-zinc-800/80 bg-zinc-900/60 text-zinc-300 hover:border-amber-400/60 hover:bg-zinc-900"
                                        }`}
                                    >
                                      <div className="flex items-center gap-2 min-w-0">
                                        <span
                                          className={`shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] font-bold ${isSelectedStart
                                            ? "bg-amber-400 text-black"
                                            : isInSyncedRange
                                              ? "bg-emerald-500/25 text-emerald-300"
                                              : "bg-zinc-950 text-zinc-400"
                                            }`}
                                        >
                                          #{lIdx + 1}
                                        </span>
                                        <span
                                          style={{ fontFamily: editableUiFontFamily }}
                                          className="truncate"
                                        >
                                          {lnText}
                                        </span>
                                      </div>
                                      <button
                                        type="button"
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          setSelectedStartLineIdx(lIdx);
                                          const fullTxt = fullSongLines.join("\n");
                                          setManualLyrics(fullTxt);
                                          setCaptionMode("manual");
                                          if (cachedSongId) {
                                            handleAutoSyncLyricsToAudio("manual", fullTxt, lIdx);
                                          }
                                        }}
                                        className={`shrink-0 rounded px-2 py-0.5 text-[10px] font-extrabold transition ${isSelectedStart
                                          ? "bg-amber-400 text-black"
                                          : "border border-amber-400/40 bg-amber-400/10 text-amber-300 hover:bg-amber-400 hover:text-black"
                                          }`}
                                      >
                                        {isSelectedStart ? "🎯 Starts Here" : "▶ Start From Here"}
                                      </button>
                                    </div>
                                  );
                                })}
                              </div>
                            ) : (
                              <div className="space-y-2">
                                <textarea
                                  rows={7}
                                  value={
                                    manualLyrics ||
                                    editableTimeline.map((l) => l.text).join("\n")
                                  }
                                  onChange={(e) => {
                                    const nextVal = e.target.value;
                                    const lineCount = nextVal
                                      .split("\n")
                                      .map((l) => l.trim())
                                      .filter((l) => l && !(l.startsWith("[") && l.endsWith("]"))).length;
                                    setManualLyrics(nextVal);
                                    setCaptionMode("manual");
                                    if (selectedStartLineIdx >= lineCount) {
                                      setSelectedStartLineIdx(-1);
                                    }
                                  }}
                                  style={{ fontFamily: editableUiFontFamily }}
                                  placeholder="Fetched full song Sinhala lyrics appear here..."
                                  className="w-full rounded-xl border border-zinc-800 bg-zinc-900 p-2.5 text-xs leading-relaxed text-zinc-100 focus:border-emerald-400 focus:outline-none"
                                />
                                <button
                                  type="button"
                                  disabled={isSyncingLyrics || isFetchingSong}
                                  onClick={() => {
                                    const txt =
                                      manualLyrics ||
                                      editableTimeline.map((l) => l.text).join("\n");
                                    if (txt.trim()) {
                                      handleAutoSyncLyricsToAudio(
                                        "manual",
                                        txt,
                                        selectedStartLineIdx
                                      );
                                    }
                                  }}
                                  className="w-full rounded-lg bg-emerald-400 py-2 text-xs font-extrabold text-black transition hover:bg-emerald-300 disabled:opacity-40"
                                >
                                  {isSyncingLyrics || isFetchingSong
                                    ? "Syncing Edited Lyrics to Song Vocals..."
                                    : selectedStartLineIdx >= 0
                                      ? `⚡ Apply & Re-Sync from Line #${selectedStartLineIdx + 1}`
                                      : "⚡ Apply & Re-Sync Edited Lyrics"}
                                </button>
                              </div>
                            )}
                          </div>
                        );
                      })()
                    )}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* TAB 3: SINHALA CAPTIONS & VOCAL SYNC STUDIO */}
          {activeSidebarTab === "captions" && (
            <div className="space-y-4">
              <div>
                <h2 className="text-sm font-bold text-white">
                  💬 Sinhala Captions &amp; Vocal Timing Sync
                </h2>
                <p className="text-xs text-zinc-400">
                  Auto-transcribe vocals, fetch Sinhala lyrics by Song Title (LyriHub.lk / Lyrics-LK), or paste manually.
                </p>
              </div>

              {/* 🌐 SINHALA LYRICS FINDER BY SONG TITLE / URL (LYRIHUB.LK + LYRICS-LK.COM + SMULE SLUG) */}
              <div className="rounded-xl border border-emerald-500/30 bg-zinc-950 p-3 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-[11px] font-bold text-emerald-300">
                    Sinhala Lyrics Finder (All Sites)
                  </span>
                  <span className="text-[10px] text-zinc-400">LyriHub • Lyrics-LK • GeeMansala</span>
                </div>
                <div className="flex gap-1.5">
                  <input
                    type="text"
                    value={lyricsSearchQuery}
                    onChange={(e) => setLyricsSearchQuery(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        handleSearchSinhalaLyrics();
                      }
                    }}
                    placeholder="Song title (e.g. Sanda Mithuri / සඳ මිතුරි) or link..."
                    className="flex-1 rounded-lg border border-zinc-800 bg-zinc-900 px-2.5 py-1.5 text-xs text-zinc-100 placeholder-zinc-500 focus:border-emerald-400 focus:outline-none"
                  />
                  <button
                    type="button"
                    disabled={isSearchingWebLyrics}
                    onClick={() => handleSearchSinhalaLyrics()}
                    className="shrink-0 rounded-lg bg-emerald-400 px-3 py-1.5 text-xs font-extrabold text-black hover:bg-emerald-300 disabled:opacity-40"
                  >
                    {isSearchingWebLyrics ? "Searching..." : "🔍 Find"}
                  </button>
                </div>

                {webLyricsStatus && (
                  <div
                    className={`rounded-lg px-2.5 py-1.5 text-[11px] ${webLyricsStatus.startsWith("Error:")
                      ? "bg-red-500/10 text-red-300"
                      : "bg-emerald-500/10 text-emerald-300"
                      }`}
                  >
                    {webLyricsStatus}
                  </div>
                )}

                {webLyricsCandidates.length > 1 && (
                  <div className="flex flex-wrap gap-1 pt-0.5">
                    {webLyricsCandidates.map((cand, idx) => (
                      <button
                        key={`${cand.url}-${idx}`}
                        type="button"
                        onClick={async () => {
                          setManualLyrics(cand.lyrics_text);
                          setCaptionMode("manual");
                          if (cand.title) setSongName(cand.title);
                          if (cand.artist) setArtistName(cand.artist);
                          setWebLyricsStatus(`✓ Switched to ${cand.source}: ${cand.title || cand.url}`);
                          if (cachedSongId) {
                            await handleAutoSyncLyricsToAudio("manual", cand.lyrics_text);
                          } else {
                            setEditableTimeline([]);
                          }
                        }}
                        className="rounded-md border border-zinc-800 bg-zinc-900 px-2 py-0.5 text-[10px] text-zinc-300 hover:border-emerald-400 hover:text-emerald-300"
                      >
                        {cand.source}: {cand.title || "Alt Version"}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <div className="grid grid-cols-2 rounded-xl border border-zinc-800 bg-zinc-950 p-1">
                <button
                  type="button"
                  onClick={() => {
                    setCaptionMode("auto");
                    setEditableTimeline([]);
                  }}
                  className={`rounded-lg py-1.5 text-xs font-semibold transition ${captionMode === "auto"
                    ? "bg-amber-400 text-black"
                    : "text-zinc-400 hover:text-white"
                    }`}
                >
                  🤖 Auto-Generate
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setCaptionMode("manual");
                    setEditableTimeline([]);
                  }}
                  className={`rounded-lg py-1.5 text-xs font-semibold transition ${captionMode === "manual"
                    ? "bg-emerald-400 text-black"
                    : "text-zinc-400 hover:text-white"
                    }`}
                >
                  ✍️ Manual Lyrics
                </button>
              </div>

              <div className="flex items-center justify-between gap-2 text-xs">
                <div className="flex items-center gap-1.5">
                  <span className="text-zinc-400">Language:</span>
                  <select
                    value={language}
                    onChange={(e) => {
                      setLanguage(e.target.value);
                      setEditableTimeline([]);
                    }}
                    className="rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-1 text-xs text-zinc-100"
                  >
                    <option value="si">සිංහල (si-LK)</option>
                    <option value="en">English (en-US)</option>
                  </select>
                </div>

                <div className="flex items-center gap-2">
                  {editableTimeline.length > 0 && captionMode === "auto" && (
                    <button
                      type="button"
                      onClick={() => {
                        setManualLyrics(editableTimeline.map((l) => l.text).join("\n"));
                        setCaptionMode("manual");
                      }}
                      className="text-[11px] font-semibold text-emerald-400 underline"
                    >
                      Edit as Text
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => {
                      setManualLyrics(REFERENCE_SINHALA_LYRICS);
                      setCaptionMode("manual");
                      setEditableTimeline([]);
                    }}
                    className="text-[11px] font-semibold text-amber-400 underline"
                  >
                    Load Demo Lyrics
                  </button>
                </div>
              </div>

              {captionMode === "auto" ? (
                <div className="rounded-xl border border-amber-400/25 bg-zinc-950 p-3.5 text-xs space-y-2">
                  <div className="font-bold text-amber-300">
                    🤖 Auto-Caption Mode Active
                  </div>
                  <p className="text-zinc-400 leading-relaxed">
                    Our engine listens to the song using Sinhala Speech-to-Text (<code className="text-amber-300">si-LK</code>) / LRCLIB / Spotify and generates time-synced captions automatically on the Timeline below!
                  </p>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      disabled={isSyncingLyrics || isFetchingSong}
                      onClick={() => handleAutoSyncLyricsToAudio("auto")}
                      className="flex-1 rounded-xl bg-amber-400 py-2 text-xs font-extrabold text-black hover:bg-amber-300 disabled:opacity-40"
                    >
                      {isSyncingLyrics || isFetchingSong
                        ? "Transcribing Song Vocals..."
                        : "🤖 Auto-Transcribe Song Captions Now"}
                    </button>
                    {isFetchingSong || isSyncingLyrics ? (
                      <button
                        type="button"
                        onClick={handleStopFetchSong}
                        className="shrink-0 rounded-xl border border-rose-500/50 bg-rose-500/20 px-3 py-2 text-xs font-extrabold text-rose-200 transition hover:bg-rose-500/35"
                      >
                        ⏹ Stop
                      </button>
                    ) : null}
                  </div>
                </div>
              ) : (
                <div className="space-y-2.5">
                  <textarea
                    rows={7}
                    value={manualLyrics}
                    onChange={(e) => {
                      const val = e.target.value;
                      setManualLyrics(val);
                      setEditableTimeline([]);
                      if (val.trim()) setCaptionMode("manual");
                    }}
                    style={{ fontFamily: editableUiFontFamily }}
                    placeholder="ඔබේ සිංහල ගීතයේ පද පේළි මෙතන Paste කරන්න (එක පේළියකට එක කොටසක් බැගින්)..."
                    className="w-full rounded-xl border border-zinc-800 bg-zinc-950 p-3 text-xs leading-relaxed text-zinc-100 placeholder-zinc-500 focus:border-emerald-400 focus:outline-none"
                  />
                  {manualLyrics.trim() && (
                    <div className="rounded-lg border border-amber-400/30 bg-zinc-950 p-2 space-y-1">
                      <label className="text-[10px] font-bold text-amber-300">
                        🎯 Song Starts At Lyric Line (මැදින් පටන් ගන්නා පේළිය තෝරන්න):
                      </label>
                      <select
                        value={selectedStartLineIdx}
                        onChange={(e) => setSelectedStartLineIdx(parseInt(e.target.value, 10))}
                        style={{ fontFamily: editableUiFontFamily }}
                        className="w-full rounded-lg border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-xs text-zinc-100 focus:border-amber-400 focus:outline-none"
                      >
                        <option value={-1}>
                          🤖 Auto-Detect from Song Voice (හඬ අනුව Auto හඳුනාගන්න)
                        </option>
                        {manualLyrics
                          .split("\n")
                          .map((l) => l.trim())
                          .filter((l) => l && !(l.startsWith("[") && l.endsWith("]")))
                          .map((lnText, lIdx) => (
                            <option key={lIdx} value={lIdx}>
                              Line #{lIdx + 1}: {lnText}
                            </option>
                          ))}
                      </select>
                    </div>
                  )}
                  <div className="flex gap-2">
                    <button
                      type="button"
                      disabled={isSyncingLyrics || isFetchingSong}
                      onClick={() =>
                        handleAutoSyncLyricsToAudio("manual", manualLyrics, selectedStartLineIdx)
                      }
                      className="flex-1 rounded-xl bg-emerald-400 py-2.5 text-xs font-extrabold text-black hover:bg-emerald-300 disabled:opacity-40"
                    >
                      {isSyncingLyrics || isFetchingSong
                        ? "Syncing to Song Vocals..."
                        : selectedStartLineIdx >= 0
                          ? `⚡ Sync Captions from Line #${selectedStartLineIdx + 1}`
                          : "⚡ Sync Manual Captions to Song Vocals"}
                    </button>
                    {isFetchingSong || isSyncingLyrics ? (
                      <button
                        type="button"
                        onClick={handleStopFetchSong}
                        className="shrink-0 rounded-xl border border-rose-500/50 bg-rose-500/20 px-3 py-2.5 text-xs font-extrabold text-rose-200 transition hover:bg-rose-500/35"
                      >
                        ⏹ Stop
                      </button>
                    ) : null}
                  </div>
                </div>
              )}

              {/* Global Timing Shift & Live Tap-to-Sync */}
              <div className="rounded-xl border border-zinc-800 bg-zinc-950 p-3.5 space-y-3">
                <div>
                  <label className="mb-1 flex justify-between text-xs text-zinc-300">
                    <span>Global Caption Timing Shift</span>
                    <span className="font-mono font-bold text-amber-400">
                      {lyricsOffsetSec > 0 ? `+${lyricsOffsetSec.toFixed(2)}s` : `${lyricsOffsetSec.toFixed(2)}s`}
                    </span>
                  </label>
                  <input
                    type="range"
                    min="-2.5"
                    max="2.5"
                    step="0.05"
                    value={lyricsOffsetSec}
                    onChange={(e) => setLyricsOffsetSec(parseFloat(e.target.value))}
                    className="w-full accent-amber-400"
                  />
                </div>

                {songPreviewUrl && (
                  <div>
                    {!isTapSyncActive ? (
                      <button
                        type="button"
                        onClick={startLiveTapToSync}
                        className="w-full rounded-xl border border-amber-400/50 bg-amber-400/15 py-2 text-xs font-bold text-amber-300 hover:bg-amber-400/25"
                      >
                        🎯 Start Live Tap-to-Sync with Song
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={handleTapCurrentLine}
                        className="w-full animate-pulse rounded-xl bg-amber-400 py-2.5 text-xs font-extrabold text-black shadow-lg"
                      >
                        👆 TAP NOW FOR LINE #{tapLineCursor + 1}: &ldquo;
                        {editableTimeline[tapLineCursor]?.text.slice(0, 18)}...&rdquo;
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* TAB 4: SINHALA FONTS & TYPOGRAPHY */}
          {activeSidebarTab === "fonts" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-sm font-bold text-white">
                    🔤 Sinhala Font Studio &amp; Live Preview
                  </h2>
                  <p className="text-xs text-zinc-400">
                    Click any font below for a real live Sinhala preview &amp; FFmpeg render check.
                  </p>
                </div>
                <label className="cursor-pointer rounded-lg border border-amber-400/40 bg-amber-400/15 px-2.5 py-1.5 text-[11px] font-bold text-amber-300 hover:bg-amber-400/25">
                  + Upload .TTF/.OTF
                  <input
                    type="file"
                    accept=".ttf,.otf,.TTF,.OTF"
                    onChange={handleFontFileUpload}
                    className="hidden"
                  />
                </label>
              </div>

              {/* ✨ REAL-TIME SELECTED FONT SPECIMEN & LIVE PREVIEW STAGE */}
              <div className="overflow-hidden rounded-2xl border border-amber-400/40 bg-zinc-950 shadow-xl">
                <div className="flex items-center justify-between border-b border-zinc-800/80 bg-zinc-900/90 px-3 py-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="inline-block h-2 w-2 shrink-0 rounded-full bg-amber-400 animate-ping" />
                    <span className="truncate text-xs font-extrabold text-amber-300">
                      {selectedFontFamily}
                    </span>
                    {activeFontEntry?.style_tag && (
                      <span
                        className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] ${activeFontEntry.encoding === "wijesekara_fm"
                          ? "border-violet-400/50 bg-violet-500/15 font-semibold text-violet-300"
                          : activeFontEntry.encoding === "isi"
                            ? "border-rose-400/50 bg-rose-500/15 font-semibold text-rose-300"
                            : "border-zinc-700 bg-zinc-800 text-zinc-300"
                          }`}
                      >
                        {activeFontEntry.style_tag}
                      </span>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => setFontPreviewAnimKey((prev) => prev + 1)}
                    className="shrink-0 rounded-lg border border-amber-400/30 bg-amber-400/10 px-2 py-0.5 text-[10px] font-bold text-amber-300 hover:bg-amber-400/20"
                  >
                    ↻ Replay Fade
                  </button>
                </div>

                {activeFontEntry?.encoding === "wijesekara_fm" && (
                  <div className="border-b border-violet-500/20 bg-violet-950/35 px-3 py-1.5 text-[10px] text-violet-200">
                    ⌨️ <span className="font-bold">Wijesekara (FM) Auto-Converter Active:</span>{" "}
                    Unicode Sinhala lyrics are automatically converted for both Live Preview &amp; Final Render.
                  </div>
                )}
                {activeFontEntry?.encoding === "isi" && (
                  <div className="border-b border-rose-500/25 bg-rose-950/35 px-3 py-1.5 text-[10px] text-rose-200">
                    ℹ️ <span className="font-bold">Legacy ISI Font:</span>{" "}
                    This font uses Informatics ISI encoding rather than Unicode or Wijesekara FM.
                  </div>
                )}

                {/* Aesthetic Mini-Canvas Real Font Render Stage */}
                <div className="relative flex min-h-[115px] flex-col items-center justify-center overflow-hidden bg-gradient-to-b from-amber-900/35 via-zinc-950 to-black px-4 py-5 text-center">
                  {watermarkText && (
                    <span className="pointer-events-none absolute bottom-1 left-1/2 -translate-x-1/2 whitespace-nowrap font-serif text-[7.5px] italic tracking-wider text-white/55">
                      {watermarkText}
                    </span>
                  )}
                  <span className="pointer-events-none absolute right-2.5 top-2 rounded bg-black/60 px-1.5 py-0.5 text-[9px] font-mono text-zinc-400">
                    {fontSize}px • {boldText ? "Bold" : "Regular"}{italicText ? " Italic" : ""}
                  </span>

                  <p
                    key={`${selectedFontFamily}-${fontPreviewAnimKey}`}
                    className="leading-snug transition-all duration-300"
                    style={{
                      fontFamily: getCssFontFamily(selectedFontFamily),
                      fontSize: `${Math.max(18, Math.min(36, Math.round(fontSize * 0.62)))}px`,
                      color: effectiveLiveCaptionColor,
                      fontWeight: boldText ? 700 : 400,
                      fontStyle: italicText ? "italic" : "normal",
                      WebkitTextStroke:
                        fontThickness > 0
                          ? `${(fontThickness * 2 * 0.62).toFixed(2)}px ${effectiveLiveCaptionColor}`
                          : undefined,
                      paintOrder: "stroke fill",
                      textShadow: `0 2px ${Math.round(
                        shadowDepth * 3.5
                      )}px rgba(0,0,0,0.95), 0 0 ${Math.round(
                        outlineWidth * 2.5
                      )}px rgba(0,0,0,0.95)`,
                    }}
                  >
                    {formatActiveFontText(effectiveFontSampleText)}
                  </p>

                  {/* Sinhala Vowel & Ligature Specimen Strip in Active Font */}
                  <div
                    className="mt-3 border-t border-white/10 pt-2 text-[12px] text-zinc-300/90"
                    style={{
                      fontFamily: getCssFontFamily(selectedFontFamily),
                      fontWeight: boldText ? 700 : 400,
                      fontStyle: italicText ? "italic" : "normal",
                    }}
                  >
                    {formatActiveFontText(
                      "අ ආ ඇ ඈ ඉ ඊ උ ඌ එ ඒ ඔ ඕ • කි කී කු කූ කෙ කේ කො කෝ • ශ්‍රී ලංකා"
                    )}
                  </div>
                </div>

                {/* Editable Custom Sample Text + Quick Phrases */}
                <div className="border-t border-zinc-800/80 bg-zinc-900/50 p-2.5 space-y-2">
                  <div className="flex items-center gap-1.5">
                    <input
                      type="text"
                      value={fontPreviewSampleText}
                      onChange={(e) => setFontPreviewSampleText(e.target.value)}
                      placeholder="Type Sinhala or English text to test font..."
                      style={{
                        fontFamily: editableUiFontFamily,
                      }}
                      className="flex-1 rounded-lg border border-zinc-800 bg-zinc-950 px-2.5 py-1.5 text-xs text-zinc-100 placeholder-zinc-500 focus:border-amber-400 focus:outline-none"
                    />
                    <button
                      type="button"
                      onClick={() => setShowServerFontRender((prev) => !prev)}
                      className={`rounded-lg border px-2 py-1.5 text-[10px] font-bold transition ${showServerFontRender
                        ? "border-emerald-400/50 bg-emerald-400/15 text-emerald-300"
                        : "border-zinc-800 bg-zinc-900 text-zinc-400 hover:text-white"
                        }`}
                      title="Toggle 100% real FFmpeg libass server-rendered frame preview"
                    >
                      🎬 FFmpeg Frame
                    </button>
                  </div>

                  <div className="flex flex-wrap gap-1">
                    {[
                      "මගේ අභියස ලෝකයක් තියා",
                      "වටිනම දෙය තෝරගන් කියා",
                      "ජීවිතය විඳගන්න",
                      "හැමදාටම පලඳන්නෙමි මා",
                    ].map((phrase) => (
                      <button
                        key={phrase}
                        type="button"
                        onClick={() => setFontPreviewSampleText(phrase)}
                        className={`rounded-md px-2 py-0.5 text-[10px] transition ${fontPreviewSampleText === phrase
                          ? "bg-amber-400 text-black font-bold"
                          : "bg-zinc-800/80 text-zinc-300 hover:bg-zinc-700"
                          }`}
                      >
                        {phrase}
                      </button>
                    ))}
                  </div>

                  {/* 100% Real Server-Side FFmpeg + libass Burn-In Preview Strip */}
                  {showServerFontRender && (
                    <div className="pt-1">
                      <div className="mb-1 flex items-center justify-between text-[10px] text-zinc-400">
                        <span>🎬 Exact FFmpeg `libass` Video Burn-In Render:</span>
                        <span className="text-emerald-400 font-semibold">100% Pixel-Accurate</span>
                      </div>
                      <div className="overflow-hidden rounded-lg border border-zinc-800 bg-black">
                        <img
                          src={ffmpegPreviewImgUrl}
                          alt={`FFmpeg preview for ${selectedFontFamily}`}
                          className="h-14 w-full object-cover"
                        />
                      </div>
                    </div>
                  )}
                </div>
              </div>

              {/* 👆 CLICKABLE VISUAL FONT GALLERY (STARRED TOP + NEWLY ADDED HIGHLIGHTED) */}
              <div>
                <div className="mb-1.5 flex items-center justify-between gap-2">
                  <label className="text-xs font-bold text-zinc-200">
                    👆 Fonts ({installedFonts.length})
                    {starredFonts.length > 0 && (
                      <span className="ml-1.5 rounded-full bg-amber-400/20 px-1.5 py-0.5 text-[10px] text-amber-300">
                        ★ {starredFonts.length} Starred
                      </span>
                    )}
                  </label>
                  <span className="text-[10px] text-zinc-400">★ Pinned Top • ✨ New</span>
                </div>

                <div className="max-h-72 space-y-1.5 overflow-y-auto pr-1">
                  {sortedInstalledFonts.map((f, idx) => {
                    const isSelected = selectedFontFamily === f.family;
                    const isStarred = starredFonts.includes(f.family);
                    const isNew = isFontNew(f);
                    const cardSample =
                      fontPreviewSampleText.trim() ||
                      f.sample_si ||
                      "මගේ අභියස ලෝකයක් තියා";
                    return (
                      <div
                        key={`${f.family}-${idx}`}
                        role="button"
                        tabIndex={0}
                        onClick={() => handleSelectFont(f)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            handleSelectFont(f);
                          }
                        }}
                        className={`group w-full cursor-pointer rounded-xl border p-2.5 text-left transition-all ${isSelected
                          ? "border-amber-400 bg-amber-400/12 shadow-md shadow-amber-400/10"
                          : isNew
                            ? "border-emerald-400/70 bg-emerald-500/10 ring-1 ring-emerald-400/25 hover:border-emerald-300 hover:bg-emerald-500/15"
                            : isStarred
                              ? "border-amber-500/40 bg-amber-500/5 hover:border-amber-400/70 hover:bg-zinc-900"
                              : "border-zinc-800/90 bg-zinc-950/90 hover:border-zinc-600 hover:bg-zinc-900"
                          }`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <div className="flex items-center gap-1.5 min-w-0">
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                toggleStarFont(f.family);
                              }}
                              title={isStarred ? "Unstar font" : "Star font (pin to top of list)"}
                              className={`shrink-0 rounded-md p-0.5 transition ${isStarred
                                ? "text-amber-400 hover:text-amber-300"
                                : "text-zinc-500 hover:text-amber-300"
                                }`}
                            >
                              <Star
                                className={`h-3.5 w-3.5 ${isStarred ? "fill-amber-400" : ""}`}
                              />
                            </button>
                            <span
                              className={`truncate text-xs font-bold ${isSelected
                                ? "text-amber-300"
                                : isNew
                                  ? "text-emerald-200 group-hover:text-white"
                                  : "text-zinc-200 group-hover:text-white"
                                }`}
                            >
                              {f.family}
                            </span>
                            {isNew && (
                              <span className="shrink-0 rounded-full border border-emerald-400/60 bg-emerald-400/20 px-1.5 py-0.2 text-[9px] font-extrabold text-emerald-300">
                                ✨ NEW
                              </span>
                            )}
                            {f.style_tag && (
                              <span
                                className={`shrink-0 rounded px-1.5 py-0.5 text-[9px] font-medium ${f.encoding === "wijesekara_fm"
                                  ? "border border-violet-400/35 bg-violet-500/15 text-violet-300"
                                  : f.encoding === "isi"
                                    ? "border border-rose-400/35 bg-rose-500/15 text-rose-300"
                                    : "bg-zinc-800 text-zinc-400"
                                  }`}
                              >
                                {f.style_tag}
                              </span>
                            )}
                          </div>
                          <div className="flex shrink-0 items-center gap-1.5">
                            <span
                              className={`rounded-full px-2 py-0.5 text-[10px] font-extrabold ${isSelected
                                ? "bg-amber-400 text-black"
                                : "border border-zinc-700 bg-zinc-900 text-zinc-400 group-hover:border-amber-400/50 group-hover:text-amber-300"
                                }`}
                            >
                              {isSelected ? "✓ Active" : "Preview"}
                            </span>
                            {f.source !== "system" && (
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleDeleteFont(f);
                                }}
                                title={`Delete custom font (${f.filename})`}
                                className="rounded-md border border-transparent p-1 text-zinc-500 transition hover:border-red-500/40 hover:bg-red-500/15 hover:text-red-400"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            )}
                          </div>
                        </div>

                        {/* Real Font Face Rendered Sample Inside Each Card */}
                        <div
                          className="mt-1.5 truncate text-base text-white"
                          style={{
                            fontFamily: getCssFontFamily(f.family),
                            fontWeight: f.family.toLowerCase().includes("regular")
                              ? 400
                              : boldText
                                ? 700
                                : 400,
                            fontStyle: italicText ? "italic" : "normal",
                          }}
                        >
                          {formatTextForFontEntry(cardSample, f)}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              <div>
                <label className="mb-1 block text-xs font-medium text-zinc-300">
                  Install Direct .TTF / .OTF URL from Internet
                </label>
                <div className="flex gap-1.5">
                  <input
                    type="url"
                    value={customFontUrl}
                    onChange={(e) => setCustomFontUrl(e.target.value)}
                    placeholder="https://example.com/sinhala-font.ttf"
                    className="flex-1 rounded-xl border border-zinc-800 bg-zinc-950 px-3 py-1.5 text-xs text-zinc-100 placeholder-zinc-500"
                  />
                  <button
                    type="button"
                    disabled={isLoadingFont || !customFontUrl.trim()}
                    onClick={() => handleDownloadFont(undefined, customFontUrl)}
                    className="rounded-xl bg-zinc-800 px-3 py-1.5 text-xs font-bold text-amber-300 hover:bg-zinc-700 disabled:opacity-40"
                  >
                    Install
                  </button>
                </div>
              </div>

              {onlineFontCatalog.length > 0 && (
                <div className="rounded-xl border border-zinc-800 bg-zinc-950 p-3 space-y-2">
                  <div className="text-[10px] font-bold uppercase tracking-wider text-zinc-400">
                    1-Click Online Sinhala Font Pack:
                  </div>
                  <div className="grid grid-cols-1 gap-1.5">
                    {onlineFontCatalog.map((item) => {
                      const alreadyInstalled = installedFonts.some(
                        (f) => f.family.toLowerCase() === item.family.toLowerCase()
                      );
                      const isCurrent =
                        selectedFontFamily.toLowerCase() === item.family.toLowerCase();
                      return (
                        <button
                          key={item.id}
                          type="button"
                          disabled={isLoadingFont}
                          onClick={() => {
                            const found = installedFonts.find(
                              (f) => f.family.toLowerCase() === item.family.toLowerCase()
                            );
                            if (found) {
                              handleSelectFont(found);
                            } else {
                              handleDownloadFont(item.id);
                            }
                          }}
                          className={`flex items-center justify-between rounded-lg border px-2.5 py-1.5 text-left transition ${isCurrent
                            ? "border-amber-400 bg-amber-400/15 text-amber-200"
                            : "border-zinc-800 bg-zinc-900 text-zinc-200 hover:border-amber-400/60"
                            }`}
                        >
                          <div className="min-w-0">
                            <div className="truncate text-[11px] font-semibold">
                              {item.label}
                            </div>
                            <div
                              className="truncate text-sm text-zinc-300"
                              style={{ fontFamily: `"${item.family}", sans-serif` }}
                            >
                              {item.sample_si || "මගේ අභියස ලෝකයක් තියා"}
                            </div>
                          </div>
                          <span className="ml-2 shrink-0 rounded bg-zinc-800 px-2 py-0.5 text-[10px] font-bold text-amber-300">
                            {isCurrent ? "✓ Active" : alreadyInstalled ? "Use Font" : "⬇ Install"}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}

              {fontStatusMsg && (
                <div className="rounded-lg border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-xs text-amber-300">
                  {fontStatusMsg}
                </div>
              )}

              <div className="rounded-xl border border-zinc-800 bg-zinc-950 p-3.5 space-y-3">
                <div>
                  <label className="mb-1 block text-xs font-medium text-zinc-300">
                    Lyrics Reveal Animation Style
                  </label>
                  <select
                    value={stylePreset}
                    onChange={(e) => setStylePreset(e.target.value as StylePreset)}
                    className="w-full rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-xs text-zinc-100"
                  >
                    <option value="rw_aesthetic_sinhala">
                      The_Black Hart Progressive Word-by-Word Fade
                    </option>
                    <option value="soft_line_fade">Soft Whole-Line Fade &amp; Glow</option>
                    <option value="tiktok_yellow">CapCut Yellow &amp; White Bold Pop</option>
                    <option value="capcut_white_pop">CapCut Bold White Pop</option>
                    <option value="neon_cyan">Neon Cyan Glow</option>
                  </select>
                </div>

                <div>
                  <label className="mb-1 flex justify-between text-[11px] text-zinc-400">
                    <span>Font Size</span>
                    <span className="font-mono text-amber-400">{fontSize}px</span>
                  </label>
                  <input
                    type="range"
                    min="22"
                    max="76"
                    step="2"
                    value={fontSize}
                    onChange={(e) => setFontSize(parseInt(e.target.value, 10))}
                    className="w-full accent-amber-400"
                  />
                </div>

                <div>
                  <div className="mb-1 flex items-center justify-between text-[11px] text-zinc-400">
                    <span>Font Thickness (මහත)</span>
                    <div className="flex items-center gap-2">
                      {fontThickness > 0 && (
                        <button
                          type="button"
                          onClick={() => setFontThickness(0)}
                          className="text-[10px] text-zinc-400 underline hover:text-zinc-200"
                        >
                          Reset
                        </button>
                      )}
                      <span className="font-mono text-amber-400">
                        {fontThickness === 0 ? "Normal" : `+${fontThickness.toFixed(1)}`}
                      </span>
                    </div>
                  </div>
                  <input
                    type="range"
                    min="0"
                    max="3"
                    step="0.2"
                    value={fontThickness}
                    onChange={(e) => setFontThickness(parseFloat(e.target.value))}
                    className="w-full accent-amber-400"
                  />
                  <div className="mt-0.5 flex justify-between text-[9px] text-zinc-500">
                    <span>Thin / Normal</span>
                    <span>Medium</span>
                    <span>Bold / Thick</span>
                  </div>
                </div>

                <div>
                  <label className="mb-1 flex justify-between text-[11px] text-zinc-400">
                    <span>Vertical Y Position</span>
                    <span className="font-mono text-amber-400">{textPosYPercent}%</span>
                  </label>
                  <input
                    type="range"
                    min="20"
                    max="85"
                    step="5"
                    value={textPosYPercent}
                    onChange={(e) => setTextPosYPercent(parseInt(e.target.value, 10))}
                    className="w-full accent-amber-400"
                  />
                </div>

                <div>
                  <label className="mb-1 flex justify-between text-[11px] text-zinc-400">
                    <span>Dark Glow / Shadow Depth</span>
                    <span className="font-mono text-amber-400">{shadowDepth}</span>
                  </label>
                  <input
                    type="range"
                    min="0"
                    max="6"
                    step="0.4"
                    value={shadowDepth}
                    onChange={(e) => setShadowDepth(parseFloat(e.target.value))}
                    className="w-full accent-amber-400"
                  />
                </div>

                <div className="flex items-center justify-between pt-1">
                  <div className="flex items-center gap-2">
                    <input
                      type="color"
                      value={primaryColorHex}
                      onChange={(e) => setPrimaryColorHex(e.target.value)}
                      className="h-7 w-10 cursor-pointer rounded border border-zinc-700 bg-zinc-900"
                    />
                    <span className="text-xs text-zinc-400">Text Color</span>
                  </div>
                  <div className="flex items-center gap-3">
                    <label className="flex items-center gap-1 text-xs text-zinc-300 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={boldText}
                        onChange={(e) => setBoldText(e.target.checked)}
                        className="accent-amber-400"
                      />
                      <b>Bold</b>
                    </label>
                    <label className="flex items-center gap-1 text-xs text-zinc-300 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={italicText}
                        onChange={(e) => setItalicText(e.target.checked)}
                        className="accent-amber-400"
                      />
                      <i>Italic</i>
                    </label>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* TAB 5: CANVAS FRAMING, COLOR GRADING & TRANSITIONS */}
          {activeSidebarTab === "effects" && (
            <div className="space-y-4">
              <div>
                <h2 className="text-sm font-bold text-white">
                  🎨 9:16 Canvas Framing &amp; Color Grading
                </h2>
                <p className="text-xs text-zinc-400">
                  Configure the The_Black Hart rounded card, color filters, and blur transitions.
                </p>
              </div>

              <div className="space-y-3 rounded-xl border border-zinc-800 bg-zinc-950 p-3.5">
                <div>
                  <label className="mb-1 block text-xs font-medium text-zinc-300">
                    Canvas &amp; Card Layout
                  </label>
                  <select
                    value={frameLayout}
                    onChange={(e) => setFrameLayout(e.target.value as FrameLayout)}
                    className="w-full rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-xs text-zinc-100"
                  >
                    <option value="rounded_square_card">
                      9:16 Black Canvas + Rounded Square Card (The_Black Hart)
                    </option>
                    <option value="rounded_portrait_card">
                      9:16 Black Canvas + Rounded 4:5 Portrait Card
                    </option>
                    <option value="fullscreen_vertical">
                      9:16 Fullscreen Vertical (No Black Borders)
                    </option>
                  </select>
                </div>

                <div>
                  <label className="mb-1 block text-xs font-medium text-zinc-300">
                    Color Grading Filter Preset
                  </label>
                  <select
                    value={colorGrade}
                    onChange={(e) => setColorGrade(e.target.value as ColorGradePreset)}
                    className="w-full rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-xs text-zinc-100"
                  >
                    <option value="golden_sunset">Golden Sunset &amp; Silhouette (The_Black Hart)</option>
                    <option value="moody_cinema">Moody Cinema High-Contrast</option>
                    <option value="dreamy_pastel">Dreamy Soft Glow</option>
                    <option value="black_and_white">Classic Black &amp; White</option>
                    <option value="original">Original AI Video Colors</option>
                  </select>
                </div>

                {/* Watermark Text, Size & Position Adjustment Section */}
                <div className="space-y-2.5 rounded-xl border border-zinc-800/90 bg-zinc-900/50 p-3">
                  <div className="flex items-center justify-between">
                    <label className="text-xs font-bold text-amber-300">
                      🏷️ Watermark Size &amp; Position
                    </label>
                    <button
                      type="button"
                      onClick={() => {
                        setWatermarkSize(15);
                        setWatermarkPosXPercent(50);
                        setWatermarkPosYPercent(98);
                      }}
                      className="text-[10px] font-semibold text-zinc-400 hover:text-amber-300"
                    >
                      Reset Default
                    </button>
                  </div>

                  <input
                    type="text"
                    value={watermarkText}
                    onChange={(e) => setWatermarkText(e.target.value)}
                    placeholder="The_Black Hart 🖤"
                    className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-1.5 text-xs text-zinc-100"
                  />

                  <div>
                    <label className="mb-0.5 flex justify-between text-[11px] text-zinc-400">
                      <span>Watermark Font Size</span>
                      <span className="font-mono text-amber-400">{watermarkSize}px</span>
                    </label>
                    <input
                      type="range"
                      min="8"
                      max="44"
                      step="1"
                      value={watermarkSize}
                      onChange={(e) => setWatermarkSize(parseInt(e.target.value, 10))}
                      className="w-full accent-amber-400"
                    />
                  </div>

                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="mb-0.5 flex justify-between text-[10px] text-zinc-400">
                        <span>Position X</span>
                        <span className="font-mono text-amber-400">{watermarkPosXPercent}%</span>
                      </label>
                      <input
                        type="range"
                        min="10"
                        max="90"
                        step="1"
                        value={watermarkPosXPercent}
                        onChange={(e) => setWatermarkPosXPercent(parseInt(e.target.value, 10))}
                        className="w-full accent-amber-400"
                      />
                    </div>
                    <div>
                      <label className="mb-0.5 flex justify-between text-[10px] text-zinc-400">
                        <span>Position Y</span>
                        <span className="font-mono text-amber-400">{watermarkPosYPercent}%</span>
                      </label>
                      <input
                        type="range"
                        min="5"
                        max="99"
                        step="1"
                        value={watermarkPosYPercent}
                        onChange={(e) => setWatermarkPosYPercent(parseInt(e.target.value, 10))}
                        className="w-full accent-amber-400"
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-3 gap-1 pt-0.5">
                    {[
                      { label: "↖ Top-L", x: 18, y: 7 },
                      { label: "↑ Top-C", x: 50, y: 7 },
                      { label: "↗ Top-R", x: 82, y: 7 },
                      { label: "↙ Bot-L", x: 18, y: 97 },
                      { label: "↓ Bot-C", x: 50, y: 98 },
                      { label: "↘ Bot-R", x: 82, y: 97 },
                    ].map((pos) => {
                      const active =
                        watermarkPosXPercent === pos.x && watermarkPosYPercent === pos.y;
                      return (
                        <button
                          key={pos.label}
                          type="button"
                          onClick={() => {
                            setWatermarkPosXPercent(pos.x);
                            setWatermarkPosYPercent(pos.y);
                          }}
                          className={`rounded-md border px-1.5 py-1 text-[10px] font-semibold transition ${active
                            ? "border-amber-400 bg-amber-400/20 text-amber-300"
                            : "border-zinc-800 bg-zinc-950 text-zinc-400 hover:border-zinc-600 hover:text-zinc-200"
                            }`}
                        >
                          {pos.label}
                        </button>
                      );
                    })}
                  </div>
                </div>

                <div>
                  <label className="mb-1 flex justify-between text-[11px] text-zinc-400">
                    <span>Blur-Pulse Transition</span>
                    <span className="font-mono text-amber-400">
                      {blurInterval > 0 ? `Every ${blurInterval}s` : "Off"}
                    </span>
                  </label>
                  <input
                    type="range"
                    min="0"
                    max="10"
                    step="0.5"
                    value={blurInterval}
                    onChange={(e) => setBlurInterval(parseFloat(e.target.value))}
                    className="w-full accent-amber-400"
                  />
                </div>

                {/* Dedicated Font Brightness / Glow Control (Captions Only) */}
                <div className="rounded-xl border border-amber-400/25 bg-zinc-950/75 p-2.5 space-y-1.5">
                  <div className="flex items-center justify-between text-[11px]">
                    <span className="font-semibold text-zinc-200">
                      ☀️ Font Brightness (අකුරු දීප්තිය)
                    </span>
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono font-bold text-amber-400">
                        {Math.round(fontBrightness * 100)}%
                      </span>
                      {Math.abs(fontBrightness - 1.35) > 0.02 && (
                        <button
                          type="button"
                          onClick={() => setFontBrightness(1.35)}
                          className="rounded border border-zinc-700 bg-zinc-900 px-1.5 py-0.2 text-[9px] text-zinc-400 hover:border-amber-400 hover:text-amber-300"
                        >
                          Reset (135%)
                        </button>
                      )}
                    </div>
                  </div>
                  <input
                    type="range"
                    min="0.5"
                    max="2.0"
                    step="0.05"
                    value={fontBrightness}
                    onChange={(e) => setFontBrightness(parseFloat(e.target.value))}
                    className="w-full accent-amber-400"
                  />
                  <div className="grid grid-cols-5 gap-1 pt-0.5">
                    {[
                      { label: "75%", val: 0.75 },
                      { label: "100%", val: 1.0 },
                      { label: "135%", val: 1.35 },
                      { label: "170%", val: 1.7 },
                      { label: "200%", val: 2.0 },
                    ].map((preset) => {
                      const active = Math.abs(fontBrightness - preset.val) < 0.03;
                      return (
                        <button
                          key={preset.label}
                          type="button"
                          onClick={() => setFontBrightness(preset.val)}
                          className={`rounded border py-0.5 text-[10px] font-semibold transition ${active
                            ? "border-amber-400 bg-amber-400/20 text-amber-300"
                            : "border-zinc-800 bg-zinc-900 text-zinc-400 hover:border-zinc-700 hover:text-zinc-200"
                            }`}
                        >
                          {preset.label}
                        </button>
                      );
                    })}
                  </div>
                </div>

                <div>
                  <label className="mb-1 flex justify-between text-[11px] text-zinc-400">
                    <span>Card Corner Radius</span>
                    <span className="font-mono text-amber-400">{cornerRadius}px</span>
                  </label>
                  <input
                    type="range"
                    min="0"
                    max="96"
                    step="4"
                    value={cornerRadius}
                    onChange={(e) => setCornerRadius(parseInt(e.target.value, 10))}
                    className="w-full accent-amber-400"
                  />
                </div>

                <div className="space-y-1.5 pt-1">
                  <div className="flex items-center justify-between text-[10px] text-zinc-400">
                    <span className="font-semibold text-zinc-300">
                      🎬 Video Only Color &amp; Brightness (Without Captions)
                    </span>
                    {(Math.abs(brightness - -0.3) > 0.02 || contrast !== 1 || saturation !== 1) && (
                      <button
                        type="button"
                        onClick={() => {
                          setBrightness(-0.3);
                          setContrast(1);
                          setSaturation(1);
                        }}
                        className="rounded border border-zinc-700 bg-zinc-900 px-1.5 py-0.2 text-[9px] text-zinc-400 hover:border-amber-400 hover:text-amber-300"
                      >
                        Reset Video EQ
                      </button>
                    )}
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    <div>
                      <label className="mb-1 flex justify-between text-[10px] text-zinc-400">
                        <span>Video Bright</span>
                        <span className="font-mono text-amber-400">
                          {brightness > 0 ? `+${brightness}` : brightness}
                        </span>
                      </label>
                      <input
                        type="range"
                        min="-0.5"
                        max="0.5"
                        step="0.05"
                        value={brightness}
                        onChange={(e) => setBrightness(parseFloat(e.target.value))}
                        className="w-full accent-amber-400"
                      />
                    </div>
                    <div>
                      <label className="mb-1 flex justify-between text-[10px] text-zinc-400">
                        <span>Contrast</span>
                        <span className="font-mono text-amber-400">{contrast}x</span>
                      </label>
                      <input
                        type="range"
                        min="0.7"
                        max="1.5"
                        step="0.05"
                        value={contrast}
                        onChange={(e) => setContrast(parseFloat(e.target.value))}
                        className="w-full accent-amber-400"
                      />
                    </div>
                    <div>
                      <label className="mb-1 flex justify-between text-[10px] text-zinc-400">
                        <span>Saturate</span>
                        <span className="font-mono text-amber-400">{saturation}x</span>
                      </label>
                      <input
                        type="range"
                        min="0.0"
                        max="1.8"
                        step="0.05"
                        value={saturation}
                        onChange={(e) => setSaturation(parseFloat(e.target.value))}
                        className="w-full accent-amber-400"
                      />
                    </div>
                  </div>
                </div>

                <div className="flex items-center justify-between pt-1">
                  <label className="flex items-center gap-1.5 text-xs text-zinc-300 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={vignette}
                      onChange={(e) => setVignette(e.target.checked)}
                      className="h-3.5 w-3.5 rounded accent-amber-400"
                    />
                    Dark Vignette
                  </label>
                  <label className="flex items-center gap-1.5 text-xs text-zinc-300 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={fadeInOut}
                      onChange={(e) => setFadeInOut(e.target.checked)}
                      className="h-3.5 w-3.5 rounded accent-amber-400"
                    />
                    Video Fade In/Out
                  </label>
                </div>
              </div>
            </div>
          )}

          {/* TAB 6: RENDER & EXPORT PIPELINE STATUS */}
          {activeSidebarTab === "export" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-bold text-white">🚀 Render &amp; Export MP4</h2>
                {job && (
                  <span className="font-mono text-xs font-bold text-amber-400">
                    {job.progress}%
                  </span>
                )}
              </div>

              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                <button
                  type="button"
                  disabled={isSubmitting}
                  onClick={() => handleSubmit(undefined, false)}
                  className="flex w-full items-center justify-center gap-2 rounded-xl bg-amber-400 px-4 py-3 text-xs font-extrabold text-black shadow-lg shadow-amber-400/20 transition hover:bg-amber-300 disabled:opacity-50"
                >
                  {isSubmitting && !job?.is_draft ? (
                    <>
                      <span className="h-4 w-4 animate-spin rounded-full border-2 border-black border-t-transparent" />
                      Rendering Final...
                    </>
                  ) : (
                    "🎬 Render Final Video"
                  )}
                </button>
                <button
                  type="button"
                  disabled={isSubmitting}
                  onClick={() => handleSubmit(undefined, true)}
                  className="flex w-full items-center justify-center gap-2 rounded-xl border border-violet-400/50 bg-violet-500/15 px-4 py-3 text-xs font-extrabold text-violet-200 transition hover:bg-violet-500/25 disabled:opacity-50"
                >
                  {isSubmitting && job?.is_draft ? (
                    <>
                      <span className="h-4 w-4 animate-spin rounded-full border-2 border-violet-200 border-t-transparent" />
                      Saving Draft...
                    </>
                  ) : (
                    "📝 Save Video as Draft"
                  )}
                </button>
              </div>

              {activeDraftFilename && (
                <div className="rounded-xl border border-violet-400/35 bg-violet-500/10 px-3 py-2 text-[11px] text-violet-200 flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="font-bold">Active Draft: <span className="font-mono text-violet-300">{activeDraftFilename}</span></div>
                    <div className="text-[10px] text-violet-300/80">
                      Clicking &quot;Render Final Video&quot; will remove this draft from the draft folder and save the final video to your original folder.
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setActiveDraftFilename(null)}
                    className="shrink-0 rounded-md border border-violet-400/30 px-2 py-0.5 text-[10px] text-violet-300 hover:bg-violet-500/20"
                  >
                    Detach
                  </button>
                </div>
              )}

              <div className="h-2 w-full overflow-hidden rounded-full bg-zinc-800">
                <div
                  className={`h-full transition-all duration-500 ${job?.stage === "failed" ? "bg-red-500" : "bg-amber-400"
                    }`}
                  style={{ width: `${job ? job.progress : 0}%` }}
                />
              </div>

              <p className="text-xs text-zinc-400">
                {job
                  ? job.message
                  : "Ready to render. Your short video will automatically loop smoothly to match the full song duration."}
              </p>

              {job?.error && (
                <div className="rounded-xl border border-red-500/40 bg-red-500/10 p-3 text-xs text-red-300">
                  <strong>Error:</strong> {job.error}
                </div>
              )}

              <div className="rounded-xl border border-emerald-500/25 bg-emerald-500/5 px-3 py-2 text-[11px] text-emerald-200 flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="font-bold flex items-center gap-1.5">
                    <FolderOpen className="h-3.5 w-3.5 text-emerald-400 shrink-0" />
                    Auto-Save Folder
                  </div>
                  <div className="truncate font-mono text-[10px] text-emerald-300/80">
                    {renderedVideosFolder}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    fetchRenderedVideos();
                    setActiveSidebarTab("videos");
                  }}
                  className="shrink-0 rounded-lg border border-emerald-400/40 bg-emerald-400/15 px-2.5 py-1 text-[10px] font-bold text-emerald-200 hover:bg-emerald-400/25"
                >
                  🎬 Videos ({renderedVideos.length})
                </button>
              </div>

              {job?.video_url && (
                <div className="flex flex-col gap-2 pt-1">
                  {job.is_draft && job.output_filename && (
                    <button
                      type="button"
                      disabled={promotingDraftFilename === job.output_filename}
                      onClick={() =>
                        handlePromoteDraft({
                          id: `draft:${job.output_filename}`,
                          filename: job.output_filename!,
                          job_id: job.job_id,
                          location: "draft",
                          is_draft: true,
                          size_mb: 0,
                          created_at: 0,
                          created_label: "",
                          saved_in_downloads: false,
                          downloads_path: "",
                          stream_url: job.video_url!,
                          download_url: `${job.video_url!}?download=1`,
                        })
                      }
                      className="w-full rounded-xl bg-gradient-to-r from-amber-400 to-orange-400 py-2.5 text-center text-xs font-extrabold text-black transition hover:brightness-105 disabled:opacity-50"
                    >
                      {promotingDraftFilename === job.output_filename
                        ? "Moving Draft to Original Folder..."
                        : "🚀 Render Draft to Original Folder"}
                    </button>
                  )}
                  <a
                    href={`${API_BASE}${job.video_url}${job.video_url.includes("?") ? "&" : "?"}download=1`}
                    download
                    className="w-full rounded-xl bg-emerald-400 py-2.5 text-center text-xs font-extrabold text-black transition hover:bg-emerald-300"
                  >
                    {job.is_draft ? "⬇ Download Draft MP4 Video" : "⬇ Download Final Synced MP4 Video"}
                  </a>
                  {job.lrc_content && (
                    <button
                      type="button"
                      onClick={handleDownloadLrc}
                      className="w-full rounded-xl border border-zinc-700 bg-zinc-800 py-2 text-xs font-semibold text-zinc-200 hover:bg-zinc-700"
                    >
                      ⬇ Download Synced .LRC File
                    </button>
                  )}
                </div>
              )}

              <div className="space-y-2 border-t border-zinc-800 pt-3">
                {PIPELINE_STEPS.map((step, index) => {
                  const thresholds = [10, 35, 65, 82];
                  const isDone =
                    job?.stage === "completed" ||
                    (job && job.progress > thresholds[index] + 15);
                  const isCurrent =
                    job &&
                    job.stage !== "completed" &&
                    job.stage !== "failed" &&
                    (job.stage === step.id ||
                      (step.id === "fetching_lyrics" && job.stage === "aligning_audio"));

                  return (
                    <div
                      key={step.id}
                      className={`flex items-start gap-2.5 rounded-xl border p-2.5 text-xs ${isCurrent
                        ? "border-amber-400/50 bg-amber-400/5"
                        : isDone
                          ? "border-emerald-500/30 bg-emerald-500/5"
                          : "border-zinc-800/60 bg-zinc-950/40 opacity-60"
                        }`}
                    >
                      <span
                        className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold ${isDone
                          ? "bg-emerald-500 text-black"
                          : isCurrent
                            ? "bg-amber-400 text-black animate-pulse"
                            : "bg-zinc-800 text-zinc-400"
                          }`}
                      >
                        {isDone ? "✓" : index + 1}
                      </span>
                      <div>
                        <div className="font-semibold text-zinc-200">{step.label}</div>
                        <div className="text-[11px] text-zinc-400">{step.desc}</div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* TAB 7: RENDERED VIDEOS & DRAFTS GALLERY */}
          {activeSidebarTab === "videos" && (() => {
            const finalCount = renderedVideos.filter((v) => !v.is_draft && v.location !== "draft").length;
            const draftCount = renderedVideos.filter((v) => v.is_draft || v.location === "draft").length;
            const visibleVideos = renderedVideos.filter((v) => {
              const isDraftItem = Boolean(v.is_draft || v.location === "draft");
              if (videoLibraryFilter === "draft") return isDraftItem;
              if (videoLibraryFilter === "final") return !isDraftItem;
              return true;
            });
            return (
              <div className="space-y-3">
                <div className="flex items-center justify-between gap-2">
                  <div>
                    <h2 className="text-sm font-bold text-white">🎬 Videos &amp; Drafts</h2>
                    <p className="text-[11px] text-zinc-400">
                      Final videos &amp; drafts saved in your Downloads folder.
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => fetchRenderedVideos()}
                    disabled={isLoadingRenderedVideos}
                    className="rounded-lg border border-zinc-700 bg-zinc-900 px-2.5 py-1 text-[11px] font-semibold text-zinc-200 hover:border-amber-400 hover:text-amber-300 disabled:opacity-50"
                  >
                    {isLoadingRenderedVideos ? "..." : "↻ Refresh"}
                  </button>
                </div>

                <div className="flex items-center gap-1.5 rounded-xl border border-zinc-800 bg-zinc-900/70 p-1">
                  {(
                    [
                      { id: "all", label: `All (${renderedVideos.length})` },
                      { id: "final", label: `Final (${finalCount})` },
                      { id: "draft", label: `📝 Drafts (${draftCount})` },
                    ] as const
                  ).map((flt) => (
                    <button
                      key={flt.id}
                      type="button"
                      onClick={() => setVideoLibraryFilter(flt.id)}
                      className={`flex-1 rounded-lg px-2 py-1 text-[11px] font-bold transition ${videoLibraryFilter === flt.id
                        ? flt.id === "draft"
                          ? "bg-violet-500 text-white"
                          : "bg-amber-400 text-black"
                        : "text-zinc-400 hover:text-zinc-200"
                        }`}
                    >
                      {flt.label}
                    </button>
                  ))}
                </div>

                <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-2.5 text-[11px] text-emerald-200 space-y-1.5">
                  <div>
                    <div className="flex items-center gap-1.5 font-bold">
                      <FolderOpen className="h-3.5 w-3.5 text-emerald-400 shrink-0" />
                      Original Videos Folder
                    </div>
                    <div className="mt-0.5 break-all font-mono text-[10px] text-emerald-300/90">
                      {renderedVideosFolder}
                    </div>
                  </div>
                  <div className="border-t border-emerald-500/20 pt-1.5">
                    <div className="flex items-center gap-1.5 font-bold text-violet-300">
                      <FolderOpen className="h-3.5 w-3.5 text-violet-400 shrink-0" />
                      Drafts Folder
                    </div>
                    <div className="mt-0.5 break-all font-mono text-[10px] text-violet-300/90">
                      {draftsFolder}
                    </div>
                  </div>
                </div>

                {visibleVideos.length === 0 ? (
                  <div className="rounded-xl border border-dashed border-zinc-800 bg-zinc-950/60 p-6 text-center text-xs text-zinc-400">
                    {videoLibraryFilter === "draft" ? (
                      <>
                        No draft videos yet. Click{" "}
                        <span className="font-bold text-violet-300">Save Draft</span> to save a draft
                        into <span className="font-mono text-zinc-300">draft</span> folder.
                      </>
                    ) : (
                      <>
                        No rendered videos yet. Click{" "}
                        <span className="font-bold text-amber-300">Render Video</span> or{" "}
                        <span className="font-bold text-violet-300">Save Draft</span> to create your
                        first MP4.
                      </>
                    )}
                  </div>
                ) : (
                  <div className="space-y-2.5">
                    {visibleVideos.map((vid) => {
                      const isDraftItem = Boolean(vid.is_draft || vid.location === "draft");
                      const isPromoting = promotingDraftFilename === vid.filename;
                      const isPlayingOnStage =
                        activeRenderedPlaybackUrl === vid.stream_url ||
                        selectedRenderedVideoUrl === vid.stream_url;
                      return (
                        <div
                          key={vid.id}
                          className={`flex gap-2.5 overflow-hidden rounded-xl border p-2.5 transition ${isPlayingOnStage
                            ? "border-emerald-400 bg-emerald-500/10"
                            : isDraftItem
                              ? "border-violet-500/40 bg-violet-950/20 hover:border-violet-400/60"
                              : "border-zinc-800 bg-zinc-950/90 hover:border-zinc-700"
                            }`}
                        >
                          <button
                            type="button"
                            onClick={() => playRenderedVideoInStage(vid.stream_url, vid.filename)}
                            title="Preview MP4 in Stage"
                            className="group relative aspect-[9/16] w-14 shrink-0 overflow-hidden rounded-lg border border-zinc-800 bg-black"
                          >
                            <video
                              src={`${API_BASE}${vid.stream_url}#t=0.4`}
                              preload="metadata"
                              muted
                              playsInline
                              onMouseEnter={(e) => {
                                e.currentTarget.play().catch(() => { });
                              }}
                              onMouseLeave={(e) => {
                                e.currentTarget.pause();
                              }}
                              className="h-full w-full object-cover"
                            />
                            <span className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/35 opacity-85 transition group-hover:bg-black/15">
                              <Play className="h-4 w-4 fill-white text-white drop-shadow" />
                            </span>
                          </button>

                          <div className="min-w-0 flex-1 flex flex-col justify-between">
                            <div className="flex items-start justify-between gap-2">
                              <div className="min-w-0 flex-1">
                                <div className="flex items-center gap-1.5">
                                  {isDraftItem && (
                                    <span className="shrink-0 rounded bg-violet-500/30 px-1.5 py-0.5 text-[9px] font-extrabold uppercase tracking-wider text-violet-200 ring-1 ring-violet-400/40">
                                      Draft
                                    </span>
                                  )}
                                  <div className="truncate text-xs font-bold text-white" title={vid.filename}>
                                    {vid.filename}
                                  </div>
                                </div>
                                <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10px] text-zinc-400">
                                  <span>{vid.size_mb} MB</span>
                                  <span>•</span>
                                  <span>{vid.created_label}</span>
                                  {isDraftItem ? (
                                    <span className="rounded bg-violet-500/20 px-1.5 py-0.2 font-semibold text-violet-300">
                                      📁 draft
                                    </span>
                                  ) : vid.saved_in_downloads ? (
                                    <span className="rounded bg-emerald-500/20 px-1.5 py-0.2 font-semibold text-emerald-300">
                                      ✓ Downloads
                                    </span>
                                  ) : null}
                                </div>
                              </div>
                              <button
                                type="button"
                                onClick={() => handleDeleteRenderedVideo(vid)}
                                title={isDraftItem ? "Delete draft video" : "Delete rendered video"}
                                className="shrink-0 rounded-lg border border-zinc-800 p-1.5 text-zinc-500 hover:border-red-500/40 hover:bg-red-500/10 hover:text-red-300"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            </div>

                            <div className="mt-2 flex flex-wrap items-center gap-1.5">
                              <>
                                <button
                                  type="button"
                                  disabled={loadingDraftFilename === vid.filename}
                                  onClick={() => handleLoadDraftForReEdit(vid)}
                                  title="Load this draft's settings, lyrics, song & video clips back into the editor to re-edit"
                                  className="flex-1 inline-flex items-center justify-center gap-1 rounded-lg border border-violet-400/50 bg-violet-500/20 px-2.5 py-1.5 text-[11px] font-extrabold text-violet-200 transition hover:bg-violet-500/35 disabled:opacity-50"
                                >
                                  <Wand2 className="h-3 w-3" />
                                  {loadingDraftFilename === vid.filename ? "Loading..." : "✏️ Re-Edit"}
                                </button>
                                <button
                                  type="button"
                                  disabled={isPromoting}
                                  onClick={() => handlePromoteDraft(vid)}
                                  title="Remove from draft folder and save to original videos folder"
                                  className="flex-1 inline-flex items-center justify-center gap-1 rounded-lg bg-gradient-to-r from-amber-400 to-orange-400 px-2.5 py-1.5 text-[11px] font-extrabold text-black transition hover:brightness-105 disabled:opacity-50"
                                >
                                  <Rocket className="h-3 w-3" />
                                  {isPromoting ? "Moving..." : "Render Draft"}
                                </button>
                              </>
                              {isDraftItem && (
                                <button
                                  type="button"
                                  disabled={isPromoting}
                                  onClick={() => handlePromoteDraft(vid)}
                                  title="Remove from draft folder and save to original videos folder"
                                  className="flex-1 inline-flex items-center justify-center gap-1 rounded-lg bg-gradient-to-r from-amber-400 to-orange-400 px-2.5 py-1.5 text-[11px] font-extrabold text-black transition hover:brightness-105 disabled:opacity-50"
                                >
                                  <Rocket className="h-3 w-3" />
                                  {isPromoting ? "Moving..." : "Render Draft"}
                                </button>
                              )}
                              <a
                                href={`${API_BASE}${vid.download_url}`}
                                download={vid.filename}
                                className="inline-flex items-center justify-center gap-1 rounded-lg border border-zinc-700 bg-zinc-800 px-2.5 py-1.5 text-[11px] font-semibold text-zinc-200 hover:border-emerald-400 hover:text-emerald-300"
                              >
                                <Download className="h-3 w-3" />
                                MP4
                              </a>
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })()}
        </aside>

        {/* 3. CENTER VIDEO PREVIEW STAGE (WITH FULLSCREEN & TRANSPORT BAR) */}
        <main
          ref={previewStageRef}
          className={`nle-stage-grid flex flex-1 flex-col items-center justify-between bg-zinc-950 p-3 sm:p-4 ${isFullscreenModal ? "fixed inset-0 z-50 bg-black p-6" : " relative overflow-hidden"
            }`}
        >
          {/* Preview Top Bar: Mode Switcher + Monitor Telemetry + Safe Zone + Fullscreen Controls */}
          <div className="z-10 flex w-full max-w-3xl flex-wrap items-center justify-between gap-2 rounded-2xl border border-zinc-800/80 bg-zinc-950/85 px-3 py-1.5 shadow-lg backdrop-blur-md">
            <div className="inline-flex rounded-xl border border-zinc-800 bg-zinc-900/90 p-0.5">
              <button
                type="button"
                onClick={() => setActivePreviewTab("canvas")}
                className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1 text-xs font-semibold transition ${activePreviewTab === "canvas"
                  ? "bg-amber-400 text-black shadow-sm"
                  : "text-zinc-400 hover:text-white"
                  }`}
              >
                <Smartphone className="h-3.5 w-3.5" />
                9:16 Live Canvas
              </button>
              <button
                type="button"
                onClick={() => {
                  audioRef.current?.pause();
                  setRenderedVideoError(null);
                  setActivePreviewTab("rendered");
                }}
                disabled={!activeRenderedPlaybackUrl}
                className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1 text-xs font-semibold transition ${activePreviewTab === "rendered"
                  ? "bg-emerald-400 text-black shadow-sm"
                  : "text-zinc-400 hover:text-white disabled:opacity-40"
                  }`}
              >
                <Film className="h-3.5 w-3.5" />
                Rendered MP4 {activeRenderedPlaybackUrl ? "✓" : ""}
              </button>
            </div>

            {/* Center Monitor Telemetry Readout */}
            <div className="hidden items-center gap-1.5 text-[10px] font-mono text-zinc-400 md:flex">
              <span className="rounded-md border border-zinc-800 bg-zinc-900/90 px-2 py-0.5 text-zinc-300">
                1080×1920 • 30fps
              </span>
              <span className="rounded-md border border-amber-400/25 bg-amber-400/10 px-2 py-0.5 text-amber-300">
                {activeFontEntry?.encoding === "wijesekara_fm"
                  ? "FM Wijesekara"
                  : "Unicode Sinhala"}
              </span>
              {fontThickness > 0 && (
                <span className="rounded-md border border-sky-400/30 bg-sky-400/10 px-1.5 py-0.5 text-sky-300">
                  +{fontThickness.toFixed(1)} Wt
                </span>
              )}
            </div>

            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => setShowTikTokSafeZone((prev) => !prev)}
                title="Toggle TikTok UI Safe-Zone Guide (won't appear in export)"
                className={`inline-flex items-center gap-1.5 rounded-xl border px-2.5 py-1 text-xs font-semibold transition ${showTikTokSafeZone
                  ? "border-sky-400/50 bg-sky-400/15 text-sky-300"
                  : "border-zinc-800 bg-zinc-900 text-zinc-400 hover:text-white"
                  }`}
              >
                <ShieldCheck className="h-3.5 w-3.5" />
                Safe Zone
              </button>
              <button
                type="button"
                onClick={() => setIsFullscreenModal((prev) => !prev)}
                className="inline-flex items-center gap-1.5 rounded-xl border border-zinc-800 bg-zinc-900 px-2.5 py-1 text-xs font-semibold text-zinc-200 hover:border-amber-400 hover:text-amber-300"
              >
                {isFullscreenModal ? (
                  <>
                    <Minimize2 className="h-3.5 w-3.5" />
                    Exit Theater
                  </>
                ) : (
                  <>
                    <Maximize2 className="h-3.5 w-3.5" />
                    Theater
                  </>
                )}
              </button>
              <button
                type="button"
                onClick={toggleFullscreenPreview}
                className="inline-flex items-center gap-1.5 rounded-xl border border-amber-400/40 bg-amber-400/15 px-2.5 py-1 text-xs font-bold text-amber-300 hover:bg-amber-400/25"
              >
                <Expand className="h-3.5 w-3.5" />
                Fullscreen
              </button>
            </div>
          </div>

          {/* Center 9:16 Phone Monitor */}
          <div className="relative my-2 flex flex-1 items-center justify-center w-full overflow-hidden">
            {/* Soft Ambient Studio Radial Glow Behind Phone */}
            <div className="pointer-events-none absolute h-72 w-72 rounded-full bg-amber-500/10 blur-3xl" />

            {activePreviewTab === "canvas" ? (
              <div
                onDragOver={(e) => {
                  e.preventDefault();
                  setIsDraggingClip(true);
                }}
                onDragLeave={() => setIsDraggingClip(false)}
                onDrop={handleDropClips}
                className={`relative flex aspect-[9/16] flex-col items-center justify-center overflow-hidden rounded-[26px] border bg-black shadow-2xl transition-all ${isDraggingClip ? "border-amber-400 ring-2 ring-amber-400/40" : "border-zinc-800"
                  } ${isFullscreenModal
                    ? "h-[80vh] max-h-[820px]"
                    : "h-full max-h-[430px] xl:max-h-[500px]"
                  }`}
              >
                <div
                  ref={previewCardRef}
                  className="relative flex items-center justify-center overflow-hidden bg-zinc-900 shadow-2xl transition-all duration-300"
                  style={{
                    width: frameLayout === "fullscreen_vertical" ? "100%" : "85.185%",
                    aspectRatio:
                      frameLayout === "rounded_square_card"
                        ? "1 / 1"
                        : frameLayout === "rounded_portrait_card"
                          ? "4 / 5"
                          : "9 / 16",
                    height: frameLayout === "fullscreen_vertical" ? "100%" : "auto",
                    borderRadius: `${frameLayout === "fullscreen_vertical"
                      ? 0
                      : Math.round(cornerRadius * liveCanvasScale)
                      }px`,
                  }}
                >
                  {previewClipUrl ? (
                    <video
                      ref={previewVideoRef}
                      src={previewClipUrl}
                      autoPlay
                      loop
                      muted
                      playsInline
                      onLoadedMetadata={(e) => {
                        if (e.currentTarget.duration && isFinite(e.currentTarget.duration)) {
                          const dur = Number(e.currentTarget.duration.toFixed(2));
                          setDetectedClipDuration(dur);
                        }
                      }}
                      onTimeUpdate={(e) => {
                        if (visualClips.length > 1 && selectedSingleClipIdx === null) return;
                        if (effectiveVideoSegments.length === 0) return;
                        if (videoSegments.length === 0 && clipTrimStart <= 0 && clipTrimEnd <= 0) return;
                        const t = e.currentTarget.currentTime;
                        const inAnySeg = effectiveVideoSegments.some(
                          (s) => t >= s.start - 0.08 && t <= s.end + 0.08
                        );
                        if (!inAnySeg) {
                          const nextSeg =
                            effectiveVideoSegments.find((s) => s.start > t) ||
                            effectiveVideoSegments[0];
                          if (nextSeg) {
                            e.currentTarget.currentTime = nextSeg.start;
                          }
                        }
                      }}
                      className="h-full w-full object-cover transition-transform duration-300"
                      style={{
                        transform: `scale(${videoZoom}) scaleX(${mirrorFlip ? -1 : 1})`,
                        filter: buildLiveCssFilter(),
                      }}
                    />
                  ) : (
                    <div
                      className="flex h-full w-full flex-col items-center justify-center bg-gradient-to-b from-amber-500/90 via-orange-600/85 to-zinc-950 p-4 text-center"
                      style={{
                        transform: `scale(${videoZoom}) scaleX(${mirrorFlip ? -1 : 1})`,
                        filter: buildLiveCssFilter(),
                      }}
                    >
                      <Film className="mb-1.5 h-6 w-6 text-white/75" />
                      <span className="text-xs font-semibold text-white/90">
                        Drop AI Video Clip Here
                      </span>
                      <span className="mt-0.5 text-[10px] text-white/65">
                        or upload via Media &amp; Loop
                      </span>
                    </div>
                  )}

                  {vignette && (
                    <div
                      className="pointer-events-none absolute inset-0"
                      style={{
                        background:
                          "radial-gradient(circle, rgba(0,0,0,0) 48%, rgba(0,0,0,0.68) 100%)",
                      }}
                    />
                  )}

                  {watermarkText && (
                    <span
                      className="pointer-events-none absolute whitespace-nowrap italic text-white drop-shadow"
                      style={{
                        fontFamily: '"Georgia", serif',
                        opacity: 0.73,
                        left: `${watermarkPosXPercent}%`,
                        top: `${watermarkPosYPercent}%`,
                        transform: "translate(-50%, -50%)",
                        fontSize: `${Math.max(4, Number((watermarkSize * liveCanvasScale).toFixed(2)))}px`,
                      }}
                    >
                      {watermarkText}
                    </span>
                  )}

                  {/* Live Audio-Synced Sinhala Caption Overlay */}
                  <div
                    className="pointer-events-none absolute inset-x-0 flex justify-center text-center"
                    style={{
                      top: `${liveCaptionTopPct}%`,
                      transform: "translateY(-50%)",
                      paddingInline: `${liveCaptionPaddingInline}px`,
                    }}
                  >
                    {isAudioPlaying || currentAudioTime > 0 ? (
                      activeSyncedLine ? (() => {
                        const previewWords =
                          activeSyncedLine.words && activeSyncedLine.words.length > 0
                            ? activeSyncedLine.words
                            : buildWordTimingsForLine(
                              activeSyncedLine.text,
                              activeSyncedLine.start,
                              activeSyncedLine.end
                            );
                        const relMs =
                          (currentAudioTime -
                            (activeSyncedLine.start + lyricsOffsetSec)) *
                          1000;
                        const lineDurMs = Math.max(
                          400,
                          (activeSyncedLine.end - activeSyncedLine.start) * 1000
                        );
                        const fadeOutAlpha =
                          relMs > lineDurMs - 180
                            ? Math.max(0.05, (lineDurMs - relMs) / 180)
                            : 1;
                        return (
                          <p
                            className="leading-snug transition-opacity duration-150"
                            style={{
                              fontFamily: getCssFontFamily(selectedFontFamily),
                              fontSize: `${liveCaptionFontSizePx}px`,
                              color: effectiveLiveCaptionColor,
                              fontWeight: effectiveLiveFontWeight,
                              fontStyle: italicText ? "italic" : "normal",
                              WebkitTextStroke: liveCaptionTextStroke,
                              paintOrder: "stroke fill",
                              textShadow: liveCaptionTextShadow,
                            }}
                          >
                            {previewWords.length > 0
                              ? previewWords.map((wt, wIdx) => {
                                const wordAlpha =
                                  relMs <= wt.start_ms
                                    ? 0.08
                                    : relMs >= wt.end_ms
                                      ? 1.0
                                      : (relMs - wt.start_ms) /
                                      Math.max(wt.end_ms - wt.start_ms, 100);
                                return (
                                  <span
                                    key={wIdx}
                                    style={{
                                      opacity: Math.max(
                                        0.05,
                                        Math.min(1, wordAlpha * fadeOutAlpha)
                                      ),
                                      transition: "opacity 90ms linear",
                                    }}
                                  >
                                    {formatActiveFontText(wt.word)}{" "}
                                  </span>
                                );
                              })
                              : formatActiveFontText(activeSyncedLine.text)}
                          </p>
                        );
                      })() : null
                    ) : (
                      <p
                        key={previewLineIndex}
                        className="leading-snug transition-all"
                        style={{
                          fontFamily: getCssFontFamily(selectedFontFamily),
                          fontSize: `${liveCaptionFontSizePx}px`,
                          color: effectiveLiveCaptionColor,
                          fontWeight: effectiveLiveFontWeight,
                          fontStyle: italicText ? "italic" : "normal",
                          WebkitTextStroke: liveCaptionTextStroke,
                          paintOrder: "stroke fill",
                          textShadow: liveCaptionTextShadow,
                        }}
                      >
                        {formatActiveFontText(fallbackPreviewText)}
                      </p>
                    )}
                  </div>
                </div>
                {/* Optional Non-Exported TikTok UI Safe-Zone Overlay */}
                {showTikTokSafeZone && (
                  <div className="pointer-events-none absolute inset-0 z-20 flex flex-col justify-between p-2.5 text-[9px] text-sky-200/80">
                    <div className="mx-auto rounded-full border border-sky-400/40 bg-sky-500/15 px-2 py-0.5 text-[8px] font-bold uppercase tracking-wider">
                      TikTok Safe Zone Guide
                    </div>
                    <div className="flex items-end justify-between pb-1">
                      <div className="w-3/4 space-y-1 rounded border border-dashed border-sky-400/35 bg-sky-950/30 p-1.5">
                        <div className="h-1.5 w-16 rounded bg-sky-300/40" />
                        <div className="h-1.5 w-24 rounded bg-sky-300/25" />
                      </div>
                      <div className="flex flex-col items-center gap-1.5 rounded border border-dashed border-sky-400/35 bg-sky-950/30 p-1">
                        <div className="h-3 w-3 rounded-full bg-sky-300/35" />
                        <div className="h-3 w-3 rounded-full bg-sky-300/35" />
                        <div className="h-3 w-3 rounded-full bg-sky-300/35" />
                      </div>
                    </div>
                  </div>
                )}
              </div>
            ) : activeRenderedPlaybackUrl ? (
              <div className="flex h-full min-h-0 flex-col items-center justify-center gap-2">
                {(selectedRenderedVideoLabel || renderedVideos[0]?.filename) && (
                  <div className="shrink-0 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-0.5 text-[11px] font-semibold text-emerald-300">
                    🎬 {selectedRenderedVideoLabel || renderedVideos[0]?.filename}
                  </div>
                )}
                <div
                  className={`relative flex aspect-[9/16] items-center justify-center overflow-hidden rounded-[26px] border border-zinc-800 bg-black shadow-2xl ${isFullscreenModal
                    ? "h-[78vh] max-h-[820px]"
                    : "h-full max-h-[410px] xl:max-h-[475px]"
                    }`}
                >
                  {renderedVideoError ? (
                    <div className="flex flex-col items-center gap-2 p-4 text-center">
                      <AlertCircle className="h-7 w-7 text-red-400" />
                      <p className="text-xs text-red-300">{renderedVideoError}</p>
                      <button
                        type="button"
                        onClick={() => {
                          setRenderedVideoError(null);
                          if (renderedVideoRef.current) {
                            renderedVideoRef.current.load();
                            renderedVideoRef.current.play().catch(() => { });
                          }
                        }}
                        className="rounded-lg bg-amber-400 px-3 py-1.5 text-[11px] font-bold text-black hover:bg-amber-300"
                      >
                        Retry Video
                      </button>
                    </div>
                  ) : (
                    <video
                      key={activeRenderedPlaybackUrl}
                      ref={renderedVideoRef}
                      src={`${API_BASE}${activeRenderedPlaybackUrl}`}
                      controls
                      playsInline
                      preload="metadata"
                      onLoadedMetadata={() => {
                        setRenderedVideoError(null);
                        renderedVideoRef.current?.play().catch(() => { });
                      }}
                      onError={() => {
                        if (
                          job?.video_url &&
                          activeRenderedPlaybackUrl === job.video_url &&
                          renderedVideos.length > 0 &&
                          renderedVideos[0].stream_url !== job.video_url
                        ) {
                          setSelectedRenderedVideoUrl(renderedVideos[0].stream_url);
                          setSelectedRenderedVideoLabel(renderedVideos[0].filename);
                          return;
                        }
                        setRenderedVideoError(
                          "Could not load this rendered MP4 preview. Check that the backend server is running and the file still exists."
                        );
                      }}
                      className="h-full w-full bg-black object-contain"
                    />
                  )}
                </div>
              </div>
            ) : (
              <div className="text-xs text-zinc-400">
                No rendered video selected yet. Render a video or open 🎬 Videos to preview.
              </div>
            )}
          </div>

          {/* Synchronized Player Transport Bar below Preview */}
          <div className="relative z-10 flex w-full max-w-3xl flex-wrap items-center justify-between gap-3 rounded-2xl border border-zinc-800/90 bg-zinc-950/90 px-4 py-2 shadow-xl backdrop-blur-md">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={toggleMasterPlayPause}
                disabled={!songPreviewUrl}
                title={songPreviewUrl ? "Play/Pause (Space)" : "Fetch a song first to play"}
                className="flex h-8 w-8 items-center justify-center rounded-xl bg-gradient-to-br from-amber-300 to-amber-500 text-black shadow-md shadow-amber-500/20 transition hover:brightness-105 disabled:opacity-40"
              >
                {isAudioPlaying ? <Pause className="h-4 w-4 fill-current" /> : <Play className="h-4 w-4 fill-current ml-0.5" />}
              </button>
              <button
                type="button"
                disabled={!songPreviewUrl}
                onClick={() => seekAudioTo(currentAudioTime - 2)}
                className="rounded-lg border border-zinc-800 bg-zinc-900 px-2 py-1 text-[11px] font-medium text-zinc-300 hover:border-zinc-700 hover:text-white disabled:opacity-40"
              >
                -2s
              </button>
              <button
                type="button"
                disabled={!songPreviewUrl}
                onClick={() => seekAudioTo(currentAudioTime + 2)}
                className="rounded-lg border border-zinc-800 bg-zinc-900 px-2 py-1 text-[11px] font-medium text-zinc-300 hover:border-zinc-700 hover:text-white disabled:opacity-40"
              >
                +2s
              </button>
              <span className="tabular-timecode rounded-lg border border-zinc-800/90 bg-zinc-900/90 px-2.5 py-1 font-mono text-xs font-bold text-amber-300">
                {formatTimecode(currentAudioTime)}{" "}
                <span className="text-zinc-500">/ {formatTimecode(totalTimelineDuration)}</span>
              </span>
            </div>

            {/* Interactive Scrubber Slider */}
            <input
              type="range"
              min="0"
              max={totalTimelineDuration}
              step="0.05"
              value={currentAudioTime}
              onChange={(e) => seekAudioTo(parseFloat(e.target.value))}
              className="flex-1 min-w-[120px] accent-amber-400 cursor-pointer"
            />

            <div className="flex items-center gap-2 text-[11px]">
              {isBlurPulseActive && (
                <span className="rounded-lg border border-fuchsia-400/70 bg-fuchsia-500/25 px-2 py-1 font-bold text-fuchsia-200 animate-pulse">
                  💧 BLUR ACTIVE
                </span>
              )}
              {isFadeInActiveNow && (
                <span className="rounded-lg border border-sky-400/50 bg-sky-500/20 px-2 py-1 font-semibold text-sky-200">
                  🌅 FADE IN
                </span>
              )}
              {isFadeOutActiveNow && (
                <span className="rounded-lg border border-orange-400/50 bg-orange-500/20 px-2 py-1 font-semibold text-orange-200">
                  🌇 FADE OUT
                </span>
              )}
              {activeSyncedLine ? (
                <span className="max-w-[180px] truncate rounded-lg bg-emerald-500/15 border border-emerald-500/30 px-2.5 py-1 font-medium text-emerald-300">
                  ● {activeSyncedLine.text}
                </span>
              ) : (
                <span className="text-zinc-400">
                  {songPreviewUrl ? "Ready to Play" : "Load Song in Audio Tab"}
                </span>
              )}
              <button
                type="button"
                onClick={() => setShowShortcutsHelp((prev) => !prev)}
                title="Keyboard Shortcuts"
                className={`rounded-lg border p-1.5 transition ${showShortcutsHelp
                  ? "border-amber-400/50 bg-amber-400/15 text-amber-300"
                  : "border-zinc-800 bg-zinc-950 text-zinc-400 hover:text-white"
                  }`}
              >
                <Keyboard className="h-3.5 w-3.5" />
              </button>
            </div>

            {showShortcutsHelp && (
              <div className="animate-studio-pop absolute bottom-12 right-2 z-40 w-60 rounded-xl border border-zinc-700 bg-zinc-950/95 p-3 text-[11px] text-zinc-300 shadow-2xl backdrop-blur">
                <div className="mb-1.5 font-bold text-amber-300">Studio Keyboard Shortcuts</div>
                <div className="space-y-1">
                  <div className="flex justify-between"><span>Play / Pause</span><kbd className="rounded bg-zinc-800 px-1.5 font-mono text-[10px]">Space</kbd></div>
                  <div className="flex justify-between"><span>Seek ±2s</span><kbd className="rounded bg-zinc-800 px-1.5 font-mono text-[10px]">← / →</kbd></div>
                  <div className="flex justify-between"><span>Fine Seek ±0.2s</span><kbd className="rounded bg-zinc-800 px-1.5 font-mono text-[10px]">Shift + ← / →</kbd></div>
                  <div className="flex justify-between"><span>Snap Line Start</span><kbd className="rounded bg-zinc-800 px-1.5 font-mono text-[10px]">S</kbd></div>
                  <div className="flex justify-between"><span>Theater Mode</span><kbd className="rounded bg-zinc-800 px-1.5 font-mono text-[10px]">F</kbd></div>
                  <div className="flex justify-between"><span>Undo</span><kbd className="rounded bg-zinc-800 px-1.5 font-mono text-[10px]">⌘ / Ctrl + Z</kbd></div>
                  <div className="flex justify-between"><span>Redo</span><kbd className="rounded bg-zinc-800 px-1.5 font-mono text-[10px]">⌘⇧Z / Ctrl+Y</kbd></div>
                </div>
              </div>
            )}
          </div>
        </main>
      </div>

      {/* BOTTOM DOCK: ALWAYS-VISIBLE MULTI-TRACK TIMELINE EDITOR */}
      <section className="flex h-72 sm:h-80 shrink-0 flex-col border-t border-zinc-800 bg-zinc-900/95">
        {/* Timeline Top Toolbar */}
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-800 px-4 py-2 text-xs">
          <div className="flex items-center gap-2">
            <span className="font-extrabold uppercase tracking-wider text-amber-400">
              ⏱ Multi-Track Timeline
            </span>
            <div className="inline-flex rounded-lg border border-zinc-800 bg-zinc-950 p-0.5">
              <button
                type="button"
                onClick={() => setTimelineViewMode("visual")}
                className={`rounded-md px-2.5 py-1 text-[11px] font-semibold transition ${timelineViewMode === "visual"
                  ? "bg-amber-400 text-black"
                  : "text-zinc-400 hover:text-white"
                  }`}
              >
                📊 Visual Tracks
              </button>
              <button
                type="button"
                onClick={() => setTimelineViewMode("list")}
                className={`rounded-md px-2.5 py-1 text-[11px] font-semibold transition ${timelineViewMode === "list"
                  ? "bg-amber-400 text-black"
                  : "text-zinc-400 hover:text-white"
                  }`}
              >
                📝 Caption Timing Table ({editableTimeline.length})
              </button>
            </div>

            <button
              type="button"
              onClick={handleAddCaptionLine}
              className="rounded-lg border border-zinc-700 bg-zinc-800 px-2.5 py-1 text-[11px] font-semibold text-zinc-200 hover:border-amber-400 hover:text-amber-300"
            >
              + Add Line at {formatTimecode(currentAudioTime)}
            </button>

            <button
              type="button"
              disabled={editableTimeline.length === 0}
              onClick={() => handleSplitCaptionAtPlayhead()}
              title="Split the caption under the playhead into two lines"
              className="inline-flex items-center gap-1 rounded-lg border border-amber-400/40 bg-amber-400/15 px-2.5 py-1 text-[11px] font-bold text-amber-300 hover:bg-amber-400/25 disabled:opacity-40"
            >
              <Scissors className="h-3 w-3" />
              Split Caption
            </button>

            {/* Main Video Blade & Delete Controls on Timeline */}
            <div className="inline-flex items-center gap-1 rounded-lg border border-sky-500/35 bg-sky-500/10 px-1.5 py-0.5">
              <button
                type="button"
                disabled={visualClips.length === 0}
                onClick={() => handleBladeVideoAtTime()}
                title="Blade / Split the main video at the current playhead into two parts"
                className="inline-flex items-center gap-1 rounded bg-sky-500/25 px-2 py-0.5 text-[11px] font-bold text-sky-200 hover:bg-sky-500/40 disabled:opacity-40"
              >
                <Scissors className="h-3 w-3" />
                Blade Video
              </button>
              <button
                type="button"
                disabled={visualClips.length === 0}
                onClick={() => {
                  setIsVideoBladeMode((prev) => {
                    const next = !prev;
                    showToast(
                      next
                        ? "🔪 Blade Mode ON — click anywhere on the Video Track to cut!"
                        : "Blade Mode OFF"
                    );
                    return next;
                  });
                }}
                title="Toggle Razor/Blade cursor mode — click directly on the Video Track to cut"
                className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-[10px] font-bold transition disabled:opacity-40 ${isVideoBladeMode
                  ? "bg-amber-400 text-black shadow"
                  : "bg-zinc-900/90 text-sky-300 hover:bg-zinc-800"
                  }`}
              >
                🔪 {isVideoBladeMode ? "Blade ON" : "Razor"}
              </button>
              <button
                type="button"
                disabled={visualClips.length === 0}
                onClick={() => handleDeleteVideoSegment()}
                title={
                  effectiveVideoSegments.length > 1
                    ? "Delete selected bladed video part"
                    : "Delete main video clip"
                }
                className="inline-flex items-center gap-1 rounded bg-red-500/20 border border-red-500/40 px-2 py-0.5 text-[10px] font-bold text-red-200 hover:bg-red-500/35 disabled:opacity-40"
              >
                <Trash2 className="h-3 w-3" />
                {effectiveVideoSegments.length > 1
                  ? `Delete Part #${(selectedVideoSegmentIdx ?? 0) + 1}`
                  : "Delete Video"}
              </button>
              {(videoSegments.length > 1 || clipTrimStart > 0 || clipTrimEnd > 0) && (
                <button
                  type="button"
                  onClick={handleRestoreFullVideo}
                  title="Restore full uncut main video"
                  className="inline-flex items-center gap-0.5 rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] font-semibold text-zinc-300 hover:text-white"
                >
                  <RotateCcw className="h-2.5 w-2.5" />
                  Full
                </button>
              )}
            </div>

            {cachedSongId && (
              <div className="inline-flex flex-wrap items-center gap-1 rounded-lg border border-purple-500/35 bg-purple-500/10 px-1.5 py-0.5">
                <button
                  type="button"
                  onClick={() => {
                    const rawPos = Number((appliedAudioStartSec + currentAudioTime).toFixed(1));
                    const maxAllowed = rawSongDuration ? Math.max(0, rawSongDuration - 1.5) : rawPos;
                    const clamped = Math.min(rawPos, maxAllowed);
                    setAudioStartSec(clamped);
                    setAudioStartText(formatMmSs(clamped));
                    showToast(`Song In-point set to ${formatMmSs(clamped)}`);
                  }}
                  title="Mark where the song should start (In-point at playhead)"
                  className="rounded bg-zinc-900/90 px-2 py-0.5 font-mono text-[10px] font-bold text-emerald-300 hover:bg-emerald-500/20"
                >
                  [ In {formatMmSs(audioStartSec)}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    const rawPos = Number((appliedAudioStartSec + currentAudioTime).toFixed(1));
                    if (rawPos <= audioStartSec + 1.0) {
                      showToast("Move playhead past the Start In-point to set Out-point.");
                      return;
                    }
                    const nextDur = Number((rawPos - audioStartSec).toFixed(1));
                    setAudioMaxDurationSec(nextDur);
                    showToast(`Song Out-point set to ${formatMmSs(rawPos)} (${nextDur}s clip)`);
                  }}
                  title="Mark where the song should end (Out-point at playhead)"
                  className="rounded bg-zinc-900/90 px-2 py-0.5 font-mono text-[10px] font-bold text-sky-300 hover:bg-sky-500/20"
                >
                  Out ] {audioMaxDurationSec > 0 ? formatMmSs(audioStartSec + audioMaxDurationSec) : "End"}
                </button>
                <button
                  type="button"
                  disabled={isTrimmingSong || isFetchingSong}
                  onClick={() => handleTrimCachedSong()}
                  title="Cut cached song cleanly to [In .. Out] and re-sync captions"
                  className="inline-flex items-center gap-1 rounded bg-purple-500 px-2 py-0.5 text-[10px] font-extrabold text-white hover:bg-purple-400 disabled:opacity-50"
                >
                  <Scissors className="h-2.5 w-2.5" />
                  {isTrimmingSong ? "Cutting..." : "Cut Song"}
                </button>
                {(appliedAudioStartSec > 0 ||
                  (rawSongDuration !== null &&
                    fetchedSongDuration !== null &&
                    fetchedSongDuration < rawSongDuration - 0.5)) && (
                    <button
                      type="button"
                      disabled={isTrimmingSong || isFetchingSong}
                      onClick={() => {
                        setAudioStartSec(0);
                        setAudioStartText("0:00");
                        setAudioMaxDurationSec(0);
                        handleTrimCachedSong(0, 0);
                      }}
                      title="Restore full song length from 0:00"
                      className="inline-flex items-center gap-0.5 rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] font-semibold text-zinc-300 hover:text-white disabled:opacity-50"
                    >
                      <RotateCcw className="h-2.5 w-2.5" />
                      Full
                    </button>
                  )}
              </div>
            )}
          </div>

          {/* Selected Caption Inline Quick-Editor Bar (when a block is clicked on the timeline) */}
          {selectedCaptionIdx !== null && editableTimeline[selectedCaptionIdx] && (
            <div className="flex flex-wrap items-center gap-1 rounded-xl border border-amber-400/40 bg-zinc-950 px-2.5 py-1">
              <span className="text-[10px] font-bold text-amber-300 mr-0.5">
                #{selectedCaptionIdx + 1}
              </span>
              <button
                type="button"
                title="Snap start to current playhead (S)"
                onClick={() => snapLineStartToCurrentAudio(selectedCaptionIdx)}
                className="inline-flex items-center gap-1 rounded bg-emerald-500/20 px-1.5 py-0.5 text-[10px] font-bold text-emerald-300 hover:bg-emerald-500/30"
              >
                <Clock className="h-3 w-3" />
                Snap
              </button>
              <button
                type="button"
                title="Shift start -0.1s earlier"
                onClick={() => nudgeCaptionTime(selectedCaptionIdx, "start", -0.1)}
                className="rounded border border-zinc-800 bg-zinc-900 px-1 py-0.5 font-mono text-[10px] text-zinc-300 hover:border-amber-400"
              >
                -0.1
              </button>
              <input
                type="number"
                step="0.1"
                value={editableTimeline[selectedCaptionIdx].start}
                onChange={(e) =>
                  handleTimelineChange(selectedCaptionIdx, "start", e.target.value)
                }
                className="w-14 rounded border border-zinc-800 bg-zinc-900 px-1.5 py-0.5 font-mono text-[11px] text-amber-400"
              />
              <button
                type="button"
                title="Shift start +0.1s later"
                onClick={() => nudgeCaptionTime(selectedCaptionIdx, "start", 0.1)}
                className="rounded border border-zinc-800 bg-zinc-900 px-1 py-0.5 font-mono text-[10px] text-zinc-300 hover:border-amber-400"
              >
                +0.1
              </button>
              <span className="text-zinc-500">→</span>
              <button
                type="button"
                title="Shift end -0.1s earlier"
                onClick={() => nudgeCaptionTime(selectedCaptionIdx, "end", -0.1)}
                className="rounded border border-zinc-800 bg-zinc-900 px-1 py-0.5 font-mono text-[10px] text-zinc-300 hover:border-amber-400"
              >
                -0.1
              </button>
              <input
                type="number"
                step="0.1"
                value={editableTimeline[selectedCaptionIdx].end}
                onChange={(e) =>
                  handleTimelineChange(selectedCaptionIdx, "end", e.target.value)
                }
                className="w-14 rounded border border-zinc-800 bg-zinc-900 px-1.5 py-0.5 font-mono text-[11px] text-amber-400"
              />
              <button
                type="button"
                title="Shift end +0.1s later"
                onClick={() => nudgeCaptionTime(selectedCaptionIdx, "end", 0.1)}
                className="rounded border border-zinc-800 bg-zinc-900 px-1 py-0.5 font-mono text-[10px] text-zinc-300 hover:border-amber-400"
              >
                +0.1
              </button>
              <button
                type="button"
                title="Snap end to current playhead"
                onClick={() => snapLineEndToCurrentAudio(selectedCaptionIdx)}
                className="rounded bg-sky-500/20 px-1.5 py-0.5 text-[10px] font-bold text-sky-300 hover:bg-sky-500/30"
              >
                End@Here
              </button>
              <input
                type="text"
                value={editableTimeline[selectedCaptionIdx].text}
                onChange={(e) =>
                  handleTimelineChange(selectedCaptionIdx, "text", e.target.value)
                }
                style={{ fontFamily: editableUiFontFamily }}
                className="w-40 sm:w-56 rounded border border-zinc-800 bg-zinc-900 px-2 py-0.5 text-xs text-white"
              />
              <button
                type="button"
                onClick={() => handleDeleteCaptionLine(selectedCaptionIdx)}
                className="text-zinc-400 hover:text-red-400 p-1"
                title="Delete caption line"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          )}

          {/* Timeline Zoom Slider */}
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-zinc-400">Zoom:</span>
            <input
              type="range"
              min="1"
              max="4"
              step="0.25"
              value={timelineZoom}
              onChange={(e) => setTimelineZoom(parseFloat(e.target.value))}
              className="w-20 accent-amber-400"
            />
          </div>
        </div>

        {/* Timeline Active Effects Summary Bar (shows Blur & all active video/caption FX at a glance) */}
        <div className="flex flex-wrap items-center gap-1.5 border-b border-zinc-800/80 bg-zinc-950/75 px-4 py-1 text-[10px]">
          <span className="inline-flex items-center gap-1 font-bold uppercase tracking-wide text-zinc-400">
            <Sparkles className="h-3 w-3 text-amber-400" />
            Effects:
          </span>
          {blurInterval > 0 ? (
            <span className="inline-flex items-center gap-1 rounded-md border border-fuchsia-500/50 bg-fuchsia-500/15 px-1.5 py-0.5 font-semibold text-fuchsia-200">
              <button
                type="button"
                onClick={() => setActiveSidebarTab("effects")}
                title="Open Blur-Pulse settings in Canvas & Grade tab"
                className="hover:underline"
              >
                💧 Blur Every {blurInterval}s ({blurPulseWindows.length} pulses)
              </button>
              <button
                type="button"
                onClick={() => {
                  setBlurInterval(0);
                  showToast("Blur-Pulse Transition turned Off");
                }}
                title="Turn Off Blur-Pulse Transition"
                className="ml-0.5 rounded bg-fuchsia-500/30 px-1 text-[9px] font-bold text-white hover:bg-fuchsia-500"
              >
                Off
              </button>
            </span>
          ) : (
            <button
              type="button"
              onClick={() => setActiveSidebarTab("effects")}
              title="No periodic blur is active (click to configure in Canvas & Grade)"
              className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-1.5 py-0.5 font-medium text-emerald-300 hover:bg-emerald-500/20"
            >
              ✓ No Blur
            </button>
          )}
          {activeTimelineEffects
            .filter((fx) => fx.id !== "blur")
            .map((fx) => (
              <button
                key={fx.id}
                type="button"
                onClick={() => setActiveSidebarTab(fx.tab)}
                title={`Click to edit in ${fx.tab} tab`}
                className={`rounded-md border px-1.5 py-0.5 font-medium transition ${fx.badgeClass}`}
              >
                {fx.label}
              </button>
            ))}
        </div>

        {/* Timeline Body: Visual Multi-Track View OR Editable List Table View */}
        {timelineViewMode === "visual" ? (
          <div className="relative flex-1 overflow-x-auto overflow-y-auto select-none">
            <div
              className="relative min-h-full min-w-full"
              style={{ width: `${Math.max(100, Math.round(timelineZoom * 100))}%` }}
            >
              {/* Playhead Vertical Line */}
              <div
                className="pointer-events-none absolute inset-y-0 z-30 w-0.5 bg-amber-400 shadow-[0_0_8px_#fbbf24]"
                style={{
                  left: `${Math.min(100, (currentAudioTime / totalTimelineDuration) * 100)}%`,
                }}
              >
                <div className=" -ml-1.5 h-3 w-3 rotate-45 bg-amber-400" />
              </div>

              {/* Time Ruler (Click or drag anywhere to scrub playhead) */}
              <div
                onPointerDown={(e) => {
                  e.preventDefault();
                  const rect = e.currentTarget.getBoundingClientRect();
                  const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
                  seekAudioTo(ratio * totalTimelineDuration);
                  setIsScrubbingRuler(true);
                }}
                className="relative h-6 cursor-ew-resize border-b border-zinc-800 bg-zinc-950/90"
              >
                {Array.from({ length: Math.ceil(totalTimelineDuration / 5) + 1 }).map((_, idx) => {
                  const sec = idx * 5;
                  if (sec > totalTimelineDuration) return null;
                  const leftPct = (sec / totalTimelineDuration) * 100;
                  return (
                    <div
                      key={sec}
                      className="absolute top-0 bottom-0 flex items-center border-l border-zinc-800 pl-1 font-mono text-[10px] text-zinc-500"
                      style={{ left: `${leftPct}%` }}
                    >
                      {sec}s
                    </div>
                  );
                })}
              </div>

              {/* TRACK 1: SINHALA CAPTIONS TRACK (Direct Drag-Move & Left/Right Edge Resize) */}
              <div
                ref={captionsTrackRef}
                onClick={(e) => {
                  if (dragCaptionState?.moved) return;
                  const rect = e.currentTarget.getBoundingClientRect();
                  const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
                  seekAudioTo(ratio * totalTimelineDuration);
                }}
                className="relative flex h-14 items-center border-b border-zinc-800/80 bg-zinc-950/40 px-2"
              >
                <div className="pointer-events-none sticky left-2 z-20 rounded bg-zinc-900/90 px-2 py-0.5 text-[10px] font-bold text-amber-300 border border-zinc-800">
                  💬 Sinhala Captions ({editableTimeline.length}) • Drag blocks or edges
                </div>

                {editableTimeline.length === 0 ? (
                  <div className="ml-64 text-xs text-zinc-500">
                    Click &ldquo;⚡ Fetch &amp; Auto-Sync Song&rdquo; to populate synced Sinhala captions on this track...
                  </div>
                ) : (
                  editableTimeline.map((line, idx) => {
                    const leftPct = Math.max(
                      0,
                      ((line.start + lyricsOffsetSec) / totalTimelineDuration) * 100
                    );
                    const widthPct = Math.max(
                      1.2,
                      ((line.end - line.start) / totalTimelineDuration) * 100
                    );
                    const isSelected = selectedCaptionIdx === idx;
                    const isDraggingThis = dragCaptionState?.idx === idx;
                    const isPlayingNow =
                      currentAudioTime >= line.start + lyricsOffsetSec &&
                      currentAudioTime <= line.end + lyricsOffsetSec;

                    return (
                      <div
                        key={idx}
                        onPointerDown={(e) => {
                          e.stopPropagation();
                          setSelectedCaptionIdx(idx);
                          setDragCaptionState({
                            mode: "move",
                            idx,
                            startX: e.clientX,
                            origStart: line.start,
                            origEnd: line.end,
                            moved: false,
                          });
                        }}
                        onClick={(e) => {
                          e.stopPropagation();
                          if (dragCaptionState?.moved) return;
                          setSelectedCaptionIdx(idx);
                          seekAudioTo(line.start + lyricsOffsetSec);
                        }}
                        title={`${line.start}s -> ${line.end}s (Drag to move, drag edges to resize): ${line.text}`}
                        className={`group absolute top-2 bottom-2 flex items-center overflow-hidden rounded-lg border px-2.5 py-1 text-[11px] select-none transition-colors ${isDraggingThis
                          ? "z-30 cursor-grabbing border-white bg-amber-300 text-black font-bold shadow-xl ring-2 ring-amber-400/60"
                          : isSelected
                            ? "z-20 cursor-grab border-white bg-amber-400 text-black font-bold shadow-lg"
                            : isPlayingNow
                              ? "z-10 cursor-grab border-amber-400 bg-amber-400/25 text-white font-semibold"
                              : "cursor-grab border-emerald-500/40 bg-emerald-500/15 text-emerald-200 hover:border-amber-400"
                          }`}
                        style={{
                          left: `${leftPct}%`,
                          width: `${widthPct}%`,
                          fontFamily: editableUiFontFamily,
                        }}
                      >
                        {/* Left Resize Handle (Adjust Start Time on Timeline) */}
                        <div
                          onPointerDown={(e) => {
                            e.stopPropagation();
                            setSelectedCaptionIdx(idx);
                            setDragCaptionState({
                              mode: "resize-start",
                              idx,
                              startX: e.clientX,
                              origStart: line.start,
                              origEnd: line.end,
                              moved: false,
                            });
                          }}
                          onClick={(e) => e.stopPropagation()}
                          title="Drag left edge to adjust start time"
                          className="absolute left-0 top-0 bottom-0 z-10 flex w-2 cursor-ew-resize items-center justify-center bg-black/20 opacity-70 hover:bg-white/40 hover:opacity-100"
                        >
                          <div className="h-4 w-0.5 rounded-full bg-current" />
                        </div>

                        <div className="w-full truncate px-0.5 text-center">
                          {isDraggingThis
                            ? `${line.start.toFixed(1)}s–${line.end.toFixed(1)}s • ${line.text}`
                            : line.text}
                        </div>

                        {/* Right Resize Handle (Adjust End Time on Timeline) */}
                        <div
                          onPointerDown={(e) => {
                            e.stopPropagation();
                            setSelectedCaptionIdx(idx);
                            setDragCaptionState({
                              mode: "resize-end",
                              idx,
                              startX: e.clientX,
                              origStart: line.start,
                              origEnd: line.end,
                              moved: false,
                            });
                          }}
                          onClick={(e) => e.stopPropagation()}
                          title="Drag right edge to adjust end time"
                          className="absolute right-0 top-0 bottom-0 z-10 flex w-2 cursor-ew-resize items-center justify-center bg-black/20 opacity-70 hover:bg-white/40 hover:opacity-100"
                        >
                          <div className="h-4 w-0.5 rounded-full bg-current" />
                        </div>
                      </div>
                    );
                  })
                )}
              </div>

              {/* TRACK 2: AI VIDEO SMOOTH REPLAY LOOP TRACK (With Blade Split & Delete Part Support) */}
              <div
                onClick={(e) => {
                  const rect = e.currentTarget.getBoundingClientRect();
                  const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
                  const clickedSec = ratio * totalTimelineDuration;
                  if (isVideoBladeMode && visualClips.length > 0) {
                    seekAudioTo(clickedSec);
                    handleBladeVideoAtTime(clickedSec);
                    return;
                  }
                  seekAudioTo(clickedSec);
                }}
                className={`relative flex h-12 items-center border-b border-zinc-800/80 bg-zinc-950/60 ${isVideoBladeMode ? "cursor-crosshair ring-1 ring-inset ring-amber-400/40" : "cursor-pointer"
                  }`}
              >
                <div className="pointer-events-none sticky left-2 z-20 rounded bg-zinc-900/90 px-2 py-0.5 text-[10px] font-bold text-sky-300 border border-zinc-800">
                  🎬 Video Track (
                  {visualClips.length > 1 && selectedSingleClipIdx === null
                    ? `✨ Mixing & Blending ${visualClips.length} Clips`
                    : selectedSingleClipIdx !== null
                      ? `🔁 Repeating Clip #${selectedSingleClipIdx + 1}`
                      : `${effectiveVideoSegments.length} part${effectiveVideoSegments.length > 1 ? "s" : ""
                      }`}
                  {" • "}
                  {loopMode})
                  {isVideoBladeMode
                    ? " • 🔪 Click Video Bar to Cut"
                    : effectiveVideoSegments.length > 1
                      ? " • Click Part to Select & 🗑️ Delete"
                      : ""}
                </div>

                {visualClips.length === 0 ? (
                  <div className="ml-56 text-xs text-zinc-500">
                    Upload an AI video clip — use ✂️ Blade Video or 🗑️ Delete on this track.
                  </div>
                ) : (
                  Array.from({ length: loopCountOnTimeline }).flatMap((_, loopIdx) => {
                    const loopBaseSec = loopIdx * effectiveClipLen;
                    const speed = Math.max(0.5, clipSpeed || 1.0);
                    let segOffsetSec = 0;
                    return effectiveVideoSegments.map((seg, segIdx) => {
                      const segDur = Math.max(0.2, (seg.end - seg.start) / speed);
                      const startSec = loopBaseSec + segOffsetSec;
                      segOffsetSec += segDur;
                      if (startSec >= totalTimelineDuration) return null;
                      const endSec = Math.min(totalTimelineDuration, startSec + segDur);
                      const leftPct = (startSec / totalTimelineDuration) * 100;
                      const widthPct = Math.max(1.2, ((endSec - startSec) / totalTimelineDuration) * 100);
                      const isSelectedSeg = (selectedVideoSegmentIdx ?? 0) === segIdx;
                      return (
                        <div
                          key={`loop-${loopIdx}-seg-${segIdx}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            const segRect = e.currentTarget.getBoundingClientRect();
                            const localRatio = Math.max(
                              0,
                              Math.min(1, (e.clientX - segRect.left) / Math.max(1, segRect.width))
                            );
                            const clickedTimelineSec = Number(
                              (startSec + localRatio * (endSec - startSec)).toFixed(2)
                            );
                            const clickedSourceSec = Number(
                              (seg.start + localRatio * (seg.end - seg.start)).toFixed(2)
                            );
                            if (isVideoBladeMode) {
                              seekAudioTo(clickedTimelineSec);
                              handleBladeVideoSegmentAtSourceTime(segIdx, clickedSourceSec);
                              return;
                            }
                            setSelectedVideoSegmentIdx(segIdx);
                            seekAudioTo(clickedTimelineSec);
                            if (previewVideoRef.current) {
                              previewVideoRef.current.currentTime = clickedSourceSec;
                            }
                          }}
                          title={`Video Part #${segIdx + 1} (${seg.start.toFixed(1)}s–${seg.end.toFixed(1)}s) • Click to select, ✂️ to split, or 🗑️ to delete`}
                          className={`group absolute top-1.5 bottom-1.5 flex items-center justify-between overflow-hidden rounded-lg border px-2 text-[10px] transition ${isVideoBladeMode
                            ? "cursor-crosshair border-amber-400/80 bg-amber-500/20 text-amber-100 hover:bg-amber-500/30"
                            : isSelectedSeg
                              ? "z-10 border-amber-300 bg-sky-500/35 text-white font-bold shadow-md ring-1 ring-amber-300/60"
                              : "border-sky-500/40 bg-sky-500/15 text-sky-200 hover:border-sky-300"
                            }`}
                          style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
                        >
                          <span className="truncate">
                            {visualClips.length > 1 && selectedSingleClipIdx === null
                              ? `✨ Mix Clip #${((loopIdx + segIdx) % visualClips.length) + 1} (${visualClips[(loopIdx + segIdx) % visualClips.length]?.name || ""
                              })`
                              : selectedSingleClipIdx !== null
                                ? `🔁 Clip #${selectedSingleClipIdx + 1} Replay #${loopIdx + 1}`
                                : loopIdx === 0
                                  ? `🎬 Part #${segIdx + 1}: ${seg.start.toFixed(1)}s–${seg.end.toFixed(1)}s`
                                  : `✨ Replay #${loopIdx + 1} (Part #${segIdx + 1})`}
                          </span>
                          <div className="flex items-center gap-1 shrink-0 z-20">
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                setSelectedVideoSegmentIdx(segIdx);
                                handleBladeVideoSegmentAtSourceTime(
                                  segIdx,
                                  (seg.start + seg.end) / 2
                                );
                              }}
                              title="Split (Cut) this video part in half"
                              className="rounded bg-black/60 px-1 py-0.5 text-[9px] font-bold text-sky-200 hover:bg-sky-400 hover:text-black"
                            >
                              ✂️ Cut
                            </button>
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                handleDeleteVideoSegment(segIdx);
                              }}
                              title={
                                effectiveVideoSegments.length > 1
                                  ? `Delete Part #${segIdx + 1}`
                                  : "Delete main video clip"
                              }
                              className="rounded bg-red-600/80 px-1 py-0.5 text-[9px] font-bold text-white hover:bg-red-500"
                            >
                              🗑️ Del
                            </button>
                          </div>
                        </div>
                      );
                    });
                  })
                )}
              </div>

              {/* TRACK 3: BLUR, FADES & VISUAL EFFECTS TRACK */}
              <div
                onClick={(e) => {
                  const rect = e.currentTarget.getBoundingClientRect();
                  const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
                  seekAudioTo(ratio * totalTimelineDuration);
                }}
                className="relative flex h-10 cursor-pointer items-center border-b border-zinc-800/80 bg-zinc-950/70"
              >
                <div className="pointer-events-none sticky left-2 z-20 inline-flex items-center gap-1 rounded border border-zinc-800 bg-zinc-900/90 px-2 py-0.5 text-[10px] font-bold text-fuchsia-300">
                  <Sparkles className="h-3 w-3" />
                  Effects &amp; Blur ({blurInterval > 0 ? `Blur ${blurInterval}s` : "No Blur"})
                </div>

                {/* Continuous background FX layer */}
                <div
                  className={`pointer-events-none absolute inset-x-0 top-1.5 bottom-1.5 flex items-center justify-center rounded-md border text-[10px] ${blurInterval > 0 ||
                    colorGrade !== "original" ||
                    vignette ||
                    videoZoom > 1.01 ||
                    mirrorFlip ||
                    clipSpeed !== 1.0 ||
                    brightness !== 0 ||
                    contrast !== 1 ||
                    saturation !== 1
                    ? "border-amber-500/25 bg-amber-500/5 text-amber-200/75"
                    : "border-zinc-800/70 bg-zinc-900/30 text-zinc-500"
                    }`}
                >
                  <span className="truncate px-36">
                    {activeTimelineEffects
                      .filter((fx) => fx.id !== "blur" && fx.id !== "vfade" && fx.id !== "afade")
                      .map((fx) => fx.label)
                      .join(" • ") || "Clean Video (No Color/FX Applied)"}
                  </span>
                </div>

                {/* Video Fade-In Block (0s .. 0.6s) */}
                {hasVideoFades && (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      seekAudioTo(0);
                    }}
                    title="Video Fade-In (0.0s -> 0.6s) — Click to seek"
                    className="absolute top-1.5 bottom-1.5 z-10 flex items-center justify-center overflow-hidden rounded border border-sky-400/60 bg-gradient-to-r from-sky-500/40 to-sky-500/10 px-1 text-[9px] font-bold text-sky-200 hover:border-white"
                    style={{
                      left: "0%",
                      width: `${Math.max(1.2, (videoFadeInEnd / totalTimelineDuration) * 100)}%`,
                    }}
                  >
                    🌅 In
                  </button>
                )}

                {/* Blur-Pulse Windows (Exact FFmpeg lt(mod(t,interval),0.42)*gt(t,1.0) moments) */}
                {blurPulseWindows.map((win, wIdx) => {
                  const leftPct = (win.start / totalTimelineDuration) * 100;
                  const widthPct = Math.max(
                    1.0,
                    ((win.end - win.start) / totalTimelineDuration) * 100
                  );
                  const isActiveNow =
                    currentAudioTime >= win.start && currentAudioTime <= win.end;
                  return (
                    <button
                      key={wIdx}
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        seekAudioTo(win.start);
                      }}
                      title={`💧 Blur-Pulse Effect (${win.start.toFixed(2)}s – ${win.end.toFixed(2)}s) — Click to preview frame`}
                      className={`absolute top-1 bottom-1 z-10 flex items-center justify-center overflow-hidden rounded border px-1 text-[9px] font-bold transition ${isActiveNow
                        ? "border-white bg-fuchsia-500 text-white shadow-[0_0_10px_#d946ef]"
                        : "border-fuchsia-400/70 bg-fuchsia-500/35 text-fuchsia-100 hover:border-white hover:bg-fuchsia-500/55"
                        }`}
                      style={{
                        left: `${leftPct}%`,
                        width: `${widthPct}%`,
                      }}
                    >
                      💧
                    </button>
                  );
                })}

                {/* Loop Crossfade / Boomerang Seam Indicators */}
                {visualClips.length > 0 &&
                  Array.from({ length: Math.max(0, loopCountOnTimeline - 1) }).map((_, sIdx) => {
                    const seamSec = (sIdx + 1) * effectiveClipLen;
                    if (seamSec >= totalTimelineDuration) return null;
                    const leftPct = (seamSec / totalTimelineDuration) * 100;
                    return (
                      <button
                        key={`seam-${sIdx}`}
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          seekAudioTo(Math.max(0, seamSec - 0.3));
                        }}
                        title={`${loopMode === "pingpong" ? "Boomerang reverse" : "Crossfade"} transition at ${seamSec.toFixed(1)}s`}
                        className="absolute top-1 bottom-1 z-10 -ml-1.5 flex w-3 items-center justify-center rounded border border-cyan-400/60 bg-cyan-500/30 text-[8px] font-bold text-cyan-100 hover:border-white"
                        style={{ left: `${leftPct}%` }}
                      >
                        ⇄
                      </button>
                    );
                  })}

                {/* Video Fade-Out Block */}
                {hasVideoFades && (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      seekAudioTo(videoFadeOutStart);
                    }}
                    title={`Video Fade-Out (${videoFadeOutStart.toFixed(1)}s -> ${totalTimelineDuration.toFixed(1)}s) — Click to seek`}
                    className="absolute top-1.5 bottom-1.5 z-10 flex items-center justify-center overflow-hidden rounded border border-orange-400/60 bg-gradient-to-l from-orange-500/40 to-orange-500/10 px-1 text-[9px] font-bold text-orange-200 hover:border-white"
                    style={{
                      left: `${(videoFadeOutStart / totalTimelineDuration) * 100}%`,
                      width: `${Math.max(1.2, ((totalTimelineDuration - videoFadeOutStart) / totalTimelineDuration) * 100)}%`,
                    }}
                  >
                    🌇 Out
                  </button>
                )}
              </div>

              {/* TRACK 4: SONG AUDIO WAVEFORM TRACK (REAL DECODED AMPLITUDE) */}
              <div
                onClick={(e) => {
                  const rect = e.currentTarget.getBoundingClientRect();
                  const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
                  seekAudioTo(ratio * totalTimelineDuration);
                }}
                className="relative flex h-11 cursor-pointer items-center bg-zinc-950/80"
              >
                <div className="pointer-events-none sticky left-2 z-20 inline-flex items-center gap-1 rounded bg-zinc-900/90 px-2 py-0.5 text-[10px] font-bold text-purple-300 border border-zinc-800">
                  <Music className="h-3 w-3" />
                  Audio ({fetchedSongDuration ? `${fetchedSongDuration}s` : "30s"}
                  {appliedAudioStartSec > 0 ? ` @ ${formatMmSs(appliedAudioStartSec)}` : ""})
                </div>

                {/* Audio Fade-In / Fade-Out indicators */}
                {hasAudioFades && (
                  <>
                    <div
                      title="Audio Fade-In (0.6s)"
                      className="pointer-events-none absolute top-2 bottom-2 left-0 z-10 rounded-l-lg border-r border-purple-300/60 bg-gradient-to-r from-purple-400/35 to-transparent"
                      style={{
                        width: `${Math.max(1, (0.6 / totalTimelineDuration) * 100)}%`,
                      }}
                    />
                    <div
                      title="Audio Fade-Out (1.2s)"
                      className="pointer-events-none absolute top-2 bottom-2 right-0 z-10 rounded-r-lg border-l border-purple-300/60 bg-gradient-to-l from-purple-400/35 to-transparent"
                      style={{
                        width: `${Math.max(1, ((totalTimelineDuration - audioFadeOutStart) / totalTimelineDuration) * 100)}%`,
                      }}
                    />
                  </>
                )}

                {/* Visual Marked Song Cut Range Highlight [In .. Out] on Audio Waveform */}
                {cachedSongId &&
                  (() => {
                    const cutRelStart = Math.max(0, audioStartSec - appliedAudioStartSec);
                    const cutRelEnd =
                      audioMaxDurationSec > 0
                        ? Math.min(totalTimelineDuration, cutRelStart + audioMaxDurationSec)
                        : totalTimelineDuration;
                    const hasPendingCut =
                      cutRelStart > 0.05 || cutRelEnd < totalTimelineDuration - 0.1;
                    if (!hasPendingCut) return null;
                    const cLeftPct = Math.max(0, Math.min(100, (cutRelStart / totalTimelineDuration) * 100));
                    const cWidthPct = Math.max(
                      1,
                      Math.min(100 - cLeftPct, ((cutRelEnd - cutRelStart) / totalTimelineDuration) * 100)
                    );
                    return (
                      <div
                        className="pointer-events-none absolute top-1 bottom-1 z-10 rounded border-x-2 border-y border-emerald-400 bg-emerald-400/15"
                        style={{ left: `${cLeftPct}%`, width: `${cWidthPct}%` }}
                      />
                    );
                  })()}

                <div className="absolute inset-x-0 top-2 bottom-2 flex items-center gap-[2px] overflow-hidden rounded-lg border border-purple-500/30 bg-purple-500/10 px-2">
                  {(waveformBars.length > 0
                    ? waveformBars
                    : Array.from({ length: 140 }).map((_, idx) =>
                      22 + Math.round(Math.abs(Math.sin(idx * 0.45)) * 55)
                    )
                  ).map((h, barIdx, arr) => {
                    const barPct = barIdx / Math.max(arr.length - 1, 1);
                    const playPct = currentAudioTime / Math.max(totalTimelineDuration, 1);
                    const played = barPct <= playPct;
                    return (
                      <div
                        key={barIdx}
                        className={`flex-1 rounded-full transition-colors ${played ? "bg-amber-400/80" : "bg-purple-400/35"
                          }`}
                        style={{ height: `${h}%` }}
                      />
                    );
                  })}
                </div>
              </div>
            </div>
          </div>
        ) : (
          /* CAPTION TIMING TABLE VIEW INSIDE TIMELINE DOCK */
          <div className="flex-1 overflow-y-auto px-4 py-2 space-y-1.5 text-xs">
            {editableTimeline.length === 0 ? (
              <div className="py-8 text-center text-zinc-500">
                No captions synced yet. Click &ldquo;⚡ Fetch &amp; Auto-Sync Song&rdquo; above!
              </div>
            ) : (
              editableTimeline.map((item, idx) => {
                const isActiveNow =
                  isAudioPlaying &&
                  currentAudioTime >= item.start + lyricsOffsetSec &&
                  currentAudioTime <= item.end + lyricsOffsetSec;

                return (
                  <div
                    key={idx}
                    className={`flex items-center gap-2 rounded-xl border p-1.5 transition ${isActiveNow
                      ? "border-amber-400 bg-amber-400/15"
                      : "border-zinc-800 bg-zinc-950"
                      }`}
                  >
                    <span className="w-6 text-center font-mono text-[10px] text-zinc-500">
                      #{idx + 1}
                    </span>
                    <button
                      type="button"
                      onClick={() => seekAudioTo(item.start + lyricsOffsetSec)}
                      className="flex h-6 w-6 shrink-0 items-center justify-center rounded bg-zinc-800 text-[10px] text-amber-300 hover:bg-amber-400 hover:text-black"
                    >
                      ▶
                    </button>
                    <button
                      type="button"
                      onClick={() => snapLineStartToCurrentAudio(idx)}
                      className="flex h-6 px-2 shrink-0 items-center justify-center rounded bg-zinc-800 text-[10px] text-emerald-300 hover:bg-emerald-400 hover:text-black"
                    >
                      ⏱ Snap Start
                    </button>
                    <input
                      type="number"
                      step="0.1"
                      value={item.start}
                      onChange={(e) => handleTimelineChange(idx, "start", e.target.value)}
                      className="w-16 rounded border border-zinc-800 bg-zinc-900 px-1.5 py-1 font-mono text-[11px] text-amber-400"
                    />
                    <span className="text-zinc-500">→</span>
                    <input
                      type="number"
                      step="0.1"
                      value={item.end}
                      onChange={(e) => handleTimelineChange(idx, "end", e.target.value)}
                      className="w-16 rounded border border-zinc-800 bg-zinc-900 px-1.5 py-1 font-mono text-[11px] text-amber-400"
                    />
                    <input
                      type="text"
                      value={item.text}
                      onChange={(e) => handleTimelineChange(idx, "text", e.target.value)}
                      style={{ fontFamily: editableUiFontFamily }}
                      className="flex-1 min-w-0 rounded border border-zinc-800 bg-zinc-900 px-2.5 py-1 text-xs text-zinc-100"
                    />
                    <button
                      type="button"
                      onClick={() => handleDeleteCaptionLine(idx)}
                      className="px-2 text-zinc-500 hover:text-red-400"
                    >
                      ✕
                    </button>
                  </div>
                );
              })
            )}
          </div>
        )}
      </section>
    </div>
  );
}
