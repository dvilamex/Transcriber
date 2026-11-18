"""Transcript fetchers.

Order: youtube-transcript-api (English tracks, a few retries), then yt-dlp
English captions, then Supadata when SUPADATA_API_KEY is set. Auto-dubbed
non-English tracks are rejected. Webshare rotating residential proxies are
used when WEBSHARE_PROXY_USERNAME and WEBSHARE_PROXY_PASSWORD are set;
otherwise YOUTUBE_PROXY is an optional generic HTTP proxy.
"""

from __future__ import annotations

import json
import re
import time
from http.cookiejar import MozillaCookieJar
from pathlib import Path

from transcriber.config import RuntimeEnv
from transcriber.http import get_bytes
from transcriber.language import accept_transcript, english_rank, is_english_code
from transcriber.models import Segment, TranscriptResult


class TranscriptFailure(Exception):
    """One method failed. The caller tries the next method."""


class TranscriptError(Exception):
    def __init__(self, video_id: str, errors: list[str]):
        self.video_id = video_id
        self.errors = errors
        super().__init__(f"{video_id}: " + "; ".join(errors))


_VTT_TIME = re.compile(
    r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[.](\d{3})\s+-->\s+"
    r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[.](\d{3})"
)


def _short_error(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return (text or type(exc).__name__)[:300]


class _SilentLogger:
    def debug(self, msg):
        return None

    def info(self, msg):
        return None

    def warning(self, msg):
        return None

    def error(self, msg):
        return None


def duration_from(segments: list[Segment]) -> int | None:
    if not segments:
        return None
    end = max(segment.start + max(segment.duration, 0.0) for segment in segments)
    return int(round(end))


def parse_vtt(text: str) -> list[Segment]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    segments: list[Segment] = []
    index = 0
    while index < len(lines):
        match = _VTT_TIME.search(lines[index])
        if not match:
            index += 1
            continue
        groups = match.groups()
        start = _stamp(groups[0], groups[1], groups[2], groups[3])
        end = _stamp(groups[4], groups[5], groups[6], groups[7])
        index += 1
        buf: list[str] = []
        while index < len(lines) and lines[index].strip() and not _VTT_TIME.search(lines[index]):
            piece = re.sub(r"<[^>]+>", "", lines[index]).strip()
            if piece and not piece.startswith("NOTE"):
                buf.append(piece)
            index += 1
        if buf:
            segments.append(Segment(start, max(0.0, end - start), " ".join(buf)))
    return dedupe_rolling(segments)


def parse_json3(text: str) -> list[Segment]:
    data = json.loads(text)
    segments: list[Segment] = []
    for event in data.get("events") or []:
        parts = []
        for seg in event.get("segs") or []:
            piece = str(seg.get("utf8") or "")
            if piece.strip():
                parts.append(piece)
        phrase = " ".join("".join(parts).split())
        if not phrase:
            continue
        start = float(event.get("tStartMs") or 0) / 1000.0
        duration = float(event.get("dDurationMs") or 0) / 1000.0
        segments.append(Segment(start, duration, phrase))
    return dedupe_rolling(segments)


def dedupe_rolling(segments: list[Segment]) -> list[Segment]:
    """Collapse YouTube's rolling auto-captions, which repeat a growing line."""
    cleaned: list[Segment] = []
    for segment in segments:
        text = " ".join(segment.text.split())
        if not text:
            continue
        current = Segment(segment.start, segment.duration, text)
        if not cleaned:
            cleaned.append(current)
            continue
        previous = cleaned[-1]
        if text.startswith(previous.text) and current.start - previous.start < 8:
            cleaned[-1] = Segment(
                previous.start,
                max(previous.duration, current.start + current.duration - previous.start),
                text,
            )
            continue
        if previous.text.startswith(text) and len(text) < len(previous.text):
            continue
        cleaned.append(current)
    return cleaned


def _stamp(hours, minutes, seconds, millis) -> float:
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000.0


def proxy_config(env: RuntimeEnv):
    if env.webshare_username and env.webshare_password:
        from youtube_transcript_api.proxies import WebshareProxyConfig

        return WebshareProxyConfig(
            proxy_username=env.webshare_username,
            proxy_password=env.webshare_password,
            filter_ip_locations=env.webshare_locations or None,
            retries_when_blocked=3,
        )
    if env.youtube_proxy:
        from youtube_transcript_api.proxies import GenericProxyConfig

        return GenericProxyConfig(http_url=env.youtube_proxy, https_url=env.youtube_proxy)
    return None


def proxy_url(env: RuntimeEnv) -> str | None:
    config = proxy_config(env)
    if config is None:
        return None
    return config.https_url or config.http_url


def _session(env: RuntimeEnv):
    import requests

    session = requests.Session()
    if env.cookies_file:
        jar = MozillaCookieJar(env.cookies_file)
        jar.load(ignore_discard=True, ignore_expires=True)
        session.cookies.update(jar)
    return session


def _result(segments: list[Segment], language: str, source: str, duration: int | None) -> TranscriptResult:
    text = " ".join(segment.text for segment in segments)
    if not accept_transcript(text, language):
        raise TranscriptFailure(f"rejected non-English output ({language or 'unknown'})")
    return TranscriptResult(
        segments=segments,
        language_code=language or "en",
        source=source,
        duration_seconds=duration if duration is not None else duration_from(segments),
    )


def _fetch_api(video_id: str, env: RuntimeEnv) -> TranscriptResult:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import (
        AgeRestricted,
        NoTranscriptFound,
        TranscriptsDisabled,
        VideoUnavailable,
        VideoUnplayable,
    )

    config = proxy_config(env)
    attempts = 1 if config is not None and config.retries_when_blocked else 3
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            api = YouTubeTranscriptApi(proxy_config=config, http_client=_session(env))
            listing = api.list(video_id)
            tracks = [track for track in listing if is_english_code(track.language_code)]
            if not tracks:
                codes = sorted({track.language_code for track in listing})
                raise TranscriptFailure(
                    "no English track (available: " + (", ".join(codes) or "none") + ")"
                )
            tracks.sort(key=lambda track: (english_rank(track.language_code), track.is_generated))
            fetched = tracks[0].fetch()
            segments = [
                Segment(snippet.start, snippet.duration, snippet.text)
                for snippet in fetched.snippets
                if snippet.text and snippet.text.strip()
            ]
            return _result(segments, fetched.language_code, "youtube-transcript-api", duration_from(segments))
        except TranscriptFailure:
            raise
        except (NoTranscriptFound, TranscriptsDisabled, AgeRestricted, VideoUnavailable, VideoUnplayable) as exc:
            raise TranscriptFailure(_short_error(exc)) from exc
        except Exception as exc:  # network blocks, 429s, parse errors
            last = exc
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise TranscriptFailure(f"{type(last).__name__}: {_short_error(last)}")


def _pick_caption_track(tracks: dict) -> tuple[str, list] | None:
    candidates = []
    for code, formats in (tracks or {}).items():
        if not is_english_code(str(code)):
            continue
        name = ""
        if formats and isinstance(formats[0], dict):
            name = str(formats[0].get("name") or "")
        # Rank dubbed English below the original track. Still usable if it is all we have.
        dub_penalty = 1 if "dub" in name.lower() and "orig" not in str(code).lower() else 0
        candidates.append((english_rank(str(code))[0] + dub_penalty, str(code), formats))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1], candidates[0][2]


def _prefer_format(formats: list, lang: str) -> dict:
    order = {"json3": 0, "vtt": 1, "srv3": 2, "ttml": 3}
    ranked = sorted(formats, key=lambda fmt: order.get(str(fmt.get("ext")), 9))
    chosen = dict(ranked[0])
    chosen["lang"] = lang
    return chosen


def _fetch_ytdlp(video_id: str, env: RuntimeEnv) -> TranscriptResult:
    import yt_dlp

    url = f"https://www.youtube.com/watch?v={video_id}"
    opts: dict = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 1,
        "noplaylist": True,
        "logger": _SilentLogger(),
    }
    proxy = proxy_url(env)
    if proxy:
        opts["proxy"] = proxy
    if env.cookies_file:
        opts["cookiefile"] = env.cookies_file
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        message = _short_error(exc)
        if "429" in message:
            raise TranscriptFailure("HTTP 429") from exc
        raise TranscriptFailure(message) from exc
    picked = _pick_caption_track(info.get("subtitles") or {}) or _pick_caption_track(
        info.get("automatic_captions") or {}
    )
    if not picked:
        raise TranscriptFailure("no English captions")
    lang, formats = picked
    if not formats:
        raise TranscriptFailure("English track had no caption URL")
    chosen = _prefer_format(formats, lang)
    raw = get_bytes(chosen["url"], timeout=45, proxy=proxy)
    body = raw.decode("utf-8", "replace")
    ext = str(chosen.get("ext") or "")
    segments = parse_json3(body) if "json" in ext else parse_vtt(body)
    if not segments:
        raise TranscriptFailure("English captions were empty")
    duration = info.get("duration")
    return _result(
        segments,
        lang,
        "yt-dlp",
        int(duration) if isinstance(duration, (int, float)) else duration_from(segments),
    )


def _supadata_segments(data: dict) -> tuple[list[Segment], str]:
    lang = str(data.get("lang") or "")
    content = data.get("content")
    segments: list[Segment] = []
    if isinstance(content, str) and content.strip():
        segments.append(Segment(0.0, 0.0, content.strip()))
    elif isinstance(content, list):
        for chunk in content:
            if not isinstance(chunk, dict):
                continue
            text = str(chunk.get("text") or "").strip()
            chunk_lang = str(chunk.get("lang") or lang)
            if not text or (chunk_lang and not is_english_code(chunk_lang)):
                continue
            segments.append(
                Segment(
                    float(chunk.get("offset") or 0) / 1000.0,
                    float(chunk.get("duration") or 0) / 1000.0,
                    text,
                )
            )
    return segments, lang


def _poll_supadata(job_id: str, env: RuntimeEnv) -> dict:
    deadline = time.time() + 90
    url = f"https://api.supadata.ai/v1/transcript/{job_id}"
    headers = {"x-api-key": env.supadata_api_key, "Accept": "application/json"}
    while time.time() < deadline:
        raw = get_bytes(url, timeout=30, headers=headers)
        data = json.loads(raw.decode("utf-8"))
        status = str(data.get("status") or "").lower()
        if "content" in data and status in {"", "completed", "success"}:
            return data
        if status in {"failed", "error"}:
            raise TranscriptFailure(f"job failed: {data.get('message') or status}")
        time.sleep(2)
    raise TranscriptFailure("job timed out")


def _fetch_supadata(video_id: str, env: RuntimeEnv) -> TranscriptResult:
    if not env.supadata_api_key:
        raise TranscriptFailure("no API key")
    from urllib.parse import urlencode

    query = urlencode(
        {
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "lang": "en",
            "text": "false",
            "mode": env.supadata_mode or "native",
        }
    )
    headers = {"x-api-key": env.supadata_api_key, "Accept": "application/json"}
    raw = get_bytes(f"https://api.supadata.ai/v1/transcript?{query}", timeout=60, headers=headers)
    data = json.loads(raw.decode("utf-8"))
    if data.get("jobId") and "content" not in data:
        data = _poll_supadata(str(data["jobId"]), env)
    if data.get("error"):
        raise TranscriptFailure(str(data.get("message") or data["error"]))
    segments, lang = _supadata_segments(data)
    if not segments:
        raise TranscriptFailure("empty transcript")
    return _result(segments, lang or "en", "supadata", duration_from(segments))


def fetch_transcript(video_id: str, env: RuntimeEnv) -> TranscriptResult:
    errors: list[str] = []
    methods = (
        ("youtube-transcript-api", _fetch_api),
        ("yt-dlp", _fetch_ytdlp),
    )
    if env.supadata_api_key:
        methods = methods + (("supadata", _fetch_supadata),)
    for name, func in methods:
        try:
            return func(video_id, env)
        except TranscriptFailure as exc:
            errors.append(f"{name}: {exc}")
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {str(exc).splitlines()[0][:300]}")
    raise TranscriptError(video_id, errors)


def cookies_path_ok(env: RuntimeEnv) -> None:
    if env.cookies_file and not Path(env.cookies_file).exists():
        raise FileNotFoundError(f"YOUTUBE_COOKIES_FILE does not exist: {env.cookies_file}")
