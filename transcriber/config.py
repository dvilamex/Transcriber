"""YAML config and process environment."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CHANNEL_ID_RE = re.compile(r"^UC[0-9A-Za-z_-]{22}$")


class ConfigError(ValueError):
    pass


@dataclass
class Channel:
    name: str
    channel_id: str
    enabled: bool
    handle: str = ""


@dataclass
class Settings:
    channels: list[Channel]
    digests_dir: Path
    state_file: Path
    covered_topics_file: Path
    lock_file: Path
    init_lookback_hours: float = 48
    max_retry_runs: int = 8
    max_retry_days: int = 7
    dedupe_window_days: int = 7
    chunk_chars: int = 12000
    chunk_overlap: int = 500
    config_path: Path | None = None

    def enabled_channels(self) -> list[Channel]:
        return [channel for channel in self.channels if channel.enabled]


@dataclass
class RuntimeEnv:
    llm_provider: str = ""
    llm_model: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    youtube_proxy: str = ""
    cookies_file: str = ""
    webshare_username: str = ""
    webshare_password: str = ""
    webshare_locations: list[str] = field(default_factory=list)
    supadata_api_key: str = ""
    supadata_mode: str = "native"

    def has_llm(self) -> bool:
        provider = self.llm_provider.lower().strip()
        if provider == "openai":
            return bool(self.openai_api_key)
        if provider == "anthropic":
            return bool(self.anthropic_api_key)
        if provider:
            return False
        return bool(self.openai_api_key or self.anthropic_api_key)


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return base / path


def load_config(path: str | Path) -> Settings:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Config not found: {config_path}")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a mapping")
    base = config_path.parent
    channels = []
    for index, item in enumerate(raw.get("channels") or []):
        if not isinstance(item, dict):
            raise ConfigError(f"channels[{index}] must be a mapping")
        name = str(item.get("name") or "").strip()
        channel_id = str(item.get("channel_id") or "").strip()
        if not name or not channel_id:
            raise ConfigError(f"channels[{index}] needs name and channel_id")
        if not CHANNEL_ID_RE.match(channel_id):
            raise ConfigError(f"{name} has an invalid channel_id: {channel_id}")
        channels.append(
            Channel(
                name=name,
                channel_id=channel_id,
                enabled=bool(item.get("enabled", False)),
                handle=str(item.get("handle") or "").strip(),
            )
        )
    if not channels:
        raise ConfigError("Config has no channels")
    return Settings(
        channels=channels,
        digests_dir=_resolve(base, str(raw.get("digests_dir") or "digests")),
        state_file=_resolve(base, str(raw.get("state_file") or "state/seen.json")),
        covered_topics_file=_resolve(
            base, str(raw.get("covered_topics_file") or "state/covered_topics.json")
        ),
        lock_file=_resolve(base, str(raw.get("lock_file") or "state/transcriber.lock")),
        init_lookback_hours=float(raw.get("init_lookback_hours") or 48),
        max_retry_runs=int(raw.get("max_retry_runs") or 8),
        max_retry_days=int(raw.get("max_retry_days") or 7),
        dedupe_window_days=int(raw.get("dedupe_window_days") or 7),
        chunk_chars=int(raw.get("chunk_chars") or 12000),
        chunk_overlap=int(raw.get("chunk_overlap") or 500),
        config_path=config_path,
    )


def load_runtime_env() -> RuntimeEnv:
    locations = [
        part.strip()
        for part in os.environ.get("WEBSHARE_FILTER_LOCATIONS", "").split(",")
        if part.strip()
    ]
    mode = os.environ.get("SUPADATA_MODE", "native").strip().lower() or "native"
    if mode not in {"native", "auto", "generate"}:
        mode = "native"
    return RuntimeEnv(
        llm_provider=os.environ.get("LLM_PROVIDER", "").strip(),
        llm_model=os.environ.get("LLM_MODEL", "").strip(),
        openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", "").strip(),
        youtube_proxy=os.environ.get("YOUTUBE_PROXY", "").strip(),
        cookies_file=os.environ.get("YOUTUBE_COOKIES_FILE", "").strip(),
        webshare_username=os.environ.get("WEBSHARE_PROXY_USERNAME", "").strip(),
        webshare_password=os.environ.get("WEBSHARE_PROXY_PASSWORD", "").strip(),
        webshare_locations=locations,
        supadata_api_key=os.environ.get("SUPADATA_API_KEY", "").strip(),
        supadata_mode=mode,
    )
