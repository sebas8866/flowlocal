"""Integration tests for TriggerManager's wiring of the hold-mode
double-tap-latch gesture (flowlocal.flow_gesture.FlowGesture).

These drive TriggerManager's public/semi-public surface (_handle_key_press,
_handle_key_release, set_mode, set_binding, stop) directly, per the harness
note in flowlocal/hotkey.py, and use a fake clock injected into the
manager's internal gesture so hold vs. tap timing is deterministic (no real
sleeps, no real threading.Timer waits).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flowlocal.flow_gesture import IDLE, LATCHED, TAP_WAIT  # noqa: E402
from flowlocal.hotkey import TriggerManager  # noqa: E402


class _FakeTimer:
    def __init__(self, delay_seconds, callback):
        self.delay_seconds = delay_seconds
        self.callback = callback
        self.cancelled = False

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.callback()


def _make_manager(binding="key:f9", mode="hold"):
    events = []
    tm = TriggerManager(
        binding,
        mode=mode,
        on_press=lambda: events.append("press") or True,
        on_release=lambda: events.append("release"),
        on_cancel=lambda: events.append("cancel"),
    )
    fake_now = [0.0]
    timers = []
    tm._gesture._clock = lambda: fake_now[0]
    tm._gesture._timer_factory = lambda delay, cb: timers.append(_FakeTimer(delay, cb)) or timers[-1]
    return tm, events, fake_now, timers


class ToggleModeBypassesGestureTest(unittest.TestCase):
    def test_toggle_mode_never_touches_gesture_state(self):
        tm, events, _fake_now, _timers = _make_manager(mode="toggle")
        tm._handle_key_press("f9")
        tm._handle_key_release("f9")
        # Toggle fires entirely on press; gesture must stay IDLE throughout
        # since toggle mode's _fire_press/_fire_release never call it.
        self.assertEqual(tm._gesture.state, IDLE)
        self.assertEqual(events, ["press"])


class EscCancelResetsGestureTest(unittest.TestCase):
    def test_esc_during_latched_session_resets_gesture_and_cancels_timer(self):
        tm, events, fake_now, timers = _make_manager()

        tm._handle_key_press("f9")  # press
        fake_now[0] += 0.1  # tap
        tm._handle_key_release("f9")
        self.assertEqual(tm._gesture.state, TAP_WAIT)

        tm._handle_key_press("f9")  # second press: latch
        self.assertEqual(tm._gesture.state, LATCHED)

        tm._handle_key_press("esc")  # Esc cancels
        self.assertEqual(tm._gesture.state, IDLE)
        self.assertEqual(events[-1], "cancel")

        # A stray release arriving after Esc must not fire anything further.
        tm._handle_key_release("f9")
        self.assertEqual(events.count("release"), 0)

    def test_esc_during_tap_wait_cancels_the_pending_timer(self):
        tm, events, fake_now, timers = _make_manager()

        tm._handle_key_press("f9")
        fake_now[0] += 0.1
        tm._handle_key_release("f9")
        self.assertEqual(tm._gesture.state, TAP_WAIT)
        timer = timers[-1]

        tm._handle_key_press("esc")
        self.assertEqual(tm._gesture.state, IDLE)
        self.assertTrue(timer.cancelled)

        timer.fire()  # late/stale firing must be a no-op
        self.assertEqual(events.count("release"), 0)


class SetModeSetBindingResetGestureTest(unittest.TestCase):
    def test_set_mode_cancels_pending_latch_timer(self):
        tm, events, fake_now, timers = _make_manager()

        tm._handle_key_press("f9")
        fake_now[0] += 0.1
        tm._handle_key_release("f9")
        self.assertEqual(tm._gesture.state, TAP_WAIT)
        timer = timers[-1]

        tm.set_mode("toggle")
        self.assertEqual(tm._gesture.state, IDLE)
        self.assertTrue(timer.cancelled)

        timer.fire()
        self.assertEqual(events.count("release"), 0)

    def test_set_binding_cancels_pending_latch_timer(self):
        tm, events, fake_now, timers = _make_manager()

        tm._handle_key_press("f9")
        fake_now[0] += 0.1
        tm._handle_key_release("f9")
        self.assertEqual(tm._gesture.state, TAP_WAIT)
        timer = timers[-1]

        tm.set_binding("key:f10")
        self.assertEqual(tm._gesture.state, IDLE)
        self.assertTrue(timer.cancelled)

        timer.fire()
        self.assertEqual(events.count("release"), 0)


class StopCancelsGestureTimerTest(unittest.TestCase):
    def test_stop_cancels_pending_latch_timer(self):
        tm, events, fake_now, timers = _make_manager()
        tm._keyboard_listener = None
        tm._mouse_listener = None

        tm._handle_key_press("f9")
        fake_now[0] += 0.1
        tm._handle_key_release("f9")
        timer = timers[-1]

        tm.stop()
        self.assertEqual(tm._gesture.state, IDLE)
        self.assertTrue(timer.cancelled)

        timer.fire()
        self.assertEqual(events.count("release"), 0)


class RejectedPressResetsGestureTest(unittest.TestCase):
    def test_rejected_press_leaves_gesture_idle_and_app_can_retry(self):
        events = []
        tm = TriggerManager(
            "key:f9",
            mode="hold",
            on_press=lambda: False,  # app rejects (paused/busy)
            on_release=lambda: events.append("release"),
        )

        tm._handle_key_press("f9")
        self.assertEqual(tm._gesture.state, IDLE)

        tm._handle_key_release("f9")
        self.assertEqual(events, [])


class HoldAndTapThroughTriggerManagerTest(unittest.TestCase):
    def test_full_hold_fires_press_and_release(self):
        tm, events, fake_now, _timers = _make_manager()

        tm._handle_key_press("f9")
        fake_now[0] += 0.5
        tm._handle_key_release("f9")

        self.assertEqual(events, ["press", "release"])
        self.assertEqual(tm._gesture.state, IDLE)

    def test_tap_then_window_expiry_fires_release_once(self):
        tm, events, fake_now, timers = _make_manager()

        tm._handle_key_press("f9")
        fake_now[0] += 0.1
        tm._handle_key_release("f9")
        self.assertEqual(events, ["press"])

        timers[-1].fire()
        self.assertEqual(events, ["press", "release"])
        self.assertEqual(tm._gesture.state, IDLE)


if __name__ == "__main__":
    unittest.main()
