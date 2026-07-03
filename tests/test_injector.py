"""Unit tests for flowlocal.injector's paste-keystroke sleep budget (FIX 3).

pynput is a real dependency already installed in .venv (see hotkey.py /
test_event_filter_dispatch.py), so we monkeypatch pynput.keyboard.Controller
and time.sleep rather than mocking pynput away entirely.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flowlocal import injector  # noqa: E402


class _FakeController:
    def __init__(self):
        self.calls = []

    def press(self, key):
        self.calls.append(("press", key))

    def release(self, key):
        self.calls.append(("release", key))


class SendPasteSleepBudgetTest(unittest.TestCase):
    def test_total_sleep_is_at_most_65ms(self):
        sleeps = []

        with mock.patch("time.sleep", side_effect=lambda s: sleeps.append(s)), \
             mock.patch("pynput.keyboard.Controller", _FakeController):
            injector._send_paste()

        total_ms = sum(sleeps) * 1000
        self.assertLessEqual(total_ms, 65.0)

    def test_no_leading_sleep_before_first_keypress(self):
        """Clipboard is already set synchronously before _send_paste() runs,
        so the first action must be the ctrl press, not a sleep."""
        events = []

        def fake_sleep(s):
            events.append(("sleep", s))

        class _RecordingController(_FakeController):
            def press(self, key):
                events.append(("press", key))
                super().press(key)

            def release(self, key):
                events.append(("release", key))
                super().release(key)

        with mock.patch("time.sleep", side_effect=fake_sleep), \
             mock.patch("pynput.keyboard.Controller", _RecordingController):
            injector._send_paste()

        self.assertEqual(events[0][0], "press")

    def test_presses_ctrl_then_v_and_releases_in_order(self):
        controller = _FakeController()
        with mock.patch("time.sleep"), \
             mock.patch("pynput.keyboard.Controller", return_value=controller):
            injector._send_paste()

        from pynput.keyboard import Key

        self.assertEqual(
            controller.calls,
            [
                ("press", Key.ctrl),
                ("press", "v"),
                ("release", "v"),
                ("release", Key.ctrl),
            ],
        )


if __name__ == "__main__":
    unittest.main()
