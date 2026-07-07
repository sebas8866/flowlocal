"""Pure, dependency-free hold-mode gesture state machine (double-tap latch).

Extracted so the press/release/window-expiry/reset race logic can be unit
tested without pynput, threading.Timer, or wall-clock sleeps. Mirrors the
style of flowlocal/session_state.py: a single internal lock, explicit
inputs, side effects handed back to the caller as callback invocations
(fire_press/fire_release) rather than performed here.

Feature: hold mode gets a "double-tap to lock" upgrade on top of today's
plain push-to-talk. Toggle mode is untouched and never touches this class.

States:
    IDLE      - nothing in progress, ready to accept a press.
    HELD      - a press has fired (fire_press ran); waiting to see whether
                this turns out to be a hold (release >= TAP_MS later) or a
                quick tap (release < TAP_MS later).
    TAP_WAIT  - the user released quickly (a "tap"); recording is still
                running (release() was NOT forwarded) and we're waiting up
                to LATCH_WINDOW_MS for a second press to arrive and latch
                hands-free recording. A timer is running.
    LATCHED   - hands-free recording is active (a second press arrived
                inside the window). No timer running; waiting for the next
                press, which stops it.
    STOPPING  - a release is owed to fire_release but has already been
                claimed/scheduled (used to swallow the release that follows
                the stopping press while LATCHED, so that press's own
                release doesn't double-fire).

Inputs (all guarded by one lock):
    press()           - trigger press arrived.
    release()          - trigger release arrived (paired with the most
                         recent press()).
    window_expired()   - the TAP_WAIT timer fired with nothing else having
                         happened first (i.e. still armed).
    reset()            - force back to IDLE, canceling any pending timer
                         (Esc-cancel, auto-stop, rebind, stop()).

Outputs: side effects are invoked directly on the provided
`fire_press`/`fire_release` callables (matching TriggerManager's existing
`_fire_press`/`_fire_release` signatures for hold mode — no arguments,
firing on_press()/on_release()). `fire_press` is expected to return the
app's accept/reject bool (mirroring on_press's contract); a rejected press
resets the gesture to IDLE without ever considering it a hold or a tap.

Timing: injectable `clock` (callable -> monotonic seconds) and `timer_factory`
(callable(delay_seconds, callback) -> object with `.cancel()`, matching the
subset of threading.Timer used here) so tests can drive expiry manually
without real threading.Timer or sleeps. Real usage constructs with
`timer_factory=lambda delay, cb: threading.Timer(delay, cb)` (caller must
`.daemon = True` and `.start()` it — see `_schedule_window`).
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

IDLE = "IDLE"
HELD = "HELD"
TAP_WAIT = "TAP_WAIT"
LATCHED = "LATCHED"
STOPPING = "STOPPING"

# A press held at least this long before release is a normal hold: stop &
# process immediately on release (today's exact behavior).
TAP_MS = 350

# After a quick tap (release < TAP_MS), recording continues and we wait up
# to this long for a second press to arrive and latch hands-free recording.
LATCH_WINDOW_MS = 400


def _default_timer_factory(delay_seconds: float, callback: Callable[[], None]):
    timer = threading.Timer(delay_seconds, callback)
    timer.daemon = True
    return timer


class FlowGesture:
    """Thread-safe hold-mode double-tap-latch gesture state machine.

    All public methods are atomic (guarded by an internal lock) and safe to
    call from any thread (the hook thread for press/release, a timer thread
    for window_expired()).
    """

    def __init__(
        self,
        fire_press: Callable[[], Optional[bool]],
        fire_release: Callable[[], None],
        clock: Callable[[], float] = time.monotonic,
        timer_factory: Callable[[float, Callable[[], None]], object] = _default_timer_factory,
    ) -> None:
        self._fire_press = fire_press
        self._fire_release = fire_release
        self._clock = clock
        self._timer_factory = timer_factory

        self._lock = threading.Lock()
        self._state = IDLE
        self._press_time: Optional[float] = None
        self._timer = None

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    # --- internal helpers (caller must hold self._lock) --------------------

    def _cancel_timer_locked(self) -> None:
        if self._timer is not None:
            try:
                self._timer.cancel()
            except Exception:
                pass
            self._timer = None

    def _schedule_window_locked(self) -> None:
        self._cancel_timer_locked()
        timer = self._timer_factory(LATCH_WINDOW_MS / 1000.0, self.window_expired)
        self._timer = timer
        # Real threading.Timer instances must be started; fakes used in
        # tests generally don't implement start() at all, so tolerate its
        # absence rather than requiring test doubles to stub it out.
        start = getattr(timer, "start", None)
        if start is not None:
            start()

    # --- inputs --------------------------------------------------------

    def press(self) -> None:
        """A trigger press arrived. Behavior depends on current state:

        IDLE     -> fire_press(); accepted -> HELD, rejected -> stays IDLE.
        TAP_WAIT -> second press inside the latch window: LATCH. Cancels
                    the pending window timer. Nothing is fired (recording
                    is already running from the original press).
        LATCHED  -> this press stops & processes: fire_release(), then move
                    to STOPPING so this same press's paired release (which
                    the caller will also deliver) is swallowed.
        HELD, STOPPING -> spurious/unexpected re-press with no matching
                    release yet; ignored (defensive no-op).
        """
        with self._lock:
            state = self._state

            if state == TAP_WAIT:
                self._cancel_timer_locked()
                self._state = LATCHED
                return
            elif state == LATCHED:
                self._state = STOPPING
            elif state == IDLE:
                self._state = HELD  # tentative; may be reverted below
            else:
                # HELD or STOPPING: no matching release seen yet for the
                # current gesture; a second press here is unexpected.
                # Ignore rather than corrupting state.
                return

        if state == LATCHED:
            self._fire_release()
            return

        # state was IDLE: attempt to actually start the recording.
        accepted = self._fire_press()
        if accepted is None:
            accepted = True

        with self._lock:
            if accepted:
                self._press_time = self._clock()
            else:
                self._state = IDLE
                self._press_time = None

    def release(self) -> None:
        """A trigger release arrived, paired with the most recent press().

        HELD, elapsed >= TAP_MS  -> normal hold: fire_release(), -> IDLE.
        HELD, elapsed <  TAP_MS  -> quick tap: recording continues, start
                                    the latch window timer, -> TAP_WAIT.
        STOPPING                 -> this release is owed to the press that
                                    just stopped a LATCHED session: swallow
                                    it (already fired from press()), -> IDLE.
        IDLE, TAP_WAIT, LATCHED  -> no release is expected in these states
                                    (release always pairs with a press that
                                    moved us to HELD or was swallowed via
                                    STOPPING); ignored defensively.
        """
        with self._lock:
            state = self._state

            if state == STOPPING:
                self._state = IDLE
                self._press_time = None
                return

            if state != HELD:
                # No matching press in flight (e.g. release from a rejected
                # press, or a stray event) — nothing to do.
                return

            press_time = self._press_time
            elapsed_ms = (self._clock() - press_time) * 1000.0 if press_time is not None else TAP_MS

            if elapsed_ms >= TAP_MS:
                self._state = IDLE
                self._press_time = None
                fire_now = True
            else:
                self._state = TAP_WAIT
                self._schedule_window_locked()
                fire_now = False

        if fire_now:
            self._fire_release()

    def window_expired(self) -> None:
        """The TAP_WAIT latch-window timer fired. If we're still in
        TAP_WAIT (no second press arrived), the tap was just a very short
        dictation: stop & process now, -> IDLE. If the state has since
        moved on (LATCHED, or reset() ran), this is a stale timer firing
        and must be a no-op.
        """
        with self._lock:
            if self._state != TAP_WAIT:
                return
            self._state = IDLE
            self._press_time = None
            self._timer = None

        self._fire_release()

    def reset(self) -> None:
        """Force back to IDLE regardless of current state, canceling any
        pending timer. Used by Esc-cancel, the max-duration auto-stop, and
        by TriggerManager.set_mode/set_binding/stop() to avoid leaking a
        timer or firing a release after a rebind.
        """
        with self._lock:
            self._cancel_timer_locked()
            self._state = IDLE
            self._press_time = None
