"""Seen-video state and retry policy.

A video is fetched at most `max_retry_runs` times and only within
`max_retry_days` of the first failure. Successful transcripts are not
fetched again. Init marks everything already on the feed as seen except
videos published inside the lookback window.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

from transcriber.models import Video


class Action(str, Enum):
    FETCH = "fetch"
    SUMMARIZE = "summarize"
    SKIP = "skip"
    ABANDON = "abandon"
    ABANDON_SUMMARY = "abandon_summary"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def empty_state() -> dict:
    return {"version": 1, "videos": {}}


def load_state(path: Path) -> dict:
    if not path.exists():
        return empty_state()
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("version", 1)
    data.setdefault("videos", {})
    return data


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _exhausted(attempts: int, first_at: str | None, now: datetime, max_runs: int, max_days: int) -> bool:
    if attempts >= max_runs:
        return True
    started = parse_time(first_at)
    if started is None:
        return False
    return now - started >= timedelta(days=max_days)


def action_for(
    record: dict | None,
    *,
    now: datetime,
    max_runs: int,
    max_days: int,
    summarize: bool,
    has_llm: bool,
) -> Action:
    if record is None:
        return Action.FETCH
    status = record.get("status")
    if status in {"seen", "skipped_short", "abandoned"}:
        return Action.SKIP
    if status == "failed":
        if _exhausted(
            int(record.get("attempts") or 0),
            record.get("first_failed_at"),
            now,
            max_runs,
            max_days,
        ):
            return Action.ABANDON
        return Action.FETCH
    if status in {"transcribed", "summarized"}:
        summary_status = record.get("summary_status")
        if not summarize or summary_status == "ok" or status == "summarized":
            return Action.SKIP
        if summary_status == "abandoned":
            return Action.SKIP
        if not has_llm:
            return Action.SKIP
        if summary_status == "failed" and _exhausted(
            int(record.get("summary_attempts") or 0),
            record.get("summary_first_failed_at"),
            now,
            max_runs,
            max_days,
        ):
            return Action.ABANDON_SUMMARY
        return Action.SUMMARIZE
    return Action.FETCH


def mark_failure(record: dict | None, video: Video, error: str, now: datetime, max_runs: int, max_days: int) -> dict:
    record = dict(record or {})
    attempts = int(record.get("attempts") or 0) + 1
    first = record.get("first_failed_at") or now.isoformat()
    status = "failed"
    if _exhausted(attempts, first, now, max_runs, max_days):
        status = "abandoned"
    record.update(
        {
            "video_id": video.video_id,
            "title": video.title,
            "channel": video.channel_name,
            "channel_id": video.channel_id,
            "handle": video.handle,
            "url": video.url,
            "published": video.published.isoformat() if video.published else record.get("published"),
            "status": status,
            "error": error,
            "attempts": attempts,
            "first_failed_at": first,
            "last_attempt_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
    )
    return record


def mark_seen(video: Video, now: datetime, status: str = "seen") -> dict:
    return {
        "video_id": video.video_id,
        "title": video.title,
        "channel": video.channel_name,
        "channel_id": video.channel_id,
        "handle": video.handle,
        "url": video.url,
        "published": video.published.isoformat() if video.published else None,
        "status": status,
        "attempts": 0,
        "updated_at": now.isoformat(),
    }


def should_mark_seen_on_init(video: Video, record: dict | None, now: datetime, lookback_hours: float) -> str:
    """Return 'seen', 'short', 'recent', or 'keep'.

    'keep' means the existing record is a real outcome and init must not
    downgrade it. 'recent' means the video stays unseen so the next run
    fetches it.
    """
    if record and record.get("status") in {"transcribed", "summarized", "failed", "abandoned"}:
        return "keep"
    if video.is_short:
        return "short"
    if video.published is not None:
        published = video.published
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        if published >= now - timedelta(hours=lookback_hours):
            return "recent"
    return "seen"
