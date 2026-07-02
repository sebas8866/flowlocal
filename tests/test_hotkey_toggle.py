"""Unit tests for the toggle-mode press/release handshake in
flowlocal.hotkey.TriggerManager.

These tests drive TriggerManager._fire_press("toggle") directly rather than
going through real pynput listeners, per the harness note in
flowlocal/hotkey.py ("pynput is imported lazily inside methods so this
module can be imported without it installed"). No real keyboard/mouse
listener is started.

Run with: py -3.11 -m unittest discover -s tests
or:       py -3.11 -m unittest tests.test_hotkey_toggle
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flowlocal.hotkey import TriggerManager


class TestToggleHandshake(unittest.TestCase):
    """The app is authoritative on whether a toggle-mode press is accepted.

    TriggerManager only flips _toggle_state to "recording" if on_press
    returns truthy; a False return (paused/busy) must leave the toggle
    state unchanged so the trigger stays armed for a real attempt.
    """

    def _make_manager(self, on_press=None, on_release=None):
        return TriggerManager(
            binding="key:f9",
            mode="toggle",
            on_press=on_press,
            on_release=on_release,
        )

    def test_rejected_press_does_not_flip_toggle_state(self):
        # Simulates the app rejecting the press (e.g. paused or busy):
        # on_press returns False, so _toggle_state must stay False and
        # on_release must never fire on the next press attempt.
        release_calls = []
        tm = self._make_manager(
            on_press=lambda: False,
            on_release=lambda: release_calls.append(True),
        )

        tm._fire_press("toggle")
        self.assertFalse(tm._toggle_state)

        # A second press attempt should again call on_press (still not
        # "recording" from the trigger's point of view), not on_release.
        tm._fire_press("toggle")
        self.assertFalse(tm._toggle_state)
        self.assertEqual(release_calls, [])

    def test_accepted_press_flips_toggle_state_and_next_press_releases(self):
        # Simulates the app accepting the press: on_press returns True, so
        # _toggle_state must flip to True (recording). The following press
        # is then treated as the "stop" toggle and must call on_release,
        # flipping _toggle_state back to False.
        press_calls = []
        release_calls = []
        tm = self._make_manager(
            on_press=lambda: (press_calls.append(True) or True),
            on_release=lambda: release_calls.append(True),
        )

        tm._fire_press("toggle")
        self.assertTrue(tm._toggle_state)
        self.assertEqual(len(press_calls), 1)
        self.assertEqual(release_calls, [])

        tm._fire_press("toggle")
        self.assertFalse(tm._toggle_state)
        self.assertEqual(len(press_calls), 1)
        self.assertEqual(len(release_calls), 1)

    def test_none_return_treated_as_accepted_for_backward_compatibility(self):
        # An on_press callback that returns None (the old signature, or a
        # caller that hasn't been updated) must be treated as "accepted",
        # matching hold-mode's fire-and-forget semantics.
        tm = self._make_manager(on_press=lambda: None, on_release=lambda: None)

        tm._fire_press("toggle")
        self.assertTrue(tm._toggle_state)


if __name__ == "__main__":
    unittest.main()
