"""Regression tests for the win32 event-filter suppression path.

pynput semantics: listener.suppress_event() raises an exception that unwinds
out of the filter, and a suppressed event NEVER reaches the normal
on_press/on_release/on_click callbacks. The filters must therefore dispatch
trigger handling themselves before suppressing — the original implementation
didn't, which made the default mouse:x2 binding completely dead (recording
never started) while still swallowing the button system-wide.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flowlocal.hotkey import (  # noqa: E402
    TriggerManager,
    _WM_KEYDOWN,
    _WM_KEYUP,
    _WM_XBUTTONDOWN,
    _WM_XBUTTONUP,
)


class _Suppressed(Exception):
    pass


class _FakeListener:
    def suppress_event(self):
        raise _Suppressed()


class _MouseData:
    def __init__(self, hiword):
        self.mouseData = hiword << 16


class _KeyData:
    def __init__(self, vk):
        self.vkCode = vk


class MouseFilterDispatchTest(unittest.TestCase):
    def _manager(self, binding="mouse:x2", mode="hold"):
        events = []
        mgr = TriggerManager(
            binding,
            mode=mode,
            on_press=lambda: events.append("press") or True,
            on_release=lambda: events.append("release"),
        )
        mgr._mouse_listener = _FakeListener()
        mgr._keyboard_listener = _FakeListener()
        return mgr, events

    def test_bound_button_press_fires_and_suppresses(self):
        mgr, events = self._manager()
        with self.assertRaises(_Suppressed):
            mgr._mouse_event_filter(_WM_XBUTTONDOWN, _MouseData(2))
        self.assertEqual(events, ["press"])

    def test_bound_button_release_fires_and_suppresses(self):
        # Hold-mode press/release now funnels through FlowGesture (see
        # flowlocal/flow_gesture.py), which distinguishes a real hold
        # (>= TAP_MS between press and release) from a quick tap (which
        # keeps recording running and arms the double-tap-latch window
        # instead of firing release immediately). This test's original
        # intent — a plain hold-and-release fires release synchronously —
        # is preserved by advancing the gesture's injected clock past
        # TAP_MS between the press and release events, same as a real
        # press held for a beat before letting go.
        mgr, events = self._manager()
        fake_now = [0.0]
        mgr._gesture._clock = lambda: fake_now[0]
        with self.assertRaises(_Suppressed):
            mgr._mouse_event_filter(_WM_XBUTTONDOWN, _MouseData(2))
        fake_now[0] += 0.5  # 500ms held: a hold, not a tap
        with self.assertRaises(_Suppressed):
            mgr._mouse_event_filter(_WM_XBUTTONUP, _MouseData(2))
        self.assertEqual(events, ["press", "release"])

    def test_unbound_button_passes_through_untouched(self):
        mgr, events = self._manager(binding="mouse:x2")
        # x1 is not bound: no suppression, no dispatch.
        mgr._mouse_event_filter(_WM_XBUTTONDOWN, _MouseData(1))
        self.assertEqual(events, [])

    def test_suppress_disabled_leaves_filter_inert(self):
        mgr, events = self._manager()
        mgr.suppress_enabled = False
        mgr._mouse_event_filter(_WM_XBUTTONDOWN, _MouseData(2))
        self.assertEqual(events, [])

    def test_capture_completes_from_filter(self):
        mgr, _ = self._manager(binding="mouse:x2")
        captured = []
        mgr.capture_next(captured.append)
        with self.assertRaises(_Suppressed):
            mgr._mouse_event_filter(_WM_XBUTTONDOWN, _MouseData(1))
        self.assertEqual(captured, ["mouse:x1"])

    def test_no_double_fire_via_on_click_after_filter(self):
        # When the filter suppresses, pynput never calls _on_click; when it
        # doesn't suppress, _on_click handles it. Simulate the non-suppressed
        # path and confirm a single fire.
        mgr, events = self._manager(binding="mouse:x1")
        # x2 event passes the filter (not bound)...
        mgr._mouse_event_filter(_WM_XBUTTONDOWN, _MouseData(2))
        # ...and pynput then delivers it to on_click, which ignores it too.
        mgr._handle_x_button("x2", True)
        self.assertEqual(events, [])


class KeyboardFilterDispatchTest(unittest.TestCase):
    def _manager(self, binding, mode="hold"):
        events = []
        mgr = TriggerManager(
            binding,
            mode=mode,
            on_press=lambda: events.append("press") or True,
            on_release=lambda: events.append("release"),
        )
        mgr._mouse_listener = _FakeListener()
        mgr._keyboard_listener = _FakeListener()
        return mgr, events

    def test_bound_single_key_fires_and_suppresses(self):
        # See the analogous comment in MouseFilterDispatchTest
        # .test_bound_button_release_fires_and_suppresses: hold-mode
        # press/release now funnels through FlowGesture, which needs
        # >= TAP_MS between press and release to treat this as a hold
        # (rather than a quick tap that keeps recording running). Advance
        # the gesture's injected clock accordingly to represent a real held
        # keypress, preserving this test's original intent.
        mgr, events = self._manager("key:f9")
        fake_now = [0.0]
        mgr._gesture._clock = lambda: fake_now[0]
        with self.assertRaises(_Suppressed):
            mgr._keyboard_event_filter(_WM_KEYDOWN, _KeyData(0x78))
        fake_now[0] += 0.5  # 500ms held: a hold, not a tap
        with self.assertRaises(_Suppressed):
            mgr._keyboard_event_filter(_WM_KEYUP, _KeyData(0x78))
        self.assertEqual(events, ["press", "release"])

    def test_combo_binding_not_suppressed(self):
        mgr, events = self._manager("key:ctrl_l+f9")
        # Individual combo keys must pass through (else Ctrl would be
        # swallowed system-wide); the normal callbacks handle combos.
        mgr._keyboard_event_filter(_WM_KEYDOWN, _KeyData(0xA2))
        mgr._keyboard_event_filter(_WM_KEYDOWN, _KeyData(0x78))
        self.assertEqual(events, [])

    def test_unbound_key_passes_through(self):
        mgr, events = self._manager("key:f9")
        mgr._keyboard_event_filter(_WM_KEYDOWN, _KeyData(0x41))  # 'a'
        self.assertEqual(events, [])

    def test_capture_letter_key_completes_from_filter(self):
        mgr, _ = self._manager("key:f9")
        captured = []
        mgr.capture_next(captured.append)
        with self.assertRaises(_Suppressed):
            mgr._keyboard_event_filter(_WM_KEYDOWN, _KeyData(0x44))  # 'd'
        self.assertEqual(captured, ["key:d"])

    def test_capture_modifier_passes_through(self):
        mgr, _ = self._manager("key:f9")
        captured = []
        mgr.capture_next(captured.append)
        mgr._keyboard_event_filter(_WM_KEYDOWN, _KeyData(0xA2))  # ctrl_l
        self.assertEqual(captured, [])
        # Still capturing: the next real key completes it.
        with self.assertRaises(_Suppressed):
            mgr._keyboard_event_filter(_WM_KEYDOWN, _KeyData(0x70))  # f1
        self.assertEqual(captured, ["key:f1"])

    def test_capture_unnameable_key_passes_through(self):
        mgr, _ = self._manager("key:f9")
        captured = []
        mgr.capture_next(captured.append)
        # OEM key we can't name from the raw vk: must NOT be swallowed
        # (swallowing without dispatch would freeze the keyboard until a
        # mouse capture rescued it).
        mgr._keyboard_event_filter(_WM_KEYDOWN, _KeyData(0xBA))
        self.assertEqual(captured, [])

    def test_toggle_mode_via_filter(self):
        mgr, events = self._manager("key:f9", mode="toggle")
        with self.assertRaises(_Suppressed):
            mgr._keyboard_event_filter(_WM_KEYDOWN, _KeyData(0x78))
        with self.assertRaises(_Suppressed):
            mgr._keyboard_event_filter(_WM_KEYUP, _KeyData(0x78))
        # toggle: press starts...
        self.assertEqual(events, ["press"])
        with self.assertRaises(_Suppressed):
            mgr._keyboard_event_filter(_WM_KEYDOWN, _KeyData(0x78))
        # ...second press stops.
        self.assertEqual(events, ["press", "release"])


if __name__ == "__main__":
    unittest.main()
