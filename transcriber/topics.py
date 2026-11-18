"""Cluster episode points into themes and drop repeats from the last 7 days.

The digest is built from structured topic points (produced by the summarizer).
Matching is lexical so it can be tested without a model: same theme, same
claim, said on two shows, becomes one line that credits both. A point stored
in the covered-topics file during the last `window_days` is omitted. A new
number on an otherwise familiar point is kept and labeled as an update.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from transcriber.models import CoveredTopic, Source, Summary

_STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "with",
    "vs", "versus", "about", "is", "are", "was", "were", "be", "been",
    "this", "that", "it", "its", "at", "by", "from", "as", "into", "over",
    "than", "then", "just", "we", "you", "they", "their", "our", "your",
}
_WORD_RE = re.compile(r"[a-z0-9]+")
_NUMBER_RE = re.compile(
    r"(?:[$£€]\s*)?\d+(?:,\d{3})*(?:\.\d+)?(?:\s*(?:%|x|×|percent|billion|million|trillion|bn))?",
    re.IGNORECASE,
)


def extract_numbers(text: str) -> list[str]:
    found = []
    for match in _NUMBER_RE.findall(text or ""):
        cleaned = " ".join(match.split())
        if cleaned and cleaned not in found:
            found.append(cleaned)
    return found


def normalize_number(value: str) -> str:
    text = value.lower().replace(",", "").replace("×", "x")
    text = text.replace("percent", "%").replace("billion", "bn").replace("million", "m")
    text = text.replace("trillion", "tn").replace("$ ", "$").replace("£ ", "£")
    text = re.sub(r"\s+", "", text)
    return text


def _tokens(text: str) -> set[str]:
    words = []
    for raw in _WORD_RE.findall((text or "").lower()):
        if raw in _STOPWORDS or len(raw) < 2:
            continue
        if raw.endswith("s") and not raw.endswith("ss") and len(raw) > 4 and not raw.isdigit():
            raw = raw[:-1]
        words.append(raw)
    return set(words)


def _content_tokens(text: str) -> set[str]:
    return {token for token in _tokens(text) if not token.isdigit()}


def jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def topics_match(left: str, right: str) -> bool:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return False
    if left_tokens == right_tokens:
        return True
    smaller, larger = (
        (left_tokens, right_tokens)
        if len(left_tokens) <= len(right_tokens)
        else (right_tokens, left_tokens)
    )
    if len(smaller) >= 2 and smaller <= larger:
        return True
    return jaccard(left_tokens, right_tokens) >= 0.6


def points_match(left: str, right: str) -> bool:
    """True when two sentences are the same claim, even if the figure changed."""
    if jaccard(_tokens(left), _tokens(right)) >= 0.58:
        return True
    left_content = _content_tokens(left)
    right_content = _content_tokens(right)
    if not left_content or not right_content:
        return False
    return jaccard(left_content, right_content) >= 0.75


def _number_set(text: str, explicit: list[str] | None) -> set[str]:
    values = list(explicit or []) + extract_numbers(text)
    return {normalize_number(value) for value in values if normalize_number(value)}


@dataclass
class EpisodeInput:
    video_id: str
    title: str
    url: str
    channel: str
    published: datetime | None
    summary: Summary


@dataclass
class ThemeBullet:
    line: str
    topic: str
    key_point: str
    numbers: list[str]
    sources: list[Source]


@dataclass
class Theme:
    topic: str
    bullets: list[ThemeBullet] = field(default_factory=list)


@dataclass
class EpisodeLine:
    video_id: str
    title: str
    url: str
    channel: str
    guests: list[str]
    unique: str


@dataclass
class DigestPlan:
    themes: list[Theme]
    episodes: list[EpisodeLine]
    covered: list[CoveredTopic]


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def within_window(item_date: str, as_of: date, window_days: int) -> bool:
    parsed = _parse_date(item_date)
    if parsed is None:
        return False
    age = (as_of - parsed).days
    return 0 <= age <= window_days


def _known_numbers(
    topic: str,
    text: str,
    covered: list[CoveredTopic],
    as_of: date,
    window_days: int,
) -> tuple[set[str], list[str]] | None:
    """Normalized numbers already stored for a matching point, plus their display form.

    Returns None when the point was not covered in the window.
    """
    known: set[str] = set()
    raw: list[str] = []
    matched = False
    for item in covered:
        if not within_window(item.date, as_of, window_days):
            continue
        if not topics_match(topic, item.topic):
            continue
        for key_point in item.key_points:
            if not points_match(text, key_point):
                continue
            matched = True
            for number in list(item.numbers) + extract_numbers(key_point):
                normalized = normalize_number(number)
                if not normalized or normalized in known:
                    continue
                known.add(normalized)
                raw.append(number)
    if not matched:
        return None
    return known, raw


def _heading(topic: str) -> str:
    text = " ".join(topic.split())
    if not text:
        return "Other"
    return text[0].upper() + text[1:]


def _strip_numbers(text: str) -> str:
    stripped = _NUMBER_RE.sub("", text)
    stripped = re.sub(r"\s+", " ", stripped)
    stripped = re.sub(r"\s+([,.;:])", r"\1", stripped)
    stripped = stripped.strip(" -—,;:")
    return re.sub(r"\s+", " ", stripped).strip()


@dataclass
class _Item:
    topic: str
    text: str
    source: Source
    numbers: list[str]


@dataclass
class _Group:
    topic: str
    text: str
    items: list[_Item]


def _cluster(items: list[_Item]) -> list[list[_Item]]:
    clusters: list[list[_Item]] = []
    for item in items:
        placed = False
        for cluster in clusters:
            if topics_match(item.topic, cluster[0].topic):
                cluster.append(item)
                placed = True
                break
        if not placed:
            clusters.append([item])
    return clusters


def _group_points(items: list[_Item]) -> list[_Group]:
    groups: list[_Group] = []
    for item in items:
        placed = False
        for group in groups:
            if points_match(item.text, group.text):
                group.items.append(item)
                if len(item.text) > len(group.text):
                    group.text = item.text
                placed = True
                break
        if not placed:
            groups.append(_Group(topic=item.topic, text=item.text, items=[item]))
    return groups


def _display_numbers(items: list[_Item]) -> list[str]:
    seen = set()
    ordered = []
    for item in items:
        for number in item.numbers or extract_numbers(item.text):
            key = normalize_number(number)
            if key and key not in seen:
                seen.add(key)
                ordered.append(number.strip())
    return ordered


def _render_group_line(
    group: _Group,
    *,
    update_numbers: set[str] | None,
    previous: list[str] | None,
) -> str:
    sources = []
    seen_ids = set()
    for item in group.items:
        if item.source.video_id in seen_ids:
            continue
        seen_ids.add(item.source.video_id)
        sources.append(item)
    number_sets = []
    for item in sources:
        nums = item.numbers or extract_numbers(item.text)
        number_sets.append(tuple(nums))
    distinct = {tuple(normalize_number(n) for n in nums) for nums in number_sets}
    channels = [item.source.channel for item in sources]
    if update_numbers is not None:
        fresh = [number for number in _display_numbers(sources) if normalize_number(number) in update_numbers]
        previous_display = [
            number for number in (previous or []) if normalize_number(number) not in update_numbers
        ]
        credit = ", ".join(dict.fromkeys(channels))
        body = group.text.strip()
        if not body.lower().startswith("update:"):
            body = f"Update: {body}"
        if previous_display:
            pretty = ", ".join(previous_display)
            if pretty not in body:
                body = f"{body} (previously {pretty})"
        if fresh and not any(number in body for number in fresh):
            body = f"{body} ({', '.join(fresh)})"
        return f"{body} — {credit}" if credit else body
    if len(distinct) > 1 and any(number_sets):
        parts = []
        for item, nums in zip(sources, number_sets):
            if nums:
                parts.append(f"{item.source.channel} ({', '.join(nums)})")
            else:
                parts.append(item.source.channel)
        base = _strip_numbers(group.text) or group.text.strip()
        return f"{base} — {'; '.join(parts)}"
    credit = ", ".join(dict.fromkeys(channels))
    text = group.text.strip()
    return f"{text} — {credit}" if credit else text


def plan_digest(
    episodes: list[EpisodeInput],
    covered: list[CoveredTopic],
    as_of: date,
    window_days: int = 7,
) -> DigestPlan:
    items: list[_Item] = []
    for episode in episodes:
        source = Source(
            channel=episode.channel,
            video_id=episode.video_id,
            title=episode.title,
            url=episode.url,
        )
        for topic in episode.summary.topics:
            for point in topic.points:
                text = point.text.strip()
                if not text:
                    continue
                numbers = point.numbers or extract_numbers(text)
                items.append(_Item(topic.topic.strip() or "Other", text, source, numbers))

    themes: list[Theme] = []
    new_covered: list[CoveredTopic] = []
    published_points: list[tuple[str, str]] = []

    for cluster in _cluster(items):
        label = min((item.topic for item in cluster), key=lambda value: (len(_tokens(value)), len(value)))
        bullets: list[ThemeBullet] = []
        for group in _group_points(cluster):
            numbers = _display_numbers(group.items)
            normalized = _number_set(group.text, numbers)
            known_info = _known_numbers(label, group.text, covered, as_of, window_days)
            update_numbers = None
            previous = None
            if known_info is not None:
                known, previous = known_info
                fresh = normalized - known
                if not fresh:
                    continue
                update_numbers = fresh
            line = _render_group_line(group, update_numbers=update_numbers, previous=previous)
            sources = []
            seen_ids = set()
            for item in group.items:
                if item.source.video_id in seen_ids:
                    continue
                seen_ids.add(item.source.video_id)
                sources.append(item.source)
            stored_numbers = list(numbers)
            if previous:
                for number in previous:
                    if normalize_number(number) not in {normalize_number(value) for value in stored_numbers}:
                        stored_numbers.append(number)
            bullet = ThemeBullet(
                line=line,
                topic=label,
                key_point=group.text.strip(),
                numbers=stored_numbers,
                sources=sources,
            )
            bullets.append(bullet)
            published_points.append((label, group.text.strip()))
        if not bullets:
            continue
        themes.append(Theme(topic=_heading(label), bullets=bullets))
        new_covered.append(
            CoveredTopic(
                topic=label,
                key_points=[bullet.key_point for bullet in bullets],
                date=as_of.isoformat(),
                sources=_unique_sources(bullet.sources for bullet in bullets),
                numbers=_unique_strs(number for bullet in bullets for number in bullet.numbers),
            )
        )

    episode_lines = []
    for episode in episodes:
        unique = _filter_unique(
            episode.summary.unique,
            episode.summary.topics[0].topic if episode.summary.topics else episode.channel,
            published_points,
            covered,
            as_of,
            window_days,
        )
        episode_lines.append(
            EpisodeLine(
                video_id=episode.video_id,
                title=episode.title,
                url=episode.url,
                channel=episode.channel,
                guests=list(episode.summary.guests),
                unique=unique,
            )
        )
    return DigestPlan(themes=themes, episodes=episode_lines, covered=new_covered)


def _unique_sources(groups) -> list[Source]:
    sources = []
    seen = set()
    for group in groups:
        for source in group:
            if source.video_id in seen:
                continue
            seen.add(source.video_id)
            sources.append(source)
    return sources


def _unique_strs(values) -> list[str]:
    result = []
    seen = set()
    for value in values:
        key = normalize_number(value) or value
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _filter_unique(
    unique: str,
    topic_hint: str,
    published_points: list[tuple[str, str]],
    covered: list[CoveredTopic],
    as_of: date,
    window_days: int,
) -> str:
    text = " ".join((unique or "").split())
    if not text:
        return ""
    for _topic, point in published_points:
        if points_match(text, point):
            return ""
    known_info = _known_numbers(topic_hint, text, covered, as_of, window_days)
    if known_info is None:
        # Also compare against every recent key point, in case the unique line
        # restates a theme under a different label.
        for item in covered:
            if not within_window(item.date, as_of, window_days):
                continue
            if any(points_match(text, key_point) for key_point in item.key_points):
                fresh = _number_set(text, []) - (
                    {normalize_number(number) for number in item.numbers} | _number_set(" ".join(item.key_points), [])
                )
                if not fresh:
                    return ""
                return f"Update: {text}"
        return text
    known, _previous = known_info
    fresh = _number_set(text, []) - known
    if not fresh:
        return ""
    return f"Update: {text}"


def load_covered(path: Path) -> list[CoveredTopic]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [CoveredTopic.from_dict(item) for item in data.get("topics") or [] if isinstance(item, dict)]


def save_covered(path: Path, topics: list[CoveredTopic]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "topics": [topic.to_dict() for topic in topics]}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def extend_covered(
    existing: list[CoveredTopic],
    new_items: list[CoveredTopic],
    as_of: date,
    retention_days: int = 30,
) -> list[CoveredTopic]:
    combined = list(existing) + list(new_items)
    kept = []
    for item in combined:
        parsed = _parse_date(item.date)
        if parsed is None:
            continue
        age = (as_of - parsed).days
        if age <= retention_days:
            kept.append(item)
    return kept
