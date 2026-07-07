"""Unit tests for flowlocal.flow_gesture.FlowGesture — the hold-mode
double-tap-latch gesture state machine.

Uses a fake clock and a fake timer factory (no real threading.Timer, no
sleeps): the fake timer captures its callback and is fired manually via
`fire()` to simulate the latch window expiring.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flowlocal.flow_gesture import (  # noqa: E402
    FlowGesture,
    HELD,
    IDLE,
    LATCHED,
    LATCH_WINDOW_MS,
    STOPPING,
    TAP_MS,
    TAP_WAIT,
)


class _FakeTimer:
    """Stand-in for threading.Timer: records delay/callback, never actually
    schedules anything. Test drives expiry manually via fire()."""

    def __init__(self, delay_seconds, callback):
        self.delay_seconds = delay_seconds
        self.callback = callback
        self.started = False
        self.cancelled = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        """Simulate the timer thread invoking the callback — a no-op if the
        timer was already cancelled, mirroring real threading.Timer.
        """
        if not self.cancelled:
            self.callback()


class _FakeClock:
    def __init__(self, start=0.0):
        self._now = start

    def __call__(self):
        return self._now

    def advance(self, seconds):
        self._now += seconds


class _Harness:
    """Builds a FlowGesture with a fake clock/timer and records
    press/release fire events plus the accept/reject value to return from
    fire_press.
    """

    def __init__(self, press_accept=True):
        self.clock = _FakeClock()
        self.timers = []
        self.press_calls = 0
        self.release_calls = 0
        self._press_accept = press_accept

        self.gesture = FlowGesture(
            fire_press=self._fire_press,
            fire_release=self._fire_release,
            clock=self.clock,
            timer_factory=self._timer_factory,
        )

    def _timer_factory(self, delay_seconds, callback):
        t = _FakeTimer(delay_seconds, callback)
        self.timers.append(t)
        return t

    def _fire_press(self):
        self.press_calls += 1
        return self._press_accept

    def _fire_release(self):
        self.release_calls += 1

    @property
    def last_timer(self):
        return self.timers[-1] if self.timers else None


class HoldReleaseTest(unittest.TestCase):
    """A press held >= TAP_MS then released is exactly today's hold
    behavior: fire_release() once, no timer involved.
    """

    def test_hold_release_fires_release_once(self):
        h = _Harness()
        gesture = h.gesture

        gesture.press()
        self.assertEqual(h.press_calls, 1)
        self.assertEqual(gesture.state, HELD)

        h.clock.advance(TAP_MS / 1000.0)  # exactly at threshold: still a hold
        gesture.release()

        self.assertEqual(h.release_calls, 1)
        self.assertEqual(gesture.state, IDLE)
        self.assertEqual(len(h.timers), 0)  # no latch timer ever created

    def test_hold_release_well_past_threshold(self):
        h = _Harness()
        gesture = h.gesture

        gesture.press()
        h.clock.advance(1.0)
        gesture.release()

        self.assertEqual(h.release_calls, 1)
        self.assertEqual(gesture.state, IDLE)


class TapThenWindowExpiryTest(unittest.TestCase):
    """A quick tap (release < TAP_MS) keeps recording running and arms the
    latch window; if no second press arrives, fire_release() runs exactly
    once when the window expires.
    """

    def test_tap_does_not_fire_release_immediately(self):
        h = _Harness()
        gesture = h.gesture

        gesture.press()
        h.clock.advance(0.1)  # 100ms: a tap
        gesture.release()

        self.assertEqual(h.release_calls, 0)
        self.assertEqual(gesture.state, TAP_WAIT)
        self.assertEqual(len(h.timers), 1)
        self.assertTrue(h.last_timer.started)
        self.assertAlmostEqual(h.last_timer.delay_seconds, LATCH_WINDOW_MS / 1000.0)

    def test_window_expiry_fires_release_exactly_once(self):
        h = _Harness()
        gesture = h.gesture

        gesture.press()
        h.clock.advance(0.1)
        gesture.release()
        self.assertEqual(gesture.state, TAP_WAIT)

        h.last_timer.fire()

        self.assertEqual(h.release_calls, 1)
        self.assertEqual(gesture.state, IDLE)

    def test_double_window_expiry_after_latch_is_a_noop(self):
        # Second press arrives before the timer fires -> LATCHED, and the
        # (canceled) timer firing late must not do anything.
        h = _Harness()
        gesture = h.gesture

        gesture.press()
        h.clock.advance(0.1)
        gesture.release()
        timer = h.last_timer

        gesture.press()  # second press inside the window -> LATCHED
        self.assertEqual(gesture.state, LATCHED)
        self.assertTrue(timer.cancelled)

        # Stale timer fires late (simulating a race) -> no-op, no extra
        # release, state unchanged.
        timer.fire()
        self.assertEqual(h.release_calls, 0)
        self.assertEqual(gesture.state, LATCHED)

    def test_late_expired_timer_after_manual_window_expired_call_is_noop(self):
        # Directly exercises window_expired() being called twice (defensive
        # double-invocation) — only the first has any effect.
        h = _Harness()
        gesture = h.gesture

        gesture.press()
        h.clock.advance(0.1)
        gesture.release()

        gesture.window_expired()
        self.assertEqual(h.release_calls, 1)
        self.assertEqual(gesture.state, IDLE)

        gesture.window_expired()  # stale second call: no-op
        self.assertEqual(h.release_calls, 1)


class LatchTest(unittest.TestCase):
    """Second press inside the window latches hands-free recording; no
    extra fire_press/fire_release. The next press then stops & processes,
    and its paired release is swallowed.
    """

    def test_second_press_latches_without_extra_fires(self):
        h = _Harness()
        gesture = h.gesture

        gesture.press()
        h.clock.advance(0.1)
        gesture.release()
        self.assertEqual(h.press_calls, 1)

        gesture.press()  # latch
        self.assertEqual(gesture.state, LATCHED)
        # No additional fire_press or fire_release from latching itself.
        self.assertEqual(h.press_calls, 1)
        self.assertEqual(h.release_calls, 0)
        # The timer that was pending must have been canceled.
        self.assertTrue(h.last_timer.cancelled)

    def test_latched_next_press_fires_release_once_and_swallows_its_release(self):
        h = _Harness()
        gesture = h.gesture

        gesture.press()
        h.clock.advance(0.1)
        gesture.release()
        gesture.press()  # latch
        self.assertEqual(gesture.state, LATCHED)

        gesture.press()  # the "stop" press
        self.assertEqual(h.release_calls, 1)
        self.assertEqual(gesture.state, STOPPING)

        gesture.release()  # paired release for the stop press: swallowed
        self.assertEqual(h.release_calls, 1)  # unchanged
        self.assertEqual(gesture.state, IDLE)


class RejectedPressTest(unittest.TestCase):
    def test_rejected_press_leaves_gesture_idle(self):
        h = _Harness(press_accept=False)
        gesture = h.gesture

        gesture.press()
        self.assertEqual(h.press_calls, 1)
        self.assertEqual(gesture.state, IDLE)

        # A release with nothing in flight is a no-op.
        gesture.release()
        self.assertEqual(h.release_calls, 0)
        self.assertEqual(gesture.state, IDLE)

    def test_rejected_press_via_none_return_is_treated_as_accepted(self):
        # Matches TriggerManager's existing "None means accepted" backward
        # compatibility convention for on_press.
        h = _Harness(press_accept=True)
        h._press_accept = None
        gesture = h.gesture

        gesture.press()
        self.assertEqual(gesture.state, HELD)


class ResetTest(unittest.TestCase):
    def test_reset_from_held_cancels_nothing_and_returns_idle(self):
        h = _Harness()
        gesture = h.gesture
        gesture.press()
        self.assertEqual(gesture.state, HELD)

        gesture.reset()
        self.assertEqual(gesture.state, IDLE)

        # A release that arrives after reset (e.g. Esc raced the release)
        # must not fire anything.
        gesture.release()
        self.assertEqual(h.release_calls, 0)

    def test_reset_from_tap_wait_cancels_pending_timer(self):
        h = _Harness()
        gesture = h.gesture
        gesture.press()
        h.clock.advance(0.1)
        gesture.release()
        self.assertEqual(gesture.state, TAP_WAIT)
        timer = h.last_timer

        gesture.reset()
        self.assertEqual(gesture.state, IDLE)
        self.assertTrue(timer.cancelled)

        # Even if the (canceled) timer still fires late, no release must
        # result.
        timer.fire()
        self.assertEqual(h.release_calls, 0)

    def test_reset_from_latched_cancels_and_returns_idle(self):
        h = _Harness()
        gesture = h.gesture
        gesture.press()
        h.clock.advance(0.1)
        gesture.release()
        gesture.press()  # latch
        self.assertEqual(gesture.state, LATCHED)

        gesture.reset()
        self.assertEqual(gesture.state, IDLE)

        # Next press starts a fresh gesture rather than stopping a phantom
        # latched session.
        gesture.press()
        self.assertEqual(h.press_calls, 2)
        self.assertEqual(gesture.state, HELD)

    def test_reset_from_stopping_returns_idle_and_swallows_nothing_twice(self):
        h = _Harness()
        gesture = h.gesture
        gesture.press()
        h.clock.advance(0.1)
        gesture.release()
        gesture.press()  # latch
        gesture.press()  # stop -> STOPPING, fires release
        self.assertEqual(gesture.state, STOPPING)
        self.assertEqual(h.release_calls, 1)

        gesture.reset()
        self.assertEqual(gesture.state, IDLE)

        # The paired release for the stop press arriving after reset is a
        # no-op (already swallowed conceptually by reset()).
        gesture.release()
        self.assertEqual(h.release_calls, 1)

    def test_reset_from_idle_is_a_harmless_noop(self):
        h = _Harness()
        gesture = h.gesture
        gesture.reset()
        self.assertEqual(gesture.state, IDLE)


if __name__ == "__main__":
    unittest.main()
