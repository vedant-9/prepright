"""Tiny disk cache — JSON files under data/cache/.

WHY DISK, NOT MEMORY: the dev server restarts on every file save (--reload);
an in-memory cache would die with each reload. Disk survives.
WHY NOT REDIS: a POC on a laptop gains nothing from another moving part.
This module is the swap-in point if/when we outgrow it.

Used first by quiz generation (P5): a (chapter, difficulty, style) quiz
request is deterministic-ish, so caching kills repeat API calls —
our main defense against free-tier quota.
"""

import hashlib
import json
import time
from pathlib import Path
from typing import Any

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def make_key(*parts: Any) -> str:
    """Stable cache key from arbitrary parts. Order matters, as it should."""
    raw = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def get(key: str, ttl_seconds: float | None = None) -> Any | None:
    """Return cached value or None. ttl=None means cache forever."""
    path = _cache_path(key)
    if not path.exists():
        return None
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None  # corrupt entry = miss; never crash the request path on cache
    if ttl_seconds is not None and time.time() - blob["saved_at"] > ttl_seconds:
        return None
    return blob["value"]


def put(key: str, value: Any) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(key).write_text(
        json.dumps({"saved_at": time.time(), "value": value}, default=str),
        encoding="utf-8",
    )
