import json
from datetime import datetime, timedelta, timezone

from transcriber.config import Channel, Settings
from transcriber.models import Segment, Summary, Topic, TopicPoint, Video
from transcriber.pipeline import init_channels, run_pipeline
from transcriber.config import RuntimeEnv


NOW = datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc)


RAW_TRANSCRIPT = "Inference prices are falling quickly across every lab."


def _settings(tmp_path) -> Settings:
    return Settings(
        channels=[
            Channel("20VC", "UCf0PBRjhf0rF8fWBIxTuoWA", True, "@20VC"),
            Channel("TBPN", "UC-DRzaGnL_vtBUpCFH5M0tg", True, "@TBPNLive"),
        ],
        digests_dir=tmp_path / "digests",
        state_file=tmp_path / "state" / "seen.json",
        covered_topics_file=tmp_path / "state" / "covered_topics.json",
        lock_file=tmp_path / "state" / "transcriber.lock",
        dedupe_window_days=7,
    )


def _assert_no_transcript_files(root) -> None:
    assert not (root / "transcripts").exists()
    for path in root.rglob("*"):
        assert "transcripts" not in path.parts
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        assert RAW_TRANSCRIPT not in text
        assert "## Transcript" not in text


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


def test_run_writes_a_deduped_digest_and_no_transcript_file(tmp_path, monkeypatch):
    videos = [
        _video("abcdefghijk", "20VC", "Cost curve", 2),
        _video("bcdefghijkl", "TBPN", "Same cost curve", 1),
        _video("shortvideo1", "20VC", "A short", 1),
    ]
    fetches = []

    def fake_feed(name, channel_id, handle=""):
        return [video for video in videos if video.channel_name == name]

    def fake_transcript(video_id, env):
        from transcriber.models import TranscriptResult

        fetches.append(video_id)
        return TranscriptResult(
            segments=[Segment(0, 4, RAW_TRANSCRIPT)],
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
    _assert_no_transcript_files(tmp_path)
    markdown = [path for path in tmp_path.rglob("*.md")]
    assert markdown
    assert all(path.parent == tmp_path / "digests" for path in markdown)
    digest = markdown[0].read_text(encoding="utf-8")
    assert digest.count("Inference prices are falling quickly") == 1
    assert "20VC" in digest and "TBPN" in digest
    assert "private cluster deal" in digest
    assert "A short" not in digest
    state = json.loads(settings.state_file.read_text(encoding="utf-8"))
    saved = state["videos"]["abcdefghijk"]
    assert saved["status"] == "summarized"
    assert saved["summary"]["tldr"] == "Inference prices are falling quickly"
    assert saved["summary"]["why_it_matters"] == "Budgets move."
    assert "transcript_path" not in saved
    assert RAW_TRANSCRIPT not in json.dumps(state)

    second = run_pipeline(settings, RuntimeEnv(openai_api_key="test"), summarize=True)
    assert second == 0
    assert sorted(fetches) == ["abcdefghijk", "bcdefghijkl"]
    _assert_no_transcript_files(tmp_path)


def test_unsummarized_video_is_refetched_without_writing_a_transcript(tmp_path, monkeypatch):
    video = _video("abcdefghijk", "20VC", "Cost curve", 2)
    fetches = []

    def fake_transcript(video_id, env):
        from transcriber.models import TranscriptResult

        fetches.append(video_id)
        return TranscriptResult(
            segments=[Segment(12, 4, RAW_TRANSCRIPT)],
            language_code="en",
            source="youtube-transcript-api",
            duration_seconds=30,
        )

    def fake_summary(segments, *, title, channel, client, chunk_chars, chunk_overlap):
        assert segments[0].text == RAW_TRANSCRIPT
        return Summary(
            tldr="Inference prices are falling quickly",
            takeaways=["Inference prices are falling quickly"],
            why_it_matters="Budgets move.",
            topics=[Topic("inference costs", [TopicPoint("Inference prices are falling quickly", [])])],
        )

    monkeypatch.setattr(
        "transcriber.pipeline.fetch_channel_videos",
        lambda name, channel_id, handle="": [video] if name == "20VC" else [],
    )
    monkeypatch.setattr("transcriber.pipeline.fetch_transcript", fake_transcript)
    monkeypatch.setattr("transcriber.pipeline.build_llm", lambda env: object())
    monkeypatch.setattr("transcriber.pipeline.summarize_transcript", fake_summary)
    monkeypatch.setattr("transcriber.pipeline.datetime", _FixedDateTime)

    settings = _settings(tmp_path)
    assert run_pipeline(settings, RuntimeEnv(), summarize=False) == 0
    _assert_no_transcript_files(tmp_path)
    state = json.loads(settings.state_file.read_text(encoding="utf-8"))
    assert state["videos"]["abcdefghijk"]["status"] == "transcribed"
    assert state["videos"]["abcdefghijk"]["summary_status"] == "skipped"
    assert "summary" not in state["videos"]["abcdefghijk"]

    assert run_pipeline(settings, RuntimeEnv(openai_api_key="test"), summarize=True) == 0
    assert fetches == ["abcdefghijk", "abcdefghijk"]
    _assert_no_transcript_files(tmp_path)
    state = json.loads(settings.state_file.read_text(encoding="utf-8"))
    assert state["videos"]["abcdefghijk"]["status"] == "summarized"
    assert state["videos"]["abcdefghijk"]["summary"]["why_it_matters"] == "Budgets move."
    digest = next((tmp_path / "digests").glob("*.md")).read_text(encoding="utf-8")
    assert "Inference prices are falling quickly" in digest


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
