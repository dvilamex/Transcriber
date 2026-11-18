"""Split long transcripts into overlapping chunks for map-reduce."""

from __future__ import annotations

from dataclasses import dataclass

from transcriber.models import Segment


@dataclass
class Chunk:
    index: int
    start: float
    end: float
    text: str


def format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def parse_timestamp(value: str) -> float:
    parts = value.strip().split(":")
    if len(parts) == 3:
        hours, minutes, secs = parts
    elif len(parts) == 2:
        hours = "0"
        minutes, secs = parts
    else:
        raise ValueError(f"bad timestamp: {value}")
    return int(hours) * 3600 + int(minutes) * 60 + float(secs)


def chunk_segments(
    segments: list[Segment],
    max_chars: int = 12000,
    overlap_chars: int = 400,
) -> list[Chunk]:
    """Group timestamped lines into chunks.

    A single line longer than max_chars is kept intact. Each later chunk repeats
    the tail of the previous one so a claim on the boundary is not split away
    from its context. The index always advances.
    """
    if max_chars < 200:
        raise ValueError("max_chars must be at least 200")
    lines: list[tuple[float, float, str]] = []
    for segment in segments:
        text = " ".join(segment.text.split())
        if not text:
            continue
        line = f"[{format_timestamp(segment.start)}] {text}"
        end = segment.start + max(segment.duration, 0.0)
        lines.append((segment.start, end, line))
    if not lines:
        return []

    chunks: list[Chunk] = []
    index = 0
    count = len(lines)
    steps = 0
    while index < count:
        steps += 1
        if steps > count + 2:
            raise RuntimeError("chunker failed to advance")
        buf: list[str] = []
        size = 0
        cursor = index
        while cursor < count:
            extra = len(lines[cursor][2]) + (1 if buf else 0)
            if buf and size + extra > max_chars:
                break
            buf.append(lines[cursor][2])
            size += extra
            cursor += 1
        chunks.append(
            Chunk(
                index=len(chunks),
                start=lines[index][0],
                end=lines[cursor - 1][1],
                text="\n".join(buf),
            )
        )
        if cursor >= count:
            break
        overlap_index = cursor
        overlap = 0
        while overlap_index > index and overlap < overlap_chars:
            overlap_index -= 1
            overlap += len(lines[overlap_index][2]) + 1
        if overlap_index <= index:
            overlap_index = index + 1 if cursor > index + 1 else cursor
        if overlap_index <= index:
            overlap_index = cursor
        index = overlap_index
    return chunks
