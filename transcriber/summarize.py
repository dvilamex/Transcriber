"""Map-reduce summarization. Providers are selected with environment variables.

LLM_PROVIDER=openai|anthropic, LLM_MODEL overrides the default model.
If neither OPENAI_API_KEY nor ANTHROPIC_API_KEY is set, the caller skips
summarization and still writes the transcript.
"""

from __future__ import annotations

import json
import re
from typing import Protocol

from transcriber.chunking import Chunk, chunk_segments, format_timestamp
from transcriber.config import RuntimeEnv
from transcriber.models import Segment, Summary

SYSTEM = (
    "You summarize VC, tech, and AI podcast transcripts for a CTO. "
    "Use only facts present in the transcript or notes you are given. "
    "Do not invent numbers, quotes, people, companies, or events. "
    "Keep names spelled as in the source. "
    "Timestamps must be copied from markers like [HH:MM:SS]. Use an empty string when there is no marker. "
    "Topic labels are short and reusable across different shows, for example "
    "'inference costs', 'AI capex', or 'open source models'. Do not use the episode title as a topic. "
    "Respond with a single JSON object and no other text."
)

_FINAL_SHAPE = """{
  "tldr": "one paragraph, 3 to 5 sentences",
  "takeaways": ["5 to 8 bullets"],
  "claims": [{"timestamp": "HH:MM:SS", "text": "specific claim or number"}],
  "entities": [{"name": "person or company", "context": "how they came up"}],
  "guests": ["guest names actually on this episode"],
  "why_it_matters": "one sentence",
  "unique": "one sentence that is specific to this episode: a guest anecdote or a figure only they cited. Do not restate the general theme.",
  "topics": [
    {"topic": "reusable label", "points": [{"text": "one atomic claim", "numbers": ["optional figure"]}]}
  ]
}"""


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class SummaryError(RuntimeError):
    pass


class OpenAIClient:
    def __init__(self, model: str):
        from openai import OpenAI

        self._client = OpenAI()
        self._model = model

    def complete(self, system: str, user: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            temperature=0.2,
            max_tokens=2500,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""


class AnthropicClient:
    def __init__(self, model: str):
        from anthropic import Anthropic

        self._client = Anthropic()
        self._model = model

    def complete(self, system: str, user: str) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=2500,
            temperature=0.2,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        parts = []
        for block in response.content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        return "\n".join(parts)


def build_llm(env: RuntimeEnv) -> LLMClient | None:
    provider = env.llm_provider.lower().strip()
    if not provider:
        if env.openai_api_key:
            provider = "openai"
        elif env.anthropic_api_key:
            provider = "anthropic"
        else:
            return None
    if provider == "openai":
        if not env.openai_api_key:
            raise SummaryError("LLM_PROVIDER=openai but OPENAI_API_KEY is unset")
        return OpenAIClient(env.llm_model or "gpt-4o-mini")
    if provider == "anthropic":
        if not env.anthropic_api_key:
            raise SummaryError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is unset")
        return AnthropicClient(env.llm_model or "claude-3-5-haiku-latest")
    raise SummaryError("LLM_PROVIDER must be 'openai' or 'anthropic'")


def parse_json_object(text: str) -> dict:
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```(?:json)?\s*", "", body)
        body = re.sub(r"\s*```$", "", body)
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        start = body.find("{")
        end = body.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(body[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("model output was not a JSON object")
    return value


def _ask(client: LLMClient, user: str) -> dict:
    try:
        return parse_json_object(client.complete(SYSTEM, user))
    except Exception as exc:
        raise SummaryError(f"could not parse summary JSON: {exc}") from exc


def _map_prompt(title: str, channel: str, chunk: Chunk, total: int) -> str:
    return (
        f"Video: {title}\nChannel: {channel}\n"
        f"Chunk {chunk.index + 1} of {total} "
        f"({format_timestamp(chunk.start)}–{format_timestamp(chunk.end)})\n\n"
        f"Transcript:\n{chunk.text}\n\n"
        "Return JSON with atomic notes from this chunk only:\n"
        '{"topics":[{"topic":"reusable label","points":[{"text":"one claim","numbers":[]}]}],'
        '"claims":[{"timestamp":"HH:MM:SS","text":""}],'
        '"entities":[{"name":"","context":""}],'
        '"guests":[]}'
    )


def _final_prompt(title: str, channel: str, body: str, kind: str) -> str:
    return (
        f"Video: {title}\nChannel: {channel}\n\n"
        f"{kind}:\n{body}\n\n"
        "Write the digest fields for this one episode. Merge duplicate claims. "
        "Do not add facts that are not in the source. "
        f"Return JSON with this shape:\n{_FINAL_SHAPE}"
    )


def _notes_too_long(notes: list[str], limit: int = 48000) -> bool:
    return sum(len(note) for note in notes) > limit


def _reduce_notes(client: LLMClient, title: str, channel: str, notes: list[str]) -> dict:
    if _notes_too_long(notes):
        folded = []
        batch: list[str] = []
        size = 0
        for note in notes:
            if batch and size + len(note) > 24000:
                folded.append(json.dumps(_ask(client, _final_prompt(title, channel, "\n".join(batch), "Notes")), ensure_ascii=False))
                batch = []
                size = 0
            batch.append(note)
            size += len(note)
        if batch:
            folded.append(json.dumps(_ask(client, _final_prompt(title, channel, "\n".join(batch), "Notes")), ensure_ascii=False))
        notes = folded
    return _ask(client, _final_prompt(title, channel, "\n\n".join(notes), "Notes extracted in order"))


def summarize_transcript(
    segments: list[Segment],
    *,
    title: str,
    channel: str,
    client: LLMClient,
    chunk_chars: int = 12000,
    chunk_overlap: int = 500,
) -> Summary:
    chunks = chunk_segments(segments, max_chars=chunk_chars, overlap_chars=chunk_overlap)
    if not chunks:
        raise SummaryError("transcript was empty")
    if len(chunks) == 1:
        data = _ask(client, _final_prompt(title, channel, chunks[0].text, "Transcript"))
    else:
        notes = []
        for chunk in chunks:
            note = _ask(client, _map_prompt(title, channel, chunk, len(chunks)))
            notes.append(f"Chunk {chunk.index + 1}:\n" + json.dumps(note, ensure_ascii=False))
        data = _reduce_notes(client, title, channel, notes)
    return Summary.from_dict(data)
