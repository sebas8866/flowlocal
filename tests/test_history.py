"""Unit tests for flowlocal.history.

Run with: py -3.11 -m unittest discover -s tests
or:       py -3.11 -m unittest tests.test_history

Redirects %APPDATA% to a temp directory per-test so these tests never touch
the real user history file.
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flowlocal import history


def _ts_for_days_ago(days_ago: int, hour: int = 12) -> float:
    """Return an epoch timestamp `days_ago` days before today, at `hour`
    local time (so it's unambiguously within that calendar day).
    """
    target_date = datetime.now().date() - timedelta(days=days_ago)
    dt = datetime.combine(target_date, datetime.min.time()).replace(hour=hour)
    return dt.timestamp()


class HistoryTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = self._tmpdir.name

    def tearDown(self):
        if self._orig_appdata is not None:
            os.environ["APPDATA"] = self._orig_appdata
        else:
            os.environ.pop("APPDATA", None)
        self._tmpdir.cleanup()


class TestAddAndAll(HistoryTestBase):
    def test_add_returns_entry_with_expected_fields(self):
        entry = history.add("hello world", seconds=6.0, ts=1000.0)
        self.assertEqual(entry["text"], "hello world")
        self.assertEqual(entry["words"], 2)
        self.assertEqual(entry["seconds"], 6.0)
        self.assertEqual(entry["ts"], 1000.0)
        self.assertAlmostEqual(entry["wpm"], 20.0)  # 2 words / (6s/60) = 20 wpm

    def test_wpm_zero_seconds_no_divide_by_zero(self):
        entry = history.add("hello", seconds=0.0, ts=1000.0)
        self.assertEqual(entry["wpm"], 0.0)

    def test_wpm_missing_seconds_defaults_zero(self):
        entry = history.add("hello", ts=1000.0)
        self.assertEqual(entry["wpm"], 0.0)

    def test_all_empty_initially(self):
        self.assertEqual(history.all(), [])

    def test_all_returns_newest_first(self):
        history.add("first", ts=100.0)
        history.add("second", ts=200.0)
        history.add("third", ts=300.0)
        texts = [e["text"] for e in history.all()]
        self.assertEqual(texts, ["third", "second", "first"])

    def test_persists_across_calls(self):
        history.add("persisted", ts=100.0)
        entries = history.all()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["text"], "persisted")


class TestCap(HistoryTestBase):
    def test_caps_at_max_entries(self):
        for i in range(510):
            history.add(f"entry {i}", ts=float(i))
        entries = history.all()
        self.assertEqual(len(entries), 500)
        # Newest 500 retained: entries 10..509
        oldest_kept = min(e["ts"] for e in entries)
        self.assertEqual(oldest_kept, 10.0)


class TestClear(HistoryTestBase):
    def test_clear_removes_all_entries(self):
        history.add("one", ts=1.0)
        history.add("two", ts=2.0)
        self.assertEqual(len(history.all()), 2)
        history.clear()
        self.assertEqual(history.all(), [])


class TestDelete(HistoryTestBase):
    def test_delete_removes_matching_entry(self):
        history.add("one", ts=1.0)
        history.add("two", ts=2.0)
        history.add("three", ts=3.0)
        result = history.delete(2.0)
        self.assertTrue(result)
        texts = [e["text"] for e in history.all()]
        self.assertEqual(sorted(texts), ["one", "three"])

    def test_delete_returns_false_when_not_found(self):
        history.add("one", ts=1.0)
        result = history.delete(999.0)
        self.assertFalse(result)
        self.assertEqual(len(history.all()), 1)

    def test_delete_on_empty_history_returns_false(self):
        self.assertFalse(history.delete(1.0))


class TestStats(HistoryTestBase):
    def test_stats_empty(self):
        s = history.stats(now=_ts_for_days_ago(0))
        self.assertEqual(s["words_today"], 0)
        self.assertEqual(s["total_words"], 0)
        self.assertEqual(s["avg_wpm"], 0.0)
        self.assertEqual(s["streak_days"], 0)

    def test_words_today_and_total(self):
        today_noon = _ts_for_days_ago(0, hour=12)
        yesterday_noon = _ts_for_days_ago(1, hour=12)

        history.add("one two three", ts=yesterday_noon, seconds=9.0)  # 3 words
        history.add("four five", ts=today_noon, seconds=6.0)  # 2 words

        s = history.stats(now=today_noon + 3600)
        self.assertEqual(s["total_words"], 5)
        self.assertEqual(s["words_today"], 2)

    def test_midnight_boundary_excludes_previous_day(self):
        yesterday_2359 = _ts_for_days_ago(1, hour=23)
        today_early = _ts_for_days_ago(0, hour=0)

        history.add("late last night", ts=yesterday_2359, seconds=9.0)
        s = history.stats(now=today_early + 60)
        self.assertEqual(s["words_today"], 0)
        self.assertEqual(s["total_words"], 3)

    def test_avg_wpm_ignores_zero_wpm_entries(self):
        ts = _ts_for_days_ago(0, hour=12)
        history.add("no duration text", ts=ts, seconds=0.0)  # wpm 0, excluded
        history.add("timed text here", ts=ts + 1, seconds=6.0)  # 3 words -> 30 wpm
        s = history.stats(now=ts + 3600)
        self.assertAlmostEqual(s["avg_wpm"], 30.0)

    def test_streak_consecutive_days_ending_today(self):
        day0 = _ts_for_days_ago(0, hour=10)
        day1 = _ts_for_days_ago(1, hour=10)
        day2 = _ts_for_days_ago(2, hour=10)

        history.add("day two", ts=day2)
        history.add("day one", ts=day1)
        history.add("day zero", ts=day0)

        s = history.stats(now=day0 + 3600)
        self.assertEqual(s["streak_days"], 3)

    def test_streak_broken_by_gap_day(self):
        day0 = _ts_for_days_ago(0, hour=10)
        day2 = _ts_for_days_ago(2, hour=10)  # gap at day1

        history.add("day two", ts=day2)
        history.add("day zero", ts=day0)

        s = history.stats(now=day0 + 3600)
        self.assertEqual(s["streak_days"], 1)

    def test_streak_continues_if_today_has_no_entry_yet(self):
        # Streak through yesterday should still count even if nothing has
        # been dictated yet today.
        day1 = _ts_for_days_ago(1, hour=10)
        day2 = _ts_for_days_ago(2, hour=10)

        history.add("day two", ts=day2)
        history.add("day one", ts=day1)

        now = _ts_for_days_ago(0, hour=8)  # "now" is today, before any entry
        s = history.stats(now=now)
        self.assertEqual(s["streak_days"], 2)

    def test_streak_zero_when_no_entry_today_or_yesterday(self):
        day5 = _ts_for_days_ago(5, hour=10)
        history.add("old", ts=day5)

        now = _ts_for_days_ago(0, hour=12)
        s = history.stats(now=now)
        self.assertEqual(s["streak_days"], 0)


if __name__ == "__main__":
    unittest.main()
