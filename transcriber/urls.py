"""YouTube URL parsing."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def parse_video_id(value: str) -> str:
    text = value.strip()
    if _ID.match(text):
        return text
    parsed = urlparse(text)
    host = parsed.netloc.lower().removeprefix("www.")
    if host in {"youtube.com", "m.youtube.com", "music.youtube.com", "youtube-nocookie.com"}:
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [None])[0]
            if video_id and _ID.match(video_id):
                return video_id
        match = re.match(r"^/(?:shorts|embed|live|v)/([A-Za-z0-9_-]{11})", parsed.path)
        if match:
            return match.group(1)
    if host == "youtu.be":
        match = re.match(r"^/([A-Za-z0-9_-]{11})", parsed.path)
        if match:
            return match.group(1)
    raise ValueError(f"Could not parse a YouTube video id from {value!r}")
