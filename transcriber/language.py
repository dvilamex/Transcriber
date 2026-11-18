"""English-only checks.

YouTube sometimes hands back an auto-dubbed track. Callers request an English
language code and then reject text that does not read as English.
"""

from __future__ import annotations

import re

_ENGLISH_WORDS = {
    "the", "and", "to", "of", "a", "in", "that", "is", "for", "it", "on",
    "with", "as", "this", "be", "are", "was", "you", "we", "i", "have",
    "from", "they", "but", "not", "our", "their", "about", "what", "which",
    "can", "or", "if", "so", "just", "there", "an", "will", "would", "this",
    "its", "it's", "we're", "they're", "don't", "doesn't", "been", "than",
    "then", "into", "your", "my", "at", "by", "because", "how", "when",
}

_WORD_RE = re.compile(r"[A-Za-z']+")


def is_english_code(code: str | None) -> bool:
    if not code:
        return False
    normalized = code.lower().replace("_", "-")
    return normalized == "en" or normalized.startswith("en-")


def english_rank(code: str) -> tuple[int, str]:
    """Lower is better. Prefer the original English audio track over dubs."""
    normalized = code.lower().replace("_", "-")
    if normalized in {"en-orig", "en-us-orig"}:
        rank = 0
    elif normalized == "en":
        rank = 1
    elif normalized in {"en-us", "en-gb"}:
        rank = 2
    else:
        rank = 3
    return (rank, normalized)


def _letters(text: str) -> list[str]:
    return [char for char in text if char.isalpha()]


def looks_english(text: str, *, min_words: int = 40, threshold: float = 0.08) -> bool:
    letters = _letters(text)
    if letters:
        non_latin = [char for char in letters if not ("A" <= char <= "Z" or "a" <= char <= "z")]
        if len(non_latin) / len(letters) > 0.25:
            return False
    words = [word.lower() for word in _WORD_RE.findall(text)]
    if len(words) < min_words:
        return False
    hits = sum(1 for word in words if word in _ENGLISH_WORDS)
    return hits / len(words) >= threshold


def accept_transcript(text: str, language_code: str | None) -> bool:
    """Accept English captions and reject auto-dubbed or unlabeled foreign text."""
    if language_code and not is_english_code(language_code):
        return False
    words = _WORD_RE.findall(text)
    if not words:
        return False
    # A handful of words is not enough to judge, so trust an explicit English code.
    if len(words) < 8:
        return bool(language_code) and is_english_code(language_code)
    return looks_english(text, min_words=min(40, len(words)))
