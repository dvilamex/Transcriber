"""Markdown for one saved transcript."""

from __future__ import annotations

from datetime import datetime, timezone

import yaml

from transcriber.chunking import format_timestamp
from transcriber.models import Segment, Summary, Video


def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "unknown"
    hours, rem = divmod(int(seconds), 3600)
    minutes, _secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def slugify(name: str) -> str:
    lowered = name.casefold()
    chars = []
    prev_dash = False
    for char in lowered:
        if char.isalnum():
            chars.append(char)
            prev_dash = False
        elif not prev_dash:
            chars.append("-")
            prev_dash = True
    return "".join(chars).strip("-") or "channel"


def transcript_filename(video: Video) -> str:
    if video.published:
        day = video.published.date().isoformat()
    else:
        day = "undated"
    return f"{slugify(video.channel_name)}/{day}-{video.video_id}.md"


def render_transcript(
    video: Video,
    segments: list[Segment],
    *,
    source: str,
    language_code: str,
    duration_seconds: int | None,
    summary: Summary | None,
    summary_note: str = "",
) -> str:
    front = {
        "title": video.title,
        "channel": video.channel_name,
        "channel_id": video.channel_id,
        "handle": video.handle,
        "video_id": video.video_id,
        "url": video.url,
        "published": video.published.isoformat() if video.published else None,
        "duration_seconds": duration_seconds,
        "language": language_code,
        "transcript_source": source,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "guests": list(summary.guests) if summary else [],
    }
    dumped = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip()
    lines = ["---", dumped, "---", "", f"# {video.title}", ""]
    handle = f" ({video.handle})" if video.handle else ""
    published = video.published.date().isoformat() if video.published else "unknown"
    lines.append(f"- **Channel:** {video.channel_name}{handle}")
    lines.append(f"- **Published:** {published}")
    lines.append(f"- **Duration:** {format_duration(duration_seconds)}")
    lines.append(f"- **URL:** {video.url}")
    lines.append("")
    if summary and (summary.tldr or summary.takeaways or summary.topics):
        lines.extend(_summary_lines(summary))
    elif summary_note:
        lines.extend([summary_note, ""])
    lines.extend(["## Transcript", ""])
    for segment in segments:
        text = " ".join(segment.text.split())
        if not text:
            continue
        lines.append(f"[{format_timestamp(segment.start)}] {text}")
    lines.append("")
    return "\n".join(lines)


def _summary_lines(summary: Summary) -> list[str]:
    lines = []
    if summary.tldr:
        lines.extend(["## TL;DR", "", summary.tldr, ""])
    if summary.takeaways:
        lines.extend(["## Key takeaways", ""])
        lines.extend(f"- {item}" for item in summary.takeaways)
        lines.append("")
    if summary.claims:
        lines.extend(["## Notable claims and numbers", ""])
        for claim in summary.claims:
            if claim.timestamp:
                lines.append(f"- **[{claim.timestamp}]** {claim.text}")
            else:
                lines.append(f"- {claim.text}")
        lines.append("")
    if summary.entities:
        lines.extend(["## People and companies", ""])
        for entity in summary.entities:
            if entity.context:
                lines.append(f"- **{entity.name}** — {entity.context}")
            else:
                lines.append(f"- **{entity.name}**")
        lines.append("")
    if summary.why_it_matters:
        lines.extend(["## Why it matters", "", summary.why_it_matters, ""])
    return lines


def parse_transcript_segments(markdown: str) -> list[Segment]:
    """Read the timestamped lines back out of a saved transcript."""
    in_transcript = False
    segments: list[Segment] = []
    previous_start = None
    for line in markdown.splitlines():
        if line.strip() == "## Transcript":
            in_transcript = True
            continue
        if not in_transcript:
            continue
        if len(line) < 12 or line[0] != "[":
            continue
        end = line.find("]")
        if end < 0:
            continue
        stamp = line[1:end]
        text = line[end + 1 :].strip()
        if not text:
            continue
        try:
            parts = [int(part) for part in stamp.split(":")]
        except ValueError:
            continue
        if len(parts) != 3:
            continue
        start = parts[0] * 3600 + parts[1] * 60 + parts[2]
        if previous_start is not None and segments:
            segments[-1].duration = max(0, start - previous_start)
        segments.append(Segment(start=float(start), duration=0.0, text=text))
        previous_start = start
    return segments
