from datetime import datetime, timedelta, timezone

from transcriber.models import Video
from transcriber.state import Action, action_for, mark_failure, should_mark_seen_on_init

NOW = datetime(2026, 11, 18, 13, 0, tzinfo=timezone.utc)


def video(video_id="abcdefghijk", hours_ago=1, short=False):
    return Video(
        video_id=video_id,
        title="Episode",
        channel_name="20VC",
        channel_id="UCf0PBRjhf0rF8fWBIxTuoWA",
        handle="@20VC",
        url=f"https://www.youtube.com/watch?v={video_id}",
        published=NOW - timedelta(hours=hours_ago),
        is_short=short,
    )


def test_unseen_video_is_fetched_and_seen_video_is_skipped():
    assert action_for(None, now=NOW, max_runs=8, max_days=7, summarize=True, has_llm=True) is Action.FETCH
    seen = {"status": "seen"}
    assert action_for(seen, now=NOW, max_runs=8, max_days=7, summarize=True, has_llm=True) is Action.SKIP
    done = {"status": "summarized", "summary_status": "ok"}
    assert action_for(done, now=NOW, max_runs=8, max_days=7, summarize=True, has_llm=True) is Action.SKIP


def test_failures_retry_until_attempt_or_age_cap():
    record = {"status": "failed", "attempts": 3, "first_failed_at": (NOW - timedelta(days=1)).isoformat()}
    assert action_for(record, now=NOW, max_runs=8, max_days=7, summarize=True, has_llm=False) is Action.FETCH

    capped = {"status": "failed", "attempts": 8, "first_failed_at": NOW.isoformat()}
    assert action_for(capped, now=NOW, max_runs=8, max_days=7, summarize=True, has_llm=False) is Action.ABANDON

    stale = {
        "status": "failed",
        "attempts": 2,
        "first_failed_at": (NOW - timedelta(days=7)).isoformat(),
    }
    assert action_for(stale, now=NOW, max_runs=8, max_days=7, summarize=True, has_llm=False) is Action.ABANDON


def test_mark_failure_abandons_on_the_eighth_attempt():
    record = None
    current = NOW - timedelta(days=1)
    item = video()
    for _ in range(7):
        record = mark_failure(record, item, "blocked", current, 8, 7)
        assert record["status"] == "failed"
    record = mark_failure(record, item, "blocked", NOW, 8, 7)
    assert record["status"] == "abandoned"
    assert record["attempts"] == 8


def test_init_keeps_recent_videos_and_marks_older_ones_seen():
    recent = video(hours_ago=2)
    older = video("zxywvutsrqp", hours_ago=100)
    short = video("shortvideo1", hours_ago=1, short=True)
    summarized = {"status": "summarized", "summary_status": "ok"}
    assert should_mark_seen_on_init(recent, None, NOW, 48) == "recent"
    assert should_mark_seen_on_init(older, None, NOW, 48) == "seen"
    assert should_mark_seen_on_init(short, None, NOW, 48) == "short"
    assert should_mark_seen_on_init(older, summarized, NOW, 48) == "keep"


def test_transcript_without_summary_is_resumed_when_a_key_exists():
    record = {"status": "transcribed", "summary_status": "skipped"}
    assert (
        action_for(record, now=NOW, max_runs=8, max_days=7, summarize=True, has_llm=False)
        is Action.SKIP
    )
    assert (
        action_for(record, now=NOW, max_runs=8, max_days=7, summarize=True, has_llm=True)
        is Action.SUMMARIZE
    )
