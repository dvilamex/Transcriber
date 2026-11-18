"""Exclusive lock so two runs cannot write state at the same time."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path


class LockError(RuntimeError):
    pass


class RunLock:
    def __init__(self, path: Path):
        self.path = path
        self._handle = None

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise LockError(
                f"Another transcriber process holds {self.path}. "
                "Wait for it to finish."
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._handle = handle
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None
