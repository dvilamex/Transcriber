"""Shared data types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Segment:
    start: float
    duration: float
    text: str


@dataclass
class Video:
    video_id: str
    title: str
    channel_name: str
    channel_id: str
    handle: str
    url: str
    published: datetime | None
    is_short: bool = False


@dataclass
class Claim:
    timestamp: str
    text: str


@dataclass
class Entity:
    name: str
    context: str


@dataclass
class TopicPoint:
    text: str
    numbers: list[str] = field(default_factory=list)


@dataclass
class Topic:
    topic: str
    points: list[TopicPoint] = field(default_factory=list)


@dataclass
class Summary:
    tldr: str = ""
    takeaways: list[str] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)
    guests: list[str] = field(default_factory=list)
    why_it_matters: str = ""
    unique: str = ""
    topics: list[Topic] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "tldr": self.tldr,
            "takeaways": list(self.takeaways),
            "claims": [{"timestamp": c.timestamp, "text": c.text} for c in self.claims],
            "entities": [{"name": e.name, "context": e.context} for e in self.entities],
            "guests": list(self.guests),
            "why_it_matters": self.why_it_matters,
            "unique": self.unique,
            "topics": [
                {
                    "topic": topic.topic,
                    "points": [{"text": p.text, "numbers": list(p.numbers)} for p in topic.points],
                }
                for topic in self.topics
            ],
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "Summary":
        data = data or {}
        claims = []
        for item in data.get("claims") or []:
            if isinstance(item, str):
                claims.append(Claim("", item.strip()))
            elif isinstance(item, dict) and str(item.get("text") or "").strip():
                claims.append(
                    Claim(str(item.get("timestamp") or "").strip(), str(item["text"]).strip())
                )
        entities = []
        for item in data.get("entities") or []:
            if isinstance(item, str) and item.strip():
                entities.append(Entity(item.strip(), ""))
            elif isinstance(item, dict) and str(item.get("name") or "").strip():
                entities.append(
                    Entity(str(item["name"]).strip(), str(item.get("context") or "").strip())
                )
        topics = []
        for item in data.get("topics") or []:
            if isinstance(item, str) and item.strip():
                topics.append(Topic(item.strip(), []))
                continue
            if not isinstance(item, dict):
                continue
            label = str(item.get("topic") or "").strip()
            if not label:
                continue
            points = []
            for point in item.get("points") or []:
                if isinstance(point, str) and point.strip():
                    points.append(TopicPoint(point.strip(), []))
                elif isinstance(point, dict) and str(point.get("text") or "").strip():
                    numbers = [str(n).strip() for n in point.get("numbers") or [] if str(n).strip()]
                    points.append(TopicPoint(str(point["text"]).strip(), numbers))
            topics.append(Topic(label, points))
        guests = []
        seen = set()
        for guest in data.get("guests") or []:
            name = str(guest).strip()
            key = name.casefold()
            if name and key not in seen:
                seen.add(key)
                guests.append(name)
        takeaways = [str(x).strip() for x in data.get("takeaways") or [] if str(x).strip()]
        return cls(
            tldr=str(data.get("tldr") or "").strip(),
            takeaways=takeaways[:8],
            claims=claims[:8],
            entities=entities[:20],
            guests=guests[:12],
            why_it_matters=str(data.get("why_it_matters") or "").strip(),
            unique=str(data.get("unique") or "").strip(),
            topics=topics[:8],
        )


@dataclass
class Source:
    channel: str
    video_id: str
    title: str
    url: str

    def to_dict(self) -> dict:
        return {
            "channel": self.channel,
            "video_id": self.video_id,
            "title": self.title,
            "url": self.url,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Source":
        return cls(
            channel=str(data.get("channel") or ""),
            video_id=str(data.get("video_id") or ""),
            title=str(data.get("title") or ""),
            url=str(data.get("url") or ""),
        )


@dataclass
class CoveredTopic:
    topic: str
    key_points: list[str]
    date: str
    sources: list[Source]
    numbers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "key_points": list(self.key_points),
            "date": self.date,
            "sources": [source.to_dict() for source in self.sources],
            "numbers": list(self.numbers),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CoveredTopic":
        return cls(
            topic=str(data.get("topic") or ""),
            key_points=[str(p) for p in data.get("key_points") or [] if str(p).strip()],
            date=str(data.get("date") or ""),
            sources=[Source.from_dict(s) for s in data.get("sources") or [] if isinstance(s, dict)],
            numbers=[str(n) for n in data.get("numbers") or [] if str(n).strip()],
        )


@dataclass
class TranscriptResult:
    segments: list[Segment]
    language_code: str
    source: str
    duration_seconds: int | None = None
