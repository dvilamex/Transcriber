from datetime import datetime, timedelta, timezone

from transcriber.config import Channel, Settings
from transcriber.models import Segment, Summary, Topic, TopicPoint, Video
from transcriber.pipeline import init_channels, run_pipeline
from transcriber.config import RuntimeEnv


NOW = datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc)


def _settings(tmp_path) -> Settings:
    return Settings(
        channels=[
            Channel("20VC", "UCf0PBRjhf0rF8fWBIxTuoWA", True, "@20VC"),
            Channel("TBPN", "UC-DRzaGnL_vtBUpCFH5M0tg", True, "@TBPNLive"),
        ],
        transcripts_dir=tmp_path / "transcripts",
        digests_dir=tmp_path / "digests",
        state_file=tmp_path / "state" / "seen.json",
        covered_topics_file=tmp_path / "state" / "covered_topics.json",
        lock_file=tmp_path / "state" / "transcriber.lock",
        dedupe_window_days=7,
    )


def _video(video_id, channel, title, hours_ago):
    return Video(
        video_id=video_id,
        title=title,
        channel_name=channel,
        channel_id="UCxxxxxxxxxxxxxxxxxxxxxx",
        handle="",
        url=f"https://www.youtube.com/watch?v={video_id}",
        published=NOW - timedelta(hours=hours_ago),
        is_short=video_id.startswith("short"),
    )


def test_run_writes_transcripts_and_a_deduped_digest(tmp_path, monkeypatch):
    videos = [
        _video("abcdefghijk", "20VC", "Cost curve", 2),
        _video("bcdefghijkl", "TBPN", "Same cost curve", 1),
        _video("shortvideo1", "20VC", "A short", 1),
    ]

    def fake_feed(name, channel_id, handle=""):
        return [video for video in videos if video.channel_name == name]

    def fake_transcript(video_id, env):
        from transcriber.models import TranscriptResult

        return TranscriptResult(
            segments=[Segment(0, 4, "Inference prices are falling quickly across every lab.")],
            language_code="en",
            source="youtube-transcript-api",
            duration_seconds=4,
        )

    def fake_summary(segments, *, title, channel, client, chunk_chars, chunk_overlap):
        point = "Inference prices are falling quickly"
        unique = "Only this guest named a private cluster deal." if channel == "20VC" else ""
        return Summary(
            tldr=point,
            takeaways=[point],
            guests=["Guest"],
            unique=unique,
            why_it_matters="Budgets move.",
            topics=[Topic("inference costs", [TopicPoint(point, [])])],
        )

    monkeypatch.setattr("transcriber.pipeline.fetch_channel_videos", fake_feed)
    monkeypatch.setattr("transcriber.pipeline.fetch_transcript", fake_transcript)
    monkeypatch.setattr("transcriber.pipeline.build_llm", lambda env: object())
    monkeypatch.setattr("transcriber.pipeline.summarize_transcript", fake_summary)
    monkeypatch.setattr("transcriber.pipeline.datetime", _FixedDateTime)

    settings = _settings(tmp_path)
    code = run_pipeline(settings, RuntimeEnv(openai_api_key="test"), summarize=True)
    assert code == 0
    saved = list((tmp_path / "transcripts").rglob("*.md"))
    assert len(saved) == 2
    digest = next((tmp_path / "digests").glob("*.md")).read_text(encoding="utf-8")
    assert digest.count("Inference prices are falling quickly") == 1
    assert "20VC" in digest and "TBPN" in digest
    assert "private cluster deal" in digest
    assert "A short" not in digest

    second = run_pipeline(settings, RuntimeEnv(openai_api_key="test"), summarize=True)
    assert second == 0
    assert len(list((tmp_path / "transcripts").rglob("*.md"))) == 2


def test_init_marks_old_feed_items_and_leaves_recent_ones(tmp_path, monkeypatch):
    videos = [
        _video("abcdefghijk", "20VC", "Recent", 2),
        _video("bcdefghijkl", "20VC", "Old", 100),
    ]
    monkeypatch.setattr(
        "transcriber.pipeline.fetch_channel_videos",
        lambda name, channel_id, handle="": videos if name == "20VC" else [],
    )
    monkeypatch.setattr("transcriber.pipeline.datetime", _FixedDateTime)
    settings = _settings(tmp_path)
    assert init_channels(settings, hours=48) == 0
    state = settings.state_file.read_text(encoding="utf-8")
    assert "bcdefghijkl" in state
    assert "abcdefghijk" not in state


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return NOW.replace(tzinfo=None)
        return NOW.astimezone(tz)
