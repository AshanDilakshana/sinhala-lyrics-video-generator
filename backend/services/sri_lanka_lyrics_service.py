import asyncio
import html
import json
import logging
import re
import time
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import httpx

from backend.config import TEMP_DIR

logger = logging.getLogger(__name__)

SITEMAP_CACHE_DIR = (TEMP_DIR / "sitemaps_cache").resolve()
SITEMAP_CACHE_TTL_SEC = 86400  # 24 hours

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,si;q=0.8",
}

# Deterministic Sinhala Unicode -> Singlish romanizer for matching Romanized URL slugs
_SINHALA_CONSONANTS = {
    "ක": "ka", "ඛ": "kha", "ග": "ga", "ඝ": "gha", "ඞ": "nga", "ඟ": "nga",
    "ච": "cha", "ඡ": "cha", "ජ": "ja", "ඣ": "jha", "ඤ": "nya", "ඦ": "nja",
    "ට": "ta", "ඨ": "tha", "ඩ": "da", "ඪ": "dha", "ණ": "na", "ඬ": "nda",
    "ත": "tha", "ථ": "tha", "ද": "da", "ධ": "dha", "න": "na", "ඳ": "nda",
    "ප": "pa", "ඵ": "pha", "බ": "ba", "භ": "bha", "ම": "ma", "ඹ": "mba",
    "ය": "ya", "ර": "ra", "ල": "la", "ව": "wa",
    "ශ": "sha", "ෂ": "sha", "ස": "sa", "හ": "ha", "ළ": "la", "ෆ": "fa",
}
_SINHALA_VOWELS_INDEP = {
    "අ": "a", "ආ": "aa", "ඇ": "ae", "ඈ": "ae", "ඉ": "i", "ඊ": "ee",
    "උ": "u", "ඌ": "oo", "ඍ": "ru", "එ": "e", "ඒ": "e", "ඓ": "ai",
    "ඔ": "o", "ඕ": "o", "ඖ": "au",
}
_SINHALA_PILLAM = {
    "්": "", "ා": "a", "ැ": "ae", "ෑ": "ae", "ි": "i", "ී": "ee",
    "ු": "u", "ූ": "oo", "ෘ": "ru", "ෙ": "e", "ේ": "e", "ෛ": "ai",
    "ො": "o", "ෝ": "o", "ෞ": "au", "ං": "n", "ඃ": "h",
}


def sinhala_to_singlish(text: str) -> str:
    """Transliterate Sinhala Unicode title/phrase into searchable Singlish Latin text."""
    out: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in _SINHALA_VOWELS_INDEP:
            out.append(_SINHALA_VOWELS_INDEP[ch])
            i += 1
        elif ch in _SINHALA_CONSONANTS:
            base = _SINHALA_CONSONANTS[ch]
            if i + 1 < n and text[i + 1] in _SINHALA_PILLAM:
                # Replace trailing inherent 'a' with vowel sign (or empty for hal kirima '්')
                stem = base[:-1] if base.endswith("a") else base
                out.append(stem + _SINHALA_PILLAM[text[i + 1]])
                i += 2
                # Handle kombuwa + aela-pilla combos that remain if multi-codepoint
                while i < n and text[i] in _SINHALA_PILLAM:
                    out.append(_SINHALA_PILLAM[text[i]])
                    i += 1
            else:
                out.append(base)
                i += 1
        elif ch in ("\u200d", "\u200c"):
            i += 1
        elif ch.isalnum() or ch.isspace():
            out.append(ch.lower())
            i += 1
        else:
            out.append(" ")
            i += 1
    return re.sub(r"\s+", " ", "".join(out)).strip()


def _normalize_latin_tokens(text: str) -> List[str]:
    """Normalize a Romanized title or URL slug into lowercase alphanumeric tokens."""
    low = text.lower()
    # Common Singlish spelling variations (v/w, th/t, dh/d, oo/u, ee/i, aa/a)
    low = re.sub(r"[^a-z0-9\s-]", " ", low)
    low = low.replace("-", " ")
    tokens = [t for t in low.split() if t and t not in ("song", "lyrics", "sinhala", "official", "audio", "video", "karaoke", "new")]
    return tokens


def _canonical_singlish_word(word: str) -> str:
    """Fold common Singlish spelling variants so `mithuri`/`mituri`, `walakul`/`valakul`, `sanda`/`sada` match."""
    w = word.lower()
    w = w.replace("ph", "f").replace("th", "t").replace("dh", "d").replace("kh", "k").replace("gh", "g").replace("sh", "s").replace("ch", "c")
    w = w.replace("v", "w").replace("aa", "a").replace("ee", "i").replace("oo", "u")
    w = re.sub(r"(.)\1+", r"\1", w)
    return w


def parse_smule_url_to_query(url: str) -> str:
    """
    If a user pastes a Smule arrangement URL like
    `https://www.smule.com/song/kasun-kalhara-...-sanda-mithuri-karaoke-lyrics/.../arrangement`,
    extract clean searchable words (`kasun kalhara sanda mithuri`) from its URL slug.
    """
    try:
        parsed = urllib.parse.urlparse(url.strip())
        if "smule.com" not in parsed.netloc.lower():
            return ""
        decoded_path = urllib.parse.unquote(parsed.path)
        parts = [p for p in decoded_path.split("/") if p]
        if "song" in parts:
            idx = parts.index("song")
            if idx + 1 < len(parts):
                slug = parts[idx + 1]
                # Remove non-ASCII or use romanized tokens
                ascii_words = _normalize_latin_tokens(re.sub(r"[\u0D80-\u0DFF]+", " ", slug))
                si_parts = re.findall(r"[\u0D80-\u0DFF]+", slug.replace("-", " "))
                if ascii_words:
                    return " ".join(ascii_words)
                if si_parts:
                    return sinhala_to_singlish(" ".join(si_parts))
    except Exception:
        pass
    return ""


async def _load_multi_sitemap_urls(
    site_key: str,
    sitemap_urls: List[str],
    url_pattern: str,
) -> List[str]:
    """Fetch & cache one or more sitemaps locally for fast offline slug search."""
    SITEMAP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = SITEMAP_CACHE_DIR / f"{site_key}.json"

    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < SITEMAP_CACHE_TTL_SEC:
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                if isinstance(data, list) and len(data) > 50:
                    return data
            except Exception:
                pass

    all_urls: List[str] = []
    seen = set()
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=BROWSER_HEADERS, verify=False) as client:
            for sm_url in sitemap_urls:
                try:
                    resp = await client.get(sm_url)
                    if resp.status_code == 200:
                        for u in re.findall(url_pattern, resp.text):
                            if u not in seen:
                                seen.add(u)
                                all_urls.append(u)
                except Exception as exc:
                    logger.warning("Failed to fetch sitemap %s: %s", sm_url, exc)
        if all_urls:
            cache_file.write_text(json.dumps(all_urls), encoding="utf-8")
            logger.info("Cached %d song URLs for %s", len(all_urls), site_key)
            return all_urls
    except Exception as exc:
        logger.warning("Failed to load sitemaps for %s: %s", site_key, exc)

    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


async def _load_sitemap_urls(site_key: str, sitemap_url: str, url_pattern: str) -> List[str]:
    return await _load_multi_sitemap_urls(site_key, [sitemap_url], url_pattern)


def _score_url_against_query(
    source: str,
    url: str,
    q_canon: List[str],
    q_joined: str,
) -> Optional[Tuple[float, Dict[str, str]]]:
    """Score a `/song/...` or `/songs/...` URL slug against canonicalized Singlish tokens."""
    if "/songs/" in url:
        path_part = url.rstrip("/").split("/songs/")[-1]
    else:
        path_part = url.rstrip("/").split("/song/")[-1]

    segments = path_part.split("/")
    song_slug = segments[-1]
    artist_slug = segments[0] if len(segments) > 1 else ""

    song_tokens = _normalize_latin_tokens(song_slug)
    artist_tokens = _normalize_latin_tokens(artist_slug)
    all_tokens = song_tokens + artist_tokens
    if not all_tokens:
        return None

    song_canon = [_canonical_singlish_word(t) for t in song_tokens]
    all_canon = [_canonical_singlish_word(t) for t in all_tokens]
    song_joined = " ".join(song_canon)

    hits = 0.0
    for qt in q_canon:
        if qt in song_canon:
            hits += 1.25
        elif qt in all_canon:
            hits += 0.9
        elif any(qt in ac or ac in qt for ac in all_canon if len(qt) >= 3 and len(ac) >= 3):
            hits += 0.55

    if hits < max(1.0, len(q_canon) * 0.5):
        return None

    coverage = hits / max(len(q_canon), 1)
    exact_phrase_bonus = (
        2.0
        if song_joined == q_joined
        else (1.0 if q_joined in song_joined or song_joined in q_joined else 0.0)
    )
    slug_precision = sum(1 for st in song_canon if st in q_canon) / max(len(song_canon), 1)
    score = coverage * 3.0 + slug_precision * 1.8 + exact_phrase_bonus

    if score < 2.2:
        return None

    return (
        score,
        {
            "source": source,
            "url": url,
            "title": song_slug.replace("-", " ").title(),
            "artist": artist_slug.replace("-", " ").title() if artist_slug else "",
            "score": f"{score:.2f}",
        },
    )


async def _search_geemansala_wp_api(
    query: str,
    limit: int = 4,
) -> List[Dict[str, str]]:
    """
    Search GeeMansala via WordPress REST API (`/wp-json/wp/v2/station`).
    GeeMansala often uses the first lyric line as slug (`/songs/igilenna-oba-ekka/`),
    so full-text WP search finds songs even when queried by chorus title ("Sanda Mithuri")!
    """
    clean_q = (query or "").strip()
    if not clean_q:
        return []
    out: List[Dict[str, str]] = []
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=BROWSER_HEADERS, verify=False) as client:
            resp = await client.get(
                "https://geemansala.com/wp-json/wp/v2/station",
                params={"search": clean_q, "per_page": limit},
            )
            if resp.status_code != 200:
                return []
            items = resp.json()
            if not isinstance(items, list):
                return []
            for idx, item in enumerate(items):
                link = item.get("link") or ""
                if "/songs/" not in link:
                    continue
                raw_title = html.unescape(
                    re.sub(r"<[^>]+>", "", (item.get("title") or {}).get("rendered", ""))
                ).strip()
                artist = html.unescape(str(item.get("artist_name") or "")).strip()
                content_html = (item.get("content") or {}).get("rendered", "")
                lyrics_raw = _extract_geemansala_block_to_lyrics(content_html)
                si_count = sum(1 for c in lyrics_raw if 0x0D80 <= ord(c) <= 0x0DFF)
                if si_count < 20:
                    continue
                clean_lyrics = _strip_blog_punctuation_for_captions(lyrics_raw)
                lines_list = [ln for ln in clean_lyrics.splitlines() if ln.strip()]
                raw_lines = [ln for ln in lyrics_raw.splitlines() if ln.strip()]
                shorthand = sum(1 for ln in raw_lines if "..." in ln or "//" in ln)
                completeness = len(raw_lines) - shorthand * 3
                entry = {
                    "source": "geemansala.com",
                    "url": link,
                    "title": raw_title,
                    "artist": artist,
                    "lyrics_text": clean_lyrics,
                    "line_count": str(len(lines_list)),
                    "completeness": str(completeness),
                    "score": f"{6.0 - idx * 0.4:.2f}",
                }
                # Warm local page cache so subsequent fetch_lyrics_from_url is instant
                try:
                    import hashlib
                    pages_cache = SITEMAP_CACHE_DIR / "pages"
                    pages_cache.mkdir(parents=True, exist_ok=True)
                    cp = pages_cache / f"{hashlib.md5(link.encode('utf-8')).hexdigest()[:14]}.json"
                    cp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
                except Exception:
                    pass
                out.append(entry)
    except Exception as exc:
        logger.debug("GeeMansala WP API search failed for %r: %s", query, exc)
    return out


async def search_sri_lankan_song_urls(
    query: str,
    artist_name: str = "",
    limit: int = 6,
    sites: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    """
    Search Sinhala song databases across `lyrihub.lk`, `lyrics-lk.com`, and `geemansala.com`
    using Sinhala Unicode or Singlish/English title + artist matching.
    Supports optional `sites` filter for strict site-by-site sequential fetching.
    """
    raw_q = (query or "").strip()
    if not raw_q and not artist_name.strip():
        return []

    active_sites = set(sites) if sites else {"lyrihub.lk", "lyrics-lk.com", "geemansala.com"}

    # Direct URL support
    if raw_q.startswith(("http://", "https://")):
        low_url = raw_q.lower()
        if "lyrihub.lk/song/" in low_url:
            return [{"source": "lyrihub.lk", "url": raw_q, "slug": raw_q.rstrip("/").split("/")[-1], "score": "100"}]
        if "lyrics-lk.com/song/" in low_url:
            return [{"source": "lyrics-lk.com", "url": raw_q, "slug": raw_q.rstrip("/").split("/")[-1], "score": "100"}]
        if "geemansala.com/songs/" in low_url:
            return [{"source": "geemansala.com", "url": raw_q, "slug": raw_q.rstrip("/").split("/")[-1], "score": "100"}]
        if "smule.com/song/" in low_url:
            raw_q = parse_smule_url_to_query(raw_q) or raw_q

    combined_query = f"{raw_q} {artist_name}".strip()
    latin_query = (
        sinhala_to_singlish(combined_query)
        if any(0x0D80 <= ord(c) <= 0x0DFF for c in combined_query)
        else combined_query
    )
    q_tokens = _normalize_latin_tokens(latin_query)
    if not q_tokens:
        return []

    q_canon = [_canonical_singlish_word(t) for t in q_tokens]
    q_joined = " ".join(q_canon)

    scored: List[Tuple[float, Dict[str, str]]] = []
    seen_urls = set()

    if "lyrihub.lk" in active_sites:
        lh_urls = await _load_sitemap_urls(
            "lyrihub_lk",
            "https://www.lyrihub.lk/sitemap-0.xml",
            r"<loc>(https://www\.lyrihub\.lk/song/[^<]+)</loc>",
        )
        for u in lh_urls:
            res = _score_url_against_query("lyrihub.lk", u, q_canon, q_joined)
            if res and res[1]["url"] not in seen_urls:
                seen_urls.add(res[1]["url"])
                scored.append(res)

    if "lyrics-lk.com" in active_sites:
        ll_urls = await _load_sitemap_urls(
            "lyrics_lk_com",
            "https://lyrics-lk.com/sitemap.xml",
            r"<loc>(https://lyrics-lk\.com/song/[^<]+)</loc>",
        )
        for u in ll_urls:
            res = _score_url_against_query("lyrics-lk.com", u, q_canon, q_joined)
            if res and res[1]["url"] not in seen_urls:
                seen_urls.add(res[1]["url"])
                scored.append(res)

    if "geemansala.com" in active_sites:
        # 1. WP REST full-text search (handles chorus titles like "Sanda Mithuri" even when URL slug is first line)
        wp_hits = await _search_geemansala_wp_api(raw_q, limit=min(limit, 4))
        for item in wp_hits:
            if item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                scored.append((float(item.get("score", "5.5")), item))
        # 2.GeeMansala 3 station sitemaps for Singlish slug fuzzy matching
        gm_urls = await _load_multi_sitemap_urls(
            "geemansala_com",
            [
                "https://geemansala.com/station-sitemap.xml",
                "https://geemansala.com/station-sitemap2.xml",
                "https://geemansala.com/station-sitemap3.xml",
            ],
            r"<loc>(https://geemansala\.com/songs/[^<]+)</loc>",
        )
        for u in gm_urls:
            res = _score_url_against_query("geemansala.com", u, q_canon, q_joined)
            if res and res[1]["url"] not in seen_urls:
                seen_urls.add(res[1]["url"])
                scored.append(res)

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in scored[:limit]]


def _clean_extracted_sinhala_lyrics(raw_text: str) -> str:
    """Normalize HTML entities, breaks, and aesthetic punctuation while keeping Sinhala lines clean."""
    txt = html.unescape(raw_text)
    txt = txt.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in txt.split("\n"):
        # Clean stray multi-dots/slashes from lyric blogs for crisp TikTok captions
        cleaned = re.sub(r"[ \t]+", " ", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _strip_blog_punctuation_for_captions(lyrics_text: str) -> str:
    """Strip blog filler markers (`...`, `//`) for aesthetic TikTok display."""
    out = []
    for ln in lyrics_text.splitlines():
        s = re.sub(r"\.{2,}|/+", " ", ln)
        s = re.sub(r"\s+", " ", s).strip()
        if s:
            out.append(s)
    return "\n".join(out)


def extract_lyrihub_lyrics(page_html: str) -> Tuple[str, str, str]:
    """Extract (title, artist, sinhala_lyrics) from a lyrihub.lk HTML page."""
    title = ""
    h1_m = re.search(r"<h1[^>]*>(.*?)</h1>", page_html, re.S | re.I)
    if h1_m:
        title = html.unescape(re.sub(r"<[^>]+>", "", h1_m.group(1))).strip()

    div_m = re.search(
        r'<div[^>]*class=["\'][^"\']*lyri-content[^"\']*["\'][^>]*>(.*?)</div>',
        page_html,
        re.S | re.I,
    )
    if not div_m:
        return title, "", ""

    block = div_m.group(1)
    block = re.sub(r"<br\s*/?>", "\n", block, flags=re.I)
    block = re.sub(r"</p>\s*<p[^>]*>", "\n", block, flags=re.I)
    block = re.sub(r"<[^>]+>", "", block)
    return title, "", _clean_extracted_sinhala_lyrics(block)


def extract_lyrics_lk_lyrics(page_html: str) -> Tuple[str, str, str]:
    """Extract (title, artist, sinhala_lyrics) from a lyrics-lk.com Next.js HTML page."""
    title = ""
    artist = ""
    lyrics = ""

    t_m = re.search(r"<title>(.*?)</title>", page_html, re.S | re.I)
    if t_m:
        title = html.unescape(t_m.group(1)).split("Lyrics")[0].strip()

    a_m = re.search(r'<meta[^>]*name=["\']music:musician["\'][^>]*content=["\']([^"\']+)["\']', page_html, re.I)
    if a_m:
        artist = html.unescape(a_m.group(1)).strip()

    # Pattern 1: Next.js RSC payload `"lyrics_sinhala":"..."`
    m_rsc = re.search(r'\\?"lyrics_sinhala\\?"\s*:\s*\\?"(.*?)(?:\\"|")', page_html, re.S)
    if m_rsc:
        raw_escaped = m_rsc.group(1)
        try:
            unescaped = (
                raw_escaped.replace("\\\\r\\\\n", "\n")
                .replace("\\\\n", "\n")
                .replace("\\r\\n", "\n")
                .replace("\\n", "\n")
                .replace("\\r", "\n")
            )
            for cutoff_marker in ('","translation"', '\\",\\"translation\\"', '","meaning"', '\\",\\"meaning\\"'):
                c_idx = unescaped.find(cutoff_marker)
                if c_idx > 0:
                    unescaped = unescaped[:c_idx]
            lyrics = _clean_extracted_sinhala_lyrics(unescaped)
        except Exception:
            pass

    # Fallback Pattern 2: `data-lyrics-si":true,"children":"..."`
    if not lyrics:
        m_child = re.search(
            r'data-lyrics-si\\?"\s*:\s*true\s*,\s*\\?"children\\?"\s*:\s*\\?"((?:[^"\\]|\\.)*)\\?"',
            page_html,
        )
        if m_child:
            raw_escaped = m_child.group(1)
            lyrics = _clean_extracted_sinhala_lyrics(
                raw_escaped.replace("\\r\\n", "\n").replace("\\n", "\n")
            )

    return title, artist, lyrics


def _extract_geemansala_block_to_lyrics(block_html: str) -> str:
    """Convert GeeMansala WordPress paragraph/<br> block into clean Sinhala lyric lines."""
    if not block_html:
        return ""
    # Stop before WP sharing / related-posts widgets if parsing full entry-content
    for cutoff in ('class="sharedaddy"', 'id="jp-relatedposts"', 'class="entry-footer"'):
        idx = block_html.find(cutoff)
        if idx > 0:
            block_html = block_html[:idx]

    txt = re.sub(r"<br\s*/?>", "\n", block_html, flags=re.I)
    txt = re.sub(r"</p>\s*<p[^>]*>", "\n", txt, flags=re.I)
    txt = re.sub(r"</?(?:p|strong|em|b|i|span|div|a)[^>]*>", "\n", txt, flags=re.I)
    txt = re.sub(r"<[^>]+>", "", txt)
    txt = html.unescape(txt)

    has_sinhala_in_block = bool(re.search(r"[\u0D80-\u0DFF]", txt))
    clean_lines: List[str] = []
    for raw_ln in txt.splitlines():
        ln = re.sub(r"[ \t]+", " ", raw_ln).strip()
        if not ln:
            continue
        low_ln = ln.lower()
        if low_ln in ("lyrics", "lyrics:", "share", "share this:", "like this:"):
            continue
        if low_ln.startswith(("lyrics of ", "song :", "song:", "artist :", "artist:", "music :", "music:", "lyrics :", "vocal :")):
            continue
        if ln in ("ගී පද", "ගීතයේ පද"):
            continue
        if has_sinhala_in_block:
            # For Sinhala song pages, only keep lines that contain Sinhala Unicode characters
            if re.search(r"[\u0D80-\u0DFF]", ln):
                clean_lines.append(ln)
        elif len(ln) > 3 and not ln.startswith("http"):
            clean_lines.append(ln)
    return "\n".join(clean_lines)


def extract_geemansala_lyrics(page_html: str) -> Tuple[str, str, str]:
    """Extract (title, artist, sinhala_lyrics) from a geemansala.com/songs/... HTML page."""
    title = ""
    artist = ""

    h1_m = re.search(r"<h1[^>]*>(.*?)</h1>", page_html, re.S | re.I)
    if h1_m:
        title = html.unescape(re.sub(r"<[^>]+>", "", h1_m.group(1))).strip()

    art_m = re.search(
        r'class=["\'][^"\']*entry-artist[^"\']*["\'][^>]*>\s*(?:<a[^>]*>)?(.*?)(?:</a>)?\s*</(?:span|div|a)>',
        page_html,
        re.S | re.I,
    )
    if art_m:
        artist = html.unescape(re.sub(r"<[^>]+>", "", art_m.group(1))).strip()

    # Extract `<div class="entry-content">` block
    ec_m = re.search(
        r'<div[^>]*class=["\'][^"\']*entry-content[^"\']*["\'][^>]*>(.*?)(?:<footer|</article>)',
        page_html,
        re.S | re.I,
    )
    if not ec_m:
        return title, artist, ""

    lyrics = _extract_geemansala_block_to_lyrics(ec_m.group(1))
    return title, artist, lyrics


async def fetch_lyrics_from_url(url: str, source_hint: str = "") -> Optional[Dict[str, str]]:
    """Fetch a single song page from `lyrihub.lk`, `lyrics-lk.com`, `geemansala.com`, or `smule.com` and extract Sinhala lyrics."""
    import hashlib

    low = url.lower()
    if "smule.com" in low:
        # Check cleanly without bypassing access controls; Smule often returns HTTP 403
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=BROWSER_HEADERS, verify=False) as client:
                r = await client.get(url)
                if r.status_code != 200:
                    logger.info("Smule returned HTTP %d (access restricted); using Sinhala hub fallback.", r.status_code)
                    return None
        except Exception:
            return None

    pages_cache = SITEMAP_CACHE_DIR / "pages"
    pages_cache.mkdir(parents=True, exist_ok=True)
    cache_path = pages_cache / f"{hashlib.md5(url.encode('utf-8')).hexdigest()[:14]}.json"
    if cache_path.exists():
        try:
            cached_data = json.loads(cache_path.read_text(encoding="utf-8"))
            raw_cached_lyrics = cached_data.get("lyrics_text") or ""
            if raw_cached_lyrics and '","translation"' not in raw_cached_lyrics and "\\n" not in raw_cached_lyrics:
                # Strip any leftover English header/footer lines from cached Sinhala pages
                if re.search(r"[\u0D80-\u0DFF]", raw_cached_lyrics):
                    si_lines = [
                        ln.strip()
                        for ln in raw_cached_lyrics.splitlines()
                        if ln.strip() and re.search(r"[\u0D80-\u0DFF]", ln)
                    ]
                    if si_lines:
                        cached_data["lyrics_text"] = "\n".join(si_lines)
                return cached_data
        except Exception:
            pass

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=BROWSER_HEADERS, verify=False) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None
            page_html = resp.text

        if "lyrihub.lk" in low:
            title, artist, lyrics = extract_lyrihub_lyrics(page_html)
            source = "lyrihub.lk"
        elif "lyrics-lk.com" in low:
            title, artist, lyrics = extract_lyrics_lk_lyrics(page_html)
            source = "lyrics-lk.com"
        elif "geemansala.com" in low:
            title, artist, lyrics = extract_geemansala_lyrics(page_html)
            source = "geemansala.com"
        else:
            return None

        si_count = sum(1 for c in lyrics if 0x0D80 <= ord(c) <= 0x0DFF)
        if si_count < 20:
            return None

        raw_lines = [ln for ln in lyrics.splitlines() if ln.strip()]
        shorthand_count = sum(1 for ln in raw_lines if "..." in ln or "//" in ln)
        completeness_score = len(raw_lines) - shorthand_count * 3
        clean_lyrics = _strip_blog_punctuation_for_captions(lyrics)
        lines_list = [ln for ln in clean_lyrics.splitlines() if ln.strip()]

        result = {
            "source": source_hint or source,
            "url": url,
            "title": title,
            "artist": artist,
            "lyrics_text": clean_lyrics,
            "line_count": str(len(lines_list)),
            "completeness": str(completeness_score),
        }
        try:
            cache_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        return result
    except Exception as exc:
        logger.warning("Failed fetching lyrics from %s: %s", url, exc)
        return None


SRI_LANKAN_SITE_ORDER = ["lyrihub.lk", "lyrics-lk.com", "geemansala.com"]


async def fetch_lyrics_from_single_site(
    site: str,
    query: str,
    artist_name: str = "",
    max_pages: int = 2,
) -> List[Dict[str, str]]:
    """
    Query ONE specific Sinhala lyrics site (`lyrihub.lk`, `lyrics-lk.com`, or `geemansala.com`),
    fetch its top matching pages one by one until valid lyrics are retrieved, and return results.
    """
    candidates = await search_sri_lankan_song_urls(
        query=query,
        artist_name=artist_name,
        limit=max_pages + 2,
        sites=[site],
    )
    if not candidates:
        return []

    results: List[Dict[str, str]] = []
    for cand in candidates[:max_pages]:
        # If WP REST search already extracted lyrics_text inline, use it immediately!
        if cand.get("lyrics_text"):
            results.append(cand)
            continue
        fetched = await fetch_lyrics_from_url(cand["url"], source_hint=cand["source"])
        if fetched and fetched.get("lyrics_text"):
            fetched["score"] = cand.get("score", "0")
            results.append(fetched)
    return results


async def fetch_sri_lankan_lyrics_by_title(
    query: str,
    artist_name: str = "",
    max_candidates: int = 6,
) -> Tuple[Optional[Dict[str, str]], List[Dict[str, str]]]:
    """
    Search one by one across `lyrihub.lk`, `lyrics-lk.com`, and `geemansala.com`
    by song title / artist / Sinhala name (or direct URL / Smule slug),
    returning `(best_match_with_lyrics, all_candidates)`.
    """
    # Direct URL shortcut
    if (query or "").strip().startswith(("http://", "https://")) and "smule.com" not in query.lower():
        direct = await fetch_lyrics_from_url(query.strip())
        if direct and direct.get("lyrics_text"):
            return direct, [direct]

    all_fetched: List[Dict[str, str]] = []
    seen_urls = set()

    for site in SRI_LANKAN_SITE_ORDER:
        site_hits = await fetch_lyrics_from_single_site(
            site=site,
            query=query,
            artist_name=artist_name,
            max_pages=2,
        )
        for item in site_hits:
            if item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                all_fetched.append(item)

    if not all_fetched:
        return None, []

    all_fetched.sort(
        key=lambda r: (
            float(r.get("score", "0")) * 10.0 + int(r.get("completeness", "0"))
        ),
        reverse=True,
    )
    return all_fetched[0], all_fetched[:max_candidates]

