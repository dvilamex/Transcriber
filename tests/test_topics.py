from datetime import date, timedelta

from transcriber.digest import merge_digest, render_digest
from transcriber.models import Summary, Topic, TopicPoint
from transcriber.topics import (
    CoveredTopic,
    EpisodeInput,
    Source,
    extend_covered,
    load_covered,
    plan_digest,
    points_match,
    save_covered,
)

DAY = date(2026, 11, 18)


def episode(video_id, channel, topics, guests=None, unique="", title=None):
    return EpisodeInput(
        video_id=video_id,
        title=title or f"{channel} episode",
        url=f"https://www.youtube.com/watch?v={video_id}",
        channel=channel,
        published=None,
        summary=Summary(
            guests=guests or [],
            unique=unique,
            topics=[
                Topic(
                    topic,
                    [TopicPoint(text, list(numbers)) for text, numbers in points],
                )
                for topic, points in topics
            ],
        ),
    )


def bullet_text(plan):
    return [bullet.line for theme in plan.themes for bullet in theme.bullets]


def test_same_point_on_two_shows_is_stated_once_and_credited():
    plan = plan_digest(
        [
            episode(
                "abcdefghijk",
                "20VC",
                [("inference costs", [("Inference prices are falling quickly", [])])],
                unique="Harry asked about a seed round that only 20VC covered.",
            ),
            episode(
                "bcdefghijkl",
                "TBPN",
                [("falling inference costs", [("Inference prices are falling fast", [])])],
                guests=["John Collison"],
                unique="A guest demoed a pricing calculator.",
            ),
        ],
        [],
        DAY,
        7,
    )
    assert len(plan.themes) == 1
    assert plan.themes[0].topic == "Inference costs"
    assert len(plan.themes[0].bullets) == 1
    line = plan.themes[0].bullets[0].line
    assert "20VC" in line and "TBPN" in line
    assert line.lower().count("inference prices are falling") == 1
    rendered = render_digest(DAY, plan)
    assert rendered.count("Inference prices are falling") == 1
    assert "John Collison" in rendered
    assert "pricing calculator" in rendered
    assert "seed round" in rendered
    # The shared theme is not repeated on the episode lines.
    episode_section = rendered.split("## Episodes", 1)[1]
    assert "Inference prices are falling" not in episode_section


def test_points_covered_in_the_last_seven_days_are_skipped_until_a_new_number():
    covered = [
        CoveredTopic(
            topic="OpenAI revenue",
            key_points=["OpenAI revenue is running at $10 billion"],
            date=(DAY - timedelta(days=3)).isoformat(),
            sources=[Source("20VC", "abcdefghijk", "old", "https://www.youtube.com/watch?v=abcdefghijk")],
            numbers=["$10 billion"],
        )
    ]
    repeat = plan_digest(
        [
            episode(
                "bcdefghijkl",
                "All-In Podcast",
                [("OpenAI revenue", [("OpenAI revenue is running at $10 billion", ["$10 billion"])])],
            )
        ],
        covered,
        DAY,
        7,
    )
    assert repeat.themes == []

    outside = plan_digest(
        [
            episode(
                "bcdefghijkl",
                "All-In Podcast",
                [("OpenAI revenue", [("OpenAI revenue is running at $10 billion", ["$10 billion"])])],
            )
        ],
        [
            CoveredTopic(
                topic="OpenAI revenue",
                key_points=["OpenAI revenue is running at $10 billion"],
                date=(DAY - timedelta(days=8)).isoformat(),
                sources=[],
                numbers=["$10 billion"],
            )
        ],
        DAY,
        7,
    )
    assert outside.themes
    assert "$10 billion" in outside.themes[0].bullets[0].line

    updated = plan_digest(
        [
            episode(
                "cdefghijklm",
                "Dwarkesh Patel",
                [("OpenAI revenue", [("OpenAI revenue is running at $13 billion", ["$13 billion"])])],
                guests=["Paul Christiano"],
            )
        ],
        covered,
        DAY,
        7,
    )
    assert len(updated.themes) == 1
    line = updated.themes[0].bullets[0].line
    assert line.startswith("Update:")
    assert "$13 billion" in line
    assert "previously $10 billion" in line
    assert "Dwarkesh Patel" in line
    # The old sentence is not given its own bullet.
    assert len(updated.themes[0].bullets) == 1
    stored = updated.covered[0]
    assert any(points_match(point, "OpenAI revenue is running at $13 billion") for point in stored.key_points)


def test_a_later_repeat_of_either_number_stays_quiet_and_a_newer_figure_is_kept():
    first = plan_digest(
        [
            episode(
                "cdefghijklm",
                "Dwarkesh Patel",
                [("OpenAI revenue", [("OpenAI revenue is running at $13 billion", ["$13 billion"])])],
            )
        ],
        [
            CoveredTopic(
                topic="OpenAI revenue",
                key_points=["OpenAI revenue is running at $10 billion"],
                date=(DAY - timedelta(days=2)).isoformat(),
                sources=[],
                numbers=["$10 billion"],
            )
        ],
        DAY,
        7,
    )
    history = extend_covered(
        [
            CoveredTopic(
                topic="OpenAI revenue",
                key_points=["OpenAI revenue is running at $10 billion"],
                date=(DAY - timedelta(days=2)).isoformat(),
                sources=[],
                numbers=["$10 billion"],
            )
        ],
        first.covered,
        DAY,
    )
    repeat_old = plan_digest(
        [
            episode(
                "defghijklmn",
                "TBPN",
                [("OpenAI revenue", [("OpenAI revenue is running at $10 billion", ["$10 billion"])])],
            )
        ],
        history,
        DAY,
        7,
    )
    assert repeat_old.themes == []
    newer = plan_digest(
        [
            episode(
                "efghijklmno",
                "TBPN",
                [("OpenAI revenue", [("OpenAI revenue is running at $15 billion", ["$15 billion"])])],
            )
        ],
        history,
        DAY,
        7,
    )
    assert "$15 billion" in newer.themes[0].bullets[0].line
    assert newer.themes[0].bullets[0].line.startswith("Update:")


def test_different_views_stay_separate_under_one_theme():
    plan = plan_digest(
        [
            episode(
                "abcdefghijk",
                "20VC",
                [("AGI timelines", [("AGI arrives within two years", [])])],
            ),
            episode(
                "bcdefghijkl",
                "Dwarkesh Patel",
                [("AGI timelines", [("A government-level system is plausible within twelve months", [])])],
            ),
        ],
        [],
        DAY,
        7,
    )
    assert len(plan.themes) == 1
    lines = bullet_text(plan)
    assert len(lines) == 2
    assert any("two years" in line and "20VC" in line for line in lines)
    assert any("twelve months" in line and "Dwarkesh Patel" in line for line in lines)


def test_same_day_disagreement_on_a_number_is_one_line():
    plan = plan_digest(
        [
            episode(
                "abcdefghijk",
                "20VC",
                [("OpenAI revenue", [("OpenAI revenue is running at $10 billion", ["$10 billion"])])],
            ),
            episode(
                "bcdefghijkl",
                "All-In Podcast",
                [("OpenAI revenue", [("OpenAI revenue is running at $13 billion", ["$13 billion"])])],
            ),
        ],
        [],
        DAY,
        7,
    )
    assert len(plan.themes[0].bullets) == 1
    line = plan.themes[0].bullets[0].line
    assert "20VC ($10 billion)" in line
    assert "All-In Podcast ($13 billion)" in line
    assert line.count("OpenAI revenue is running at") == 1


def test_digest_merge_and_covered_store_roundtrip(tmp_path):
    first = plan_digest(
        [
            episode(
                "abcdefghijk",
                "20VC",
                [("inference costs", [("Inference prices are falling quickly", [])])],
                guests=["Jane Doe"],
                unique="Only 20VC toured the new cluster.",
            )
        ],
        [],
        DAY,
        7,
    )
    existing = render_digest(DAY, first)
    second = plan_digest(
        [
            episode(
                "bcdefghijkl",
                "Sequoia Capital",
                [("AI capex", [("Hyperscaler capex is still accelerating", [])])],
                guests=["Roelof Botha"],
            )
        ],
        first.covered,
        DAY,
        7,
    )
    merged = merge_digest(existing, DAY, second)
    assert merged.count("## Inference costs") == 1
    assert "## Ai capex" in merged or "## AI capex" in merged
    assert "abcdefghijk" in merged and "bcdefghijkl" in merged
    assert merged.count("Only 20VC toured") == 1
    path = tmp_path / "covered_topics.json"
    save_covered(path, extend_covered([], first.covered, DAY))
    loaded = load_covered(path)
    assert loaded[0].topic == "inference costs"
    assert loaded[0].date == DAY.isoformat()
    assert loaded[0].sources[0].channel == "20VC"


def test_old_covered_topics_are_pruned():
    old = CoveredTopic("old theme", ["a point"], (DAY - timedelta(days=40)).isoformat(), [], [])
    fresh = CoveredTopic("new theme", ["another point"], DAY.isoformat(), [], [])
    kept = extend_covered([old], [fresh], DAY, retention_days=30)
    assert [item.topic for item in kept] == ["new theme"]
