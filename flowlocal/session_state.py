"""Pure, dependency-free dictation session state machine.

Extracted out of App so the delicate press/release/cancel/auto-stop race
logic can be unit-tested without tkinter, pynput, or win32.

States:
    IDLE       - nothing in progress, ready to accept a press.
    STARTING   - press accepted, recorder.start() is in flight on the
                 starter thread (PortAudio stream open + optional app
                 context capture haven't finished yet).
    RECORDING  - recorder.start() succeeded; audio is being captured.
    FINISHING  - a stop (release, cancel, or auto-stop) has been claimed
                 and is being handled; exactly one caller may hold this.

Transitions (all guarded by a single lock):
    press():           IDLE -> STARTING              (accept)
                        else                          (reject)
    start_succeeded():  STARTING -> RECORDING          (normal case)
                        STARTING -> FINISHING          (a stop was
                                                         requested while
                                                         starting; caller
                                                         must immediately
                                                         run the finish
                                                         path instead of
                                                         going idle)
    start_failed():     STARTING -> IDLE
    claim_finish():      RECORDING -> FINISHING         (accept, caller
                                                         becomes the sole
                                                         owner of the stop)
                        STARTING -> pending_stop=True   (release/cancel
                                                         during STARTING:
                                                         remembered, acted
                                                         on by
                                                         start_succeeded())
                        else                            (reject: someone
                                                         else already
                                                         claimed it, or
                                                         nothing is
                                                         in-flight)
    cancel_pending():   like claim_finish() but marks the pending/claim as
                        a cancel (discard audio) rather than a normal stop.
    finished():          FINISHING -> IDLE

This class holds no references to the recorder, sounds, tray, etc. — it only
tracks the state and hands the caller a plain-English verdict so app.py can
decide what side effects to run.
"""
from __future__ import annotations

import threading
from typing import Optional

IDLE = "IDLE"
STARTING = "STARTING"
RECORDING = "RECORDING"
FINISHING = "FINISHING"


class ClaimResult:
    """Result of attempting to claim a stop (release, cancel, or
    auto-stop) against the session state.

    `claimed`: True if this caller now owns the stop and must run it.
    `pending`: True if the claim was recorded as a pending-stop against a
        still-in-flight STARTING state; start_succeeded() will act on it.
        The caller does nothing further right now.
    `cancel`: True if this claim/pending stop should discard the audio
        (Esc-cancel) rather than transcribe it.
    """

    __slots__ = ("claimed", "pending", "cancel")

    def __init__(self, claimed: bool, pending: bool, cancel: bool) -> None:
        self.claimed = claimed
        self.pending = pending
        self.cancel = cancel

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return f"ClaimResult(claimed={self.claimed}, pending={self.pending}, cancel={self.cancel})"

    def __eq__(self, other) -> bool:
        if not isinstance(other, ClaimResult):
            return NotImplemented
        return (
            self.claimed == other.claimed
            and self.pending == other.pending
            and self.cancel == other.cancel
        )


class SessionState:
    """Thread-safe IDLE -> STARTING -> RECORDING -> FINISHING -> IDLE
    state machine. All public methods are atomic (guarded by an internal
    lock) and safe to call from any thread.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = IDLE
        # Set when a release/cancel/auto-stop arrives while still
        # STARTING; consumed by start_succeeded()/start_failed().
        self._pending_stop = False
        self._pending_cancel = False

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def press(self) -> bool:
        """Attempt to accept a new trigger press. Returns True (and moves
        IDLE -> STARTING) if accepted, False if rejected (anything other
        than IDLE is in progress).
        """
        with self._lock:
            if self._state != IDLE:
                return False
            self._state = STARTING
            self._pending_stop = False
            self._pending_cancel = False
            return True

    def start_succeeded(self) -> ClaimResult:
        """Call after recorder.start() (and any other STARTING-phase work)
        completes successfully. Normally moves STARTING -> RECORDING and
        returns claimed=False (nothing more to do). If a stop was
        requested while STARTING, immediately claims FINISHING instead so
        the caller can run the finish path right away — a quick tap must
        never wedge the state machine in STARTING/RECORDING forever.
        """
        with self._lock:
            if self._state != STARTING:
                # Unexpected call (e.g. called twice); no-op.
                return ClaimResult(claimed=False, pending=False, cancel=False)
            if self._pending_stop:
                self._state = FINISHING
                cancel = self._pending_cancel
                self._pending_stop = False
                self._pending_cancel = False
                return ClaimResult(claimed=True, pending=False, cancel=cancel)
            self._state = RECORDING
            return ClaimResult(claimed=False, pending=False, cancel=False)

    def start_failed(self) -> None:
        """Call after recorder.start() raises. Reverts STARTING -> IDLE
        unconditionally (any pending stop is moot: there is no audio).
        """
        with self._lock:
            self._state = IDLE
            self._pending_stop = False
            self._pending_cancel = False

    def claim_finish(self, cancel: bool = False) -> ClaimResult:
        """Attempt to claim the transition into FINISHING, from a release,
        cancel (Esc), or auto-stop event. Mutually exclusive: only one
        caller can ever get claimed=True for a given recording.

        - RECORDING -> FINISHING: claimed=True, caller must stop the
          recorder and either enqueue (cancel=False) or discard
          (cancel=True) the audio.
        - STARTING: too early to stop the recorder (the stream isn't open
          yet); remembered as a pending stop for start_succeeded() to act
          on. Returns pending=True; caller does nothing further now.
        - IDLE or already FINISHING: rejected (claimed=False,
          pending=False) — either nothing is in progress or another path
          already claimed it.
        """
        with self._lock:
            if self._state == RECORDING:
                self._state = FINISHING
                return ClaimResult(claimed=True, pending=False, cancel=cancel)
            if self._state == STARTING:
                self._pending_stop = True
                self._pending_cancel = cancel
                return ClaimResult(claimed=False, pending=True, cancel=cancel)
            return ClaimResult(claimed=False, pending=False, cancel=cancel)

    def finished(self) -> None:
        """Call once the finish path (transcribe hand-off or discard) has
        fully completed. Moves FINISHING -> IDLE unconditionally so the
        machine can accept a new press.
        """
        with self._lock:
            self._state = IDLE
            self._pending_stop = False
            self._pending_cancel = False

    def reset(self) -> None:
        """Force back to IDLE regardless of current state (e.g. on
        unrecoverable error). Not part of the normal happy path.
        """
        with self._lock:
            self._state = IDLE
            self._pending_stop = False
            self._pending_cancel = False
