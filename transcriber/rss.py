"""YouTube channel RSS."""

from __future__ import annotations

from datetime import datetime

import defusedxml.ElementTree as ET

from transcriber.http import get_bytes
from transcriber.models import Video

ATOM = "http://www.w3.org/2005/Atom"
YT = "http://www.youtube.com/xml/schemas/2015"

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def feed_url(channel_id: str) -> str:
    return FEED_URL.format(channel_id=channel_id)


def _text(element, tag: str) -> str:
    child = element.find(f"{{{ATOM}}}{tag}")
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _yt(element, tag: str) -> str:
    child = element.find(f"{{{YT}}}{tag}")
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def is_short_url(url: str) -> bool:
    return "/shorts/" in url.lower()


def parse_feed(
    xml_text: str,
    *,
    channel_name: str,
    channel_id: str,
    handle: str = "",
) -> list[Video]:
    root = ET.fromstring(xml_text)
    videos: list[Video] = []
    for entry in root.findall(f"{{{ATOM}}}entry"):
        video_id = _yt(entry, "videoId")
        if not video_id:
            raw_id = _text(entry, "id")
            if raw_id.startswith("yt:video:"):
                video_id = raw_id.split(":", 2)[-1]
        if not video_id:
            continue
        links = []
        for link in entry.findall(f"{{{ATOM}}}link"):
            href = link.get("href") or ""
            if href:
                links.append(href)
        url = next((href for href in links if "youtube.com" in href or "youtu.be" in href), "")
        if not url:
            url = f"https://www.youtube.com/watch?v={video_id}"
        published_raw = _text(entry, "published")
        published = datetime.fromisoformat(published_raw) if published_raw else None
        title = _text(entry, "title") or video_id
        videos.append(
            Video(
                video_id=video_id,
                title=title,
                channel_name=channel_name,
                channel_id=channel_id or _yt(entry, "channelId"),
                handle=handle,
                url=url,
                published=published,
                is_short=any(is_short_url(href) for href in links) or "#shorts" in title.lower(),
            )
        )
    return videos


def fetch_channel_videos(channel_name: str, channel_id: str, handle: str = "") -> list[Video]:
    payload = get_bytes(feed_url(channel_id), timeout=30)
    xml_text = payload.decode("utf-8", "replace")
    return parse_feed(
        xml_text,
        channel_name=channel_name,
        channel_id=channel_id,
        handle=handle,
    )
