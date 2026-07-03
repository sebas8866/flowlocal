"""Dictation history store: JSON at %APPDATA%\\FlowLocal\\history.json.

Local-only, capped at the newest _MAX_ENTRIES entries. Pure stdlib module
(json, os, threading) — safe to import anywhere. Thread-safe via a single
module-level lock, matching the atomic-write pattern used by config.py.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_MAX_ENTRIES = 500

_lock = threading.Lock()


def _app_data_dir() -> str:
    """Return %APPDATA%\\FlowLocal, creating it if necessary."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "FlowLocal")
    os.makedirs(path, exist_ok=True)
    return path


def history_path() -> str:
    return os.path.join(_app_data_dir(), "history.json")


def _word_count(text: str) -> int:
    return len(text.split())


def _load_raw() -> List[Dict]:
    """Read the history file, tolerating a missing or corrupt file. Never
    raises. Caller must hold `_lock`.
    """
    path = history_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("Failed to read history (%s); starting fresh", exc)
        return []

    if not isinstance(data, list):
        return []

    entries = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if "ts" not in item or "text" not in item:
            continue
        entries.append(item)
    return entries


def _write_raw(entries: List[Dict]) -> None:
    """Atomically write entries to disk (temp file + os.replace). Caller
    must hold `_lock`.
    """
    path = history_path()
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, path)
    except OSError as exc:
        logger.error("Failed to save history: %s", exc)
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


def add(text: str, seconds: float = 0.0, ts: Optional[float] = None) -> Dict:
    """Record a new dictation entry. Returns the stored entry dict.

    `ts` defaults to time.time() but can be injected explicitly (tests use
    this to avoid real-clock flakiness). `seconds` is the audio duration;
    `wpm` is computed as words / (seconds / 60), guarded against divide by
    zero (0.0 when seconds <= 0).
    """
    if ts is None:
        ts = time.time()

    words = _word_count(text)
    wpm = (words / (seconds / 60.0)) if seconds and seconds > 0 else 0.0

    entry = {
        "ts": float(ts),
        "text": text,
        "words": words,
        "seconds": float(seconds or 0.0),
        "wpm": wpm,
    }

    with _lock:
        entries = _load_raw()
        entries.append(entry)
        # Newest last on disk during accumulation; cap by dropping the
        # oldest entries once over the limit.
        if len(entries) > _MAX_ENTRIES:
            entries = entries[-_MAX_ENTRIES:]
        _write_raw(entries)

    return entry


def all() -> List[Dict]:
    """Return all stored entries, newest first."""
    with _lock:
        entries = _load_raw()
    return sorted(entries, key=lambda e: e.get("ts", 0.0), reverse=True)


def clear() -> None:
    """Delete all stored history."""
    with _lock:
        _write_raw([])


def _local_midnight_ts(now: float) -> float:
    """Return the epoch timestamp for local midnight of the day containing
    `now`.
    """
    dt = datetime.fromtimestamp(now)
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.timestamp()


def stats(now: Optional[float] = None) -> Dict:
    """Compute aggregate stats from stored history.

    `now` defaults to time.time() but can be injected explicitly for
    deterministic tests. Returns a dict with:
        words_today: int
        total_words: int
        avg_wpm: float
        streak_days: int  # consecutive days with >=1 dictation, ending today
    """
    if now is None:
        now = time.time()

    entries = all()

    total_words = sum(e.get("words", 0) for e in entries)

    midnight_today = _local_midnight_ts(now)
    words_today = sum(
        e.get("words", 0) for e in entries if e.get("ts", 0.0) >= midnight_today
    )

    wpm_values = [e.get("wpm", 0.0) for e in entries if e.get("wpm", 0.0) > 0]
    avg_wpm = (sum(wpm_values) / len(wpm_values)) if wpm_values else 0.0

    streak_days = _compute_streak(entries, now)

    return {
        "words_today": words_today,
        "total_words": total_words,
        "avg_wpm": avg_wpm,
        "streak_days": streak_days,
    }


def _compute_streak(entries: List[Dict], now: float) -> int:
    """Count consecutive local-calendar days with at least one dictation,
    walking backwards from today. A day with zero dictations breaks the
    streak. If today has no dictations yet, the streak is still computed
    from yesterday backwards (today doesn't count against it until it
    ends without a dictation).
    """
    if not entries:
        return 0

    # Distinct local-calendar day ordinals that have >=1 entry.
    days_with_entries = set()
    for e in entries:
        ts = e.get("ts")
        if ts is None:
            continue
        days_with_entries.add(datetime.fromtimestamp(ts).date().toordinal())

    today_ordinal = datetime.fromtimestamp(now).date().toordinal()

    streak = 0
    day = today_ordinal
    if day not in days_with_entries:
        # No dictation today yet: start checking from yesterday so a
        # still-open streak isn't reported as broken mid-day.
        day -= 1

    while day in days_with_entries:
        streak += 1
        day -= 1

    return streak
