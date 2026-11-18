"""Render and merge the daily theme digest."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from transcriber.topics import DigestPlan

_VIDEO_MARK = re.compile(r"<!--\s*video:([A-Za-z0-9_-]{11})\s*-->")


def _escape_label(title: str) -> str:
    return title.replace("[", "(").replace("]", ")")


def render_digest(run_date: date, plan: DigestPlan) -> str:
    lines = [
        f"# Digest — {run_date.isoformat()}",
        "",
        "Themes across the episodes in this run. A point already covered in the last "
        "7 days is left out unless a number or conclusion changed. Each point is "
        "stated once, with the shows that said it.",
        "",
    ]
    if not plan.themes:
        lines.extend(["No new themes.", ""])
    else:
        for theme in plan.themes:
            lines.append(f"## {theme.topic}")
            lines.append("")
            for bullet in theme.bullets:
                lines.append(f"- {bullet.line}")
            lines.append("")
    lines.extend(["## Episodes", ""])
    if not plan.episodes:
        lines.append("No episodes.")
        lines.append("")
    for episode in plan.episodes:
        lines.append(f"<!-- video:{episode.video_id} -->")
        parts = [
            f"**[{_escape_label(episode.title)}]({episode.url})**",
            episode.channel,
        ]
        if episode.guests:
            parts.append(", ".join(episode.guests))
        if episode.unique:
            parts.append(episode.unique)
        lines.append("- " + " — ".join(parts))
    lines.append("")
    return "\n".join(lines)


def _theme_heading(line: str) -> str | None:
    if line.startswith("## ") and not line.startswith("## Episodes"):
        return line[3:].strip()
    return None


def merge_digest(existing: str, run_date: date, plan: DigestPlan) -> str:
    """Insert this run's themes and episodes into today's digest file."""
    if not existing.strip():
        return render_digest(run_date, plan)
    text = existing
    if f"# Digest — {run_date.isoformat()}" not in text.splitlines()[0:3]:
        # Different document; the caller should be writing a date-named file.
        return render_digest(run_date, plan)

    episode_split = re.split(r"^## Episodes\s*$", text, maxsplit=1, flags=re.MULTILINE)
    theme_body = episode_split[0]
    episode_body = episode_split[1] if len(episode_split) > 1 else "\n"

    existing_episode_ids = set(_VIDEO_MARK.findall(episode_body))
    blocks = re.split(r"(?=^## )", theme_body, flags=re.MULTILINE)
    preamble = blocks[0]
    sections: dict[str, str] = {}
    order: list[str] = []
    for block in blocks[1:]:
        heading_line = block.strip().splitlines()[0]
        heading = _theme_heading(heading_line)
        if heading is None:
            continue
        key = heading.casefold()
        sections[key] = block.strip("\n")
        order.append(key)

    for theme in plan.themes:
        key = theme.topic.casefold()
        new_bullets = []
        for bullet in theme.bullets:
            if key in sections and bullet.line in sections[key]:
                continue
            new_bullets.append(f"- {bullet.line}")
        if not new_bullets:
            continue
        if key in sections:
            sections[key] = sections[key].rstrip() + "\n" + "\n".join(new_bullets)
        else:
            chunk = "## " + theme.topic + "\n\n" + "\n".join(new_bullets)
            sections[key] = chunk
            order.append(key)

    theme_text = preamble.rstrip() + "\n\n"
    if not any(sections):
        if "No new themes." not in preamble:
            theme_text += "No new themes.\n\n"
    else:
        # Drop a stale "No new themes." line once a real theme exists.
        theme_text = re.sub(r"\nNo new themes\.\n+", "\n", theme_text)
        for key in order:
            theme_text += sections[key].rstrip() + "\n\n"

    rendered_episodes = render_digest(run_date, plan)
    new_episode_section = rendered_episodes.split("## Episodes", 1)[1]
    new_lines = []
    pending_id = None
    replacements = {}
    current_id = None
    buffer: list[str] = []

    def flush_new():
        nonlocal current_id, buffer
        if current_id and buffer:
            replacements[current_id] = "\n".join(buffer).rstrip()
        current_id = None
        buffer = []

    for line in new_episode_section.splitlines():
        mark = _VIDEO_MARK.search(line)
        if mark:
            flush_new()
            current_id = mark.group(1)
            buffer = [line]
            continue
        if current_id and line.startswith("- "):
            buffer.append(line)
            flush_new()
    flush_new()

    kept_ids = set()
    output_episodes: list[str] = []
    skip_next_item = False
    for line in episode_body.splitlines():
        mark = _VIDEO_MARK.search(line)
        if mark:
            pending_id = mark.group(1)
            if pending_id in replacements:
                output_episodes.append(replacements[pending_id])
                kept_ids.add(pending_id)
                skip_next_item = True
            else:
                output_episodes.append(line.rstrip())
                skip_next_item = False
            continue
        if skip_next_item and line.startswith("- "):
            skip_next_item = False
            continue
        if line.strip():
            output_episodes.append(line.rstrip())
    for video_id, block in replacements.items():
        if video_id not in kept_ids and video_id not in existing_episode_ids:
            output_episodes.append(block)

    episode_text = "\n".join(line for line in output_episodes if line is not None).strip()
    if not episode_text:
        episode_text = "No episodes."
    return theme_text.rstrip() + "\n\n## Episodes\n\n" + episode_text + "\n"


def write_digest(path: Path, run_date: date, plan: DigestPlan) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(merge_digest(existing, run_date, plan), encoding="utf-8")

