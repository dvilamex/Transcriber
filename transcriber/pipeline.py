"""Init, daily run, and one-off video commands."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from transcriber.config import RuntimeEnv, Settings
from transcriber.lock import LockError, RunLock
from transcriber.models import Summary, Video
from transcriber.render import parse_transcript_segments, render_transcript, transcript_filename
from transcriber.rss import fetch_channel_videos
from transcriber.state import (
    Action,
    action_for,
    load_state,
    mark_failure,
    mark_seen,
    save_state,
    should_mark_seen_on_init,
)
from transcriber.summarize import SummaryError, build_llm, summarize_transcript
from transcriber.topics import (
    EpisodeInput,
    extend_covered,
    load_covered,
    plan_digest,
    save_covered,
)
from transcriber.transcript import TranscriptError, cookies_path_ok, fetch_transcript
from transcriber.digest import write_digest
from transcriber.http import get_bytes

log = logging.getLogger("transcriber")
MEXICO_CITY = ZoneInfo("America/Mexico_City")


def local_today(now: datetime | None = None):
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(MEXICO_CITY).date()


def init_channels(settings: Settings, hours: float | None = None) -> int:
    lookback = settings.init_lookback_hours if hours is None else hours
    now = datetime.now(timezone.utc)
    with RunLock(settings.lock_file):
        state = load_state(settings.state_file)
        marked = 0
        recent = 0
        shorts = 0
        for channel in settings.enabled_channels():
            videos = fetch_channel_videos(channel.name, channel.channel_id, channel.handle)
            for video in videos:
                record = state["videos"].get(video.video_id)
                outcome = should_mark_seen_on_init(video, record, now, lookback)
                if outcome == "keep":
                    continue
                if outcome == "recent":
                    if record and record.get("status") in {"seen", "skipped_short"}:
                        state["videos"].pop(video.video_id, None)
                    recent += 1
                    continue
                status = "skipped_short" if outcome == "short" else "seen"
                state["videos"][video.video_id] = mark_seen(video, now, status)
                if outcome == "short":
                    shorts += 1
                else:
                    marked += 1
            log.info("%s: %s videos in the feed", channel.name, len(videos))
        save_state(settings.state_file, state)
    log.info(
        "init marked %s videos seen, %s shorts skipped, %s recent left for the next run (lookback %sh)",
        marked,
        shorts,
        recent,
        lookback,
    )
    return 0


def run_pipeline(
    settings: Settings,
    env: RuntimeEnv,
    *,
    since_hours: float | None = None,
    limit: int | None = None,
    summarize: bool = True,
) -> int:
    cookies_path_ok(env)
    llm = _llm(env, summarize)
    now = datetime.now(timezone.utc)
    failures = 0
    with RunLock(settings.lock_file):
        state = load_state(settings.state_file)
        candidates, rss_failures = _collect(settings, state, now, since_hours, summarize, llm is not None)
        if limit is not None:
            candidates = candidates[:limit]
        if not candidates:
            log.info("No new videos.")
            save_state(settings.state_file, state)
            return 1 if rss_failures else 0
        produced = []
        for video, action in candidates:
            try:
                item = _process_video(settings, env, state, video, action, llm, summarize, now)
                produced.append(item)
                if item.get("summary_failed"):
                    failures += 1
                log.info("saved %s (%s)", video.video_id, video.title)
            except (TranscriptError, SummaryError, OSError) as exc:
                failures += 1
                message = _redact(str(exc), env)
                state["videos"][video.video_id] = mark_failure(
                    state["videos"].get(video.video_id),
                    video,
                    message,
                    now,
                    settings.max_retry_runs,
                    settings.max_retry_days,
                )
                log.warning("failed %s: %s", video.video_id, message)
            save_state(settings.state_file, state)
        if produced:
            _write_run_digest(settings, produced, now)
            save_state(settings.state_file, state)
    log.info("run finished: %s saved, %s failed", len(produced), failures)
    return 1 if failures or rss_failures else 0


def process_video_url(
    settings: Settings,
    env: RuntimeEnv,
    url: str,
    *,
    summarize: bool = True,
) -> int:
    from transcriber.urls import parse_video_id

    cookies_path_ok(env)
    video_id = parse_video_id(url)
    llm = _llm(env, summarize)
    now = datetime.now(timezone.utc)
    meta = _oembed(video_id)
    channel_name = str(meta.get("author_name") or "YouTube")
    title = str(meta.get("title") or video_id)
    channel = _match_channel(settings, channel_name)
    video = Video(
        video_id=video_id,
        title=title,
        channel_name=channel.name if channel else channel_name,
        channel_id=channel.channel_id if channel else "",
        handle=channel.handle if channel else "",
        url=f"https://www.youtube.com/watch?v={video_id}",
        published=None,
        is_short="/shorts/" in url,
    )
    with RunLock(settings.lock_file):
        state = load_state(settings.state_file)
        try:
            item = _process_video(settings, env, state, video, Action.FETCH, llm, summarize, now)
        except (TranscriptError, SummaryError, OSError) as exc:
            message = _redact(str(exc), env)
            state["videos"][video.video_id] = mark_failure(
                state["videos"].get(video.video_id),
                video,
                message,
                now,
                settings.max_retry_runs,
                settings.max_retry_days,
            )
            save_state(settings.state_file, state)
            log.error("%s", message)
            return 1
        _write_run_digest(settings, [item], now)
        save_state(settings.state_file, state)
    log.info("saved %s", item["path"])
    return 1 if item.get("summary_failed") else 0


def _llm(env: RuntimeEnv, summarize: bool):
    if not summarize:
        return None
    return build_llm(env)


def _match_channel(settings: Settings, author_name: str):
    folded = author_name.casefold()
    for channel in settings.channels:
        if channel.name.casefold() in folded or folded in channel.name.casefold():
            return channel
    return None


def _oembed(video_id: str) -> dict:
    from urllib.parse import urlencode

    url = "https://www.youtube.com/oembed?" + urlencode(
        {"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"}
    )
    try:
        return json.loads(get_bytes(url, timeout=20).decode("utf-8"))
    except Exception as exc:
        log.info("oEmbed lookup failed for %s: %s", video_id, exc)
        return {}


def _redact(message: str, env: RuntimeEnv) -> str:
    redacted = message
    for secret in (
        env.webshare_password,
        env.webshare_username,
        env.youtube_proxy,
        env.supadata_api_key,
        env.openai_api_key,
        env.anthropic_api_key,
    ):
        if secret and len(secret) >= 4:
            redacted = redacted.replace(secret, "***")
    return redacted


def _collect(settings, state, now, since_hours, summarize, has_llm):
    found = []
    rss_failures = 0
    cutoff = None
    if since_hours is not None:
        cutoff = now - timedelta(hours=since_hours)
    for channel in settings.enabled_channels():
        try:
            videos = fetch_channel_videos(channel.name, channel.channel_id, channel.handle)
        except Exception as exc:
            rss_failures += 1
            log.error("RSS failed for %s: %s", channel.name, exc)
            continue
        for video in videos:
            record = state["videos"].get(video.video_id)
            if video.is_short:
                if not record or record.get("status") not in {"transcribed", "summarized"}:
                    state["videos"][video.video_id] = mark_seen(video, now, "skipped_short")
                continue
            if cutoff is not None and video.published is not None:
                published = video.published
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                if published < cutoff and not (record and record.get("status") == "failed"):
                    continue
            decision = action_for(
                record,
                now=now,
                max_runs=settings.max_retry_runs,
                max_days=settings.max_retry_days,
                summarize=summarize,
                has_llm=has_llm,
            )
            if decision == Action.ABANDON:
                record = dict(record or {})
                record["status"] = "abandoned"
                record["updated_at"] = now.isoformat()
                state["videos"][video.video_id] = record
                log.info("giving up on %s after retry cap", video.video_id)
                continue
            if decision == Action.ABANDON_SUMMARY:
                record = dict(record or {})
                record["summary_status"] = "abandoned"
                record["updated_at"] = now.isoformat()
                state["videos"][video.video_id] = record
                continue
            if decision in {Action.FETCH, Action.SUMMARIZE}:
                retry = bool(record and record.get("status") == "failed")
                published = video.published.timestamp() if video.published else 0
                found.append((1 if retry else 0, -published, video, decision))
    found.sort(key=lambda item: (item[0], item[1]))
    return [(video, decision) for _retry, _stamp, video, decision in found], rss_failures


def _process_video(settings, env, state, video: Video, action: Action, llm, summarize: bool, now: datetime):
    record = dict(state["videos"].get(video.video_id) or {})
    segments = None
    source = record.get("transcript_source") or ""
    language = record.get("language") or ""
    duration = record.get("duration_seconds")
    relative = record.get("transcript_path")
    path = settings.transcripts_dir / relative if relative else None
    if action == Action.SUMMARIZE and path and path.exists():
        segments = parse_transcript_segments(path.read_text(encoding="utf-8"))
    if not segments:
        fetched = fetch_transcript(video.video_id, env)
        segments = fetched.segments
        source = fetched.source
        language = fetched.language_code
        duration = fetched.duration_seconds
    summary = None
    summary_status = record.get("summary_status")
    summary_note = ""
    summary_failed = False
    if summarize and llm is not None:
        try:
            summary = summarize_transcript(
                segments,
                title=video.title,
                channel=video.channel_name,
                client=llm,
                chunk_chars=settings.chunk_chars,
                chunk_overlap=settings.chunk_overlap,
            )
            summary_status = "ok"
        except SummaryError as exc:
            summary_failed = True
            summary_status = "failed"
            attempts = int(record.get("summary_attempts") or 0) + 1
            message = _redact(str(exc), env)
            record["summary_attempts"] = attempts
            record["summary_error"] = message
            record["summary_first_failed_at"] = record.get("summary_first_failed_at") or now.isoformat()
            summary_note = f"Summary failed and will be retried: {message}"
            log.warning("summary failed for %s: %s", video.video_id, message)
    elif summarize:
        summary_status = "skipped"
        summary_note = "Summary skipped (no LLM API key)."
    else:
        summary_status = "skipped"
        summary_note = "Summary skipped (--no-summarize)."

    relative_path = transcript_filename(video)
    target = settings.transcripts_dir / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        render_transcript(
            video,
            segments,
            source=source,
            language_code=language,
            duration_seconds=duration,
            summary=summary,
            summary_note=summary_note,
        ),
        encoding="utf-8",
    )
    status = "summarized" if summary_status == "ok" else "transcribed"
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
            "transcript_path": relative_path,
            "transcript_source": source,
            "language": language,
            "duration_seconds": duration,
            "summary_status": summary_status,
            "summary": summary.to_dict() if summary else record.get("summary"),
            "error": None,
            "updated_at": now.isoformat(),
        }
    )
    state["videos"][video.video_id] = record
    return {
        "video": video,
        "summary": summary or Summary.from_dict(record.get("summary")),
        "path": target,
        "summary_failed": summary_failed,
    }


def _write_run_digest(settings: Settings, produced: list[dict], now: datetime) -> None:
    day = local_today(now)
    episodes = [
        EpisodeInput(
            video_id=item["video"].video_id,
            title=item["video"].title,
            url=item["video"].url,
            channel=item["video"].channel_name,
            published=item["video"].published,
            summary=item["summary"] or Summary(),
        )
        for item in produced
    ]
    covered = load_covered(settings.covered_topics_file)
    plan = plan_digest(episodes, covered, day, settings.dedupe_window_days)
    path = settings.digests_dir / f"{day.isoformat()}.md"
    write_digest(path, day, plan)
    save_covered(
        settings.covered_topics_file,
        extend_covered(covered, plan.covered, day),
    )
    log.info("digest %s (%s themes)", path, len(plan.themes))
