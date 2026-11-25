"""Command line: init, run, and video."""

from __future__ import annotations

import argparse
import logging

from transcriber.config import ConfigError, load_config, load_runtime_env
from transcriber.lock import LockError
from transcriber.pipeline import init_channels, process_video_url, run_pipeline
from transcriber.summarize import SummaryError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="transcriber",
        description="Watch YouTube channels, summarize new videos, and write a theme-based digest.",
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser(
        "init",
        help="Mark videos already on each feed as seen, except those from the last N hours",
    )
    init.add_argument("--hours", type=float, default=None, help="Lookback window to leave unseen")

    run = sub.add_parser("run", help="Fetch new videos, summarize them, and update today's digest")
    run.add_argument("--since", type=float, default=None, help="Only videos published within the last N hours")
    run.add_argument("--limit", type=int, default=None, help="Maximum videos to process")
    run.add_argument("--no-summarize", action="store_true", help="Skip the LLM summary")

    video = sub.add_parser("video", help="Summarize one YouTube URL or video id and add it to today's digest")
    video.add_argument("url")
    video.add_argument("--no-summarize", action="store_true", help="Skip the LLM summary")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    log = logging.getLogger("transcriber")
    try:
        settings = load_config(args.config)
        env = load_runtime_env()
        if args.command == "init":
            return init_channels(settings, args.hours)
        if args.command == "run":
            return run_pipeline(
                settings,
                env,
                since_hours=args.since,
                limit=args.limit,
                summarize=not args.no_summarize,
            )
        return process_video_url(
            settings,
            env,
            args.url,
            summarize=not args.no_summarize,
        )
    except (ConfigError, SummaryError, LockError, FileNotFoundError, ValueError) as exc:
        log.error("%s", exc)
        return 2
