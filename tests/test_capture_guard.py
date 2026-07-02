"""Unit tests for the trigger-rebind capture guard (#7) in
flowlocal.hotkey.TriggerManager.

Bare modifier presses (ctrl/shift/alt/win, left or right variant) must be
ignored while capturing a new binding, so a user can't accidentally end up
with "ctrl_l" alone as their trigger. A normal (non-modifier) key or a
mouse x-button press should still be captured immediately.

These tests drive TriggerManager._on_key_press / _on_click directly with
real pynput Key/KeyCode/Button objects rather than starting real listeners,
per the harness note in flowlocal/hotkey.py.

Run with: py -3.11 -m unittest discover -s tests
or:       py -3.11 -m unittest tests.test_capture_guard
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flowlocal.hotkey import TriggerManager


class TestCaptureGuard(unittest.TestCase):
    def _make_manager(self):
        return TriggerManager(binding="key:f9", mode="hold")

    def test_bare_modifier_press_is_ignored_during_capture(self):
        from pynput import keyboard

        tm = self._make_manager()
        captured = []
        tm.capture_next(lambda binding: captured.append(binding))

        for key in (
            keyboard.Key.ctrl_l,
            keyboard.Key.ctrl_r,
            keyboard.Key.shift_l,
            keyboard.Key.shift_r,
            keyboard.Key.alt_l,
            keyboard.Key.alt_r,
            keyboard.Key.cmd,
        ):
            tm._on_key_press(key)

        # None of the modifier presses should have completed the capture:
        # capture mode must still be armed and no callback fired.
        self.assertEqual(captured, [])
        self.assertTrue(tm._capture_mode)

    def test_normal_key_press_completes_capture(self):
        from pynput import keyboard

        tm = self._make_manager()
        captured = []
        tm.capture_next(lambda binding: captured.append(binding))

        # A bare modifier first (ignored)...
        tm._on_key_press(keyboard.Key.ctrl_l)
        self.assertEqual(captured, [])
        self.assertTrue(tm._capture_mode)

        # ...then a real key completes the capture.
        tm._on_key_press(keyboard.KeyCode.from_char("g"))

        self.assertEqual(captured, ["key:g"])
        self.assertFalse(tm._capture_mode)

    def test_mouse_x_button_still_captured_immediately(self):
        from pynput.mouse import Button

        tm = self._make_manager()
        captured = []
        tm.capture_next(lambda binding: captured.append(binding))

        tm._on_click(0, 0, Button.x2, True)

        self.assertEqual(captured, ["mouse:x2"])
        self.assertFalse(tm._capture_mode)

    def test_function_key_capture_still_works(self):
        from pynput import keyboard

        tm = self._make_manager()
        captured = []
        tm.capture_next(lambda binding: captured.append(binding))

        tm._on_key_press(keyboard.Key.f9)

        self.assertEqual(captured, ["key:f9"])
        self.assertFalse(tm._capture_mode)


if __name__ == "__main__":
    unittest.main()
