from datetime import datetime, timezone
from pathlib import Path

from transcriber.config import load_config
from transcriber.rss import parse_feed
from transcriber.urls import parse_video_id

FIXTURE = Path(__file__).parent / "fixtures" / "feed.xml"
ROOT = Path(__file__).resolve().parents[1]


def test_parse_feed_skips_shorts_and_keeps_watch_urls():
    videos = parse_feed(
        FIXTURE.read_text(encoding="utf-8"),
        channel_name="20VC",
        channel_id="UCf0PBRjhf0rF8fWBIxTuoWA",
        handle="@20VC",
    )
    assert [video.video_id for video in videos] == ["abcdefghijk", "shortvideo1", "zxywvutsrqp"]
    episode, short, older = videos
    assert episode.is_short is False
    assert episode.url == "https://www.youtube.com/watch?v=abcdefghijk"
    assert episode.title == "Why inference costs are falling"
    assert episode.published == datetime(2026, 11, 17, 15, 0, 40, tzinfo=timezone.utc)
    assert episode.channel_name == "20VC"
    assert short.is_short is True
    assert "/shorts/" in short.url
    assert older.is_short is False


def test_video_url_parser():
    assert parse_video_id("abcdefghijk") == "abcdefghijk"
    assert parse_video_id("https://www.youtube.com/watch?v=abcdefghijk&t=12s") == "abcdefghijk"
    assert parse_video_id("https://youtu.be/abcdefghijk") == "abcdefghijk"
    assert parse_video_id("https://www.youtube.com/shorts/shortvideo1") == "shortvideo1"
    try:
        parse_video_id("https://example.com/not-a-video")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_config_channel_ids_match_the_verified_feeds():
    settings = load_config(ROOT / "config.yaml")
    enabled = {channel.name: channel.channel_id for channel in settings.enabled_channels()}
    assert enabled == {
        "20VC": "UCf0PBRjhf0rF8fWBIxTuoWA",
        "Sequoia Capital": "UCWrF0oN6unbXrWsTN7RctTw",
        "TBPN": "UC-DRzaGnL_vtBUpCFH5M0tg",
        "All-In Podcast": "UCESLZhusAkFfsNsApnjF_Cg",
        "AI Engineer": "UCLKPca3kwwd-B59HNr-_lvA",
        "Dwarkesh Patel": "UCXl4i9dYBrFOabk0xGmbkRA",
    }
    disabled = {channel.handle: channel.channel_id for channel in settings.channels if not channel.enabled}
    assert disabled["@Bg2Pod"] == "UC-yRDvpR99LUc5l7i7jLzew"
    assert disabled["@ILTB_Podcast"] == "UCpQBb0fToph3jrDulwz1iUQ"
    assert settings.dedupe_window_days == 7
    assert settings.max_retry_runs == 8
    assert settings.max_retry_days == 7
