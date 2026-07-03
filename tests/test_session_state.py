"""Unit tests for flowlocal.session_state.SessionState — the pure
IDLE/STARTING/RECORDING/FINISHING state machine extracted from App so the
delicate press/release/cancel/auto-stop race logic is testable without
tkinter, pynput, or win32.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flowlocal.session_state import (  # noqa: E402
    ClaimResult,
    SessionState,
    FINISHING,
    IDLE,
    RECORDING,
    STARTING,
)


class PressTest(unittest.TestCase):
    def test_press_from_idle_is_accepted(self):
        s = SessionState()
        self.assertTrue(s.press())
        self.assertEqual(s.state, STARTING)

    def test_press_while_starting_is_rejected(self):
        s = SessionState()
        s.press()
        self.assertFalse(s.press())
        self.assertEqual(s.state, STARTING)

    def test_press_while_recording_is_rejected(self):
        s = SessionState()
        s.press()
        s.start_succeeded()
        self.assertFalse(s.press())
        self.assertEqual(s.state, RECORDING)

    def test_press_while_finishing_is_rejected(self):
        s = SessionState()
        s.press()
        s.start_succeeded()
        s.claim_finish()
        self.assertFalse(s.press())
        self.assertEqual(s.state, FINISHING)


class StartSucceededTest(unittest.TestCase):
    def test_normal_case_moves_to_recording(self):
        s = SessionState()
        s.press()
        result = s.start_succeeded()
        self.assertEqual(s.state, RECORDING)
        self.assertEqual(result, ClaimResult(claimed=False, pending=False, cancel=False))

    def test_release_during_starting_sets_pending_and_is_claimed_on_success(self):
        s = SessionState()
        s.press()
        release_result = s.claim_finish()
        # Too early to stop the recorder: recorded as pending, not claimed.
        self.assertEqual(release_result, ClaimResult(claimed=False, pending=True, cancel=False))
        self.assertEqual(s.state, STARTING)

        # The starter thread's recorder.start() now completes: the pending
        # stop must be claimed immediately so a quick tap never wedges the
        # machine in RECORDING forever.
        start_result = s.start_succeeded()
        self.assertEqual(start_result, ClaimResult(claimed=True, pending=False, cancel=False))
        self.assertEqual(s.state, FINISHING)

    def test_cancel_during_starting_is_claimed_as_cancel_on_success(self):
        s = SessionState()
        s.press()
        s.claim_finish(cancel=True)
        start_result = s.start_succeeded()
        self.assertEqual(start_result, ClaimResult(claimed=True, pending=False, cancel=True))
        self.assertEqual(s.state, FINISHING)


class StartFailedTest(unittest.TestCase):
    def test_reverts_to_idle(self):
        s = SessionState()
        s.press()
        s.start_failed()
        self.assertEqual(s.state, IDLE)

    def test_reverts_to_idle_even_with_pending_stop(self):
        s = SessionState()
        s.press()
        s.claim_finish()
        s.start_failed()
        self.assertEqual(s.state, IDLE)
        # A press after this must be freshly accepted, not haunted by the
        # stale pending-stop flag.
        self.assertTrue(s.press())


class ClaimFinishTest(unittest.TestCase):
    def test_claim_from_recording_succeeds(self):
        s = SessionState()
        s.press()
        s.start_succeeded()
        result = s.claim_finish()
        self.assertEqual(result, ClaimResult(claimed=True, pending=False, cancel=False))
        self.assertEqual(s.state, FINISHING)

    def test_double_claim_of_finishing_is_rejected(self):
        s = SessionState()
        s.press()
        s.start_succeeded()
        first = s.claim_finish()
        second = s.claim_finish()
        self.assertTrue(first.claimed)
        self.assertEqual(second, ClaimResult(claimed=False, pending=False, cancel=False))

    def test_claim_from_idle_is_rejected(self):
        s = SessionState()
        result = s.claim_finish()
        self.assertEqual(result, ClaimResult(claimed=False, pending=False, cancel=False))
        self.assertEqual(s.state, IDLE)

    def test_cancel_vs_autostop_exclusivity_only_one_wins(self):
        s = SessionState()
        s.press()
        s.start_succeeded()
        # Simulate cancel (Esc) and auto-stop racing: only one may claim.
        cancel_result = s.claim_finish(cancel=True)
        autostop_result = s.claim_finish(cancel=False)
        self.assertTrue(cancel_result.claimed)
        self.assertTrue(cancel_result.cancel)
        self.assertFalse(autostop_result.claimed)

    def test_release_vs_cancel_exclusivity_only_one_wins(self):
        s = SessionState()
        s.press()
        s.start_succeeded()
        release_result = s.claim_finish(cancel=False)
        cancel_result = s.claim_finish(cancel=True)
        self.assertTrue(release_result.claimed)
        self.assertFalse(release_result.cancel)
        self.assertFalse(cancel_result.claimed)


class FinishedTest(unittest.TestCase):
    def test_finished_returns_to_idle_and_allows_new_press(self):
        s = SessionState()
        s.press()
        s.start_succeeded()
        s.claim_finish()
        s.finished()
        self.assertEqual(s.state, IDLE)
        self.assertTrue(s.press())


class ResetTest(unittest.TestCase):
    def test_reset_forces_idle_from_any_state(self):
        s = SessionState()
        s.press()
        s.start_succeeded()
        s.reset()
        self.assertEqual(s.state, IDLE)
        self.assertTrue(s.press())


class FullLifecycleTest(unittest.TestCase):
    def test_normal_press_record_release_finish_cycle(self):
        s = SessionState()
        self.assertTrue(s.press())
        self.assertEqual(s.start_succeeded(), ClaimResult(claimed=False, pending=False, cancel=False))
        self.assertEqual(s.state, RECORDING)
        result = s.claim_finish()
        self.assertTrue(result.claimed)
        s.finished()
        self.assertEqual(s.state, IDLE)

    def test_quick_tap_never_wedges_state(self):
        """Press immediately followed by release, before recorder.start()
        has completed on the starter thread — the classic race this state
        machine exists to fix. Must still end up transcribing (FINISHING)
        rather than stuck in STARTING/RECORDING forever."""
        s = SessionState()
        s.press()
        pending = s.claim_finish()
        self.assertTrue(pending.pending)
        result = s.start_succeeded()
        self.assertTrue(result.claimed)
        self.assertEqual(s.state, FINISHING)
        s.finished()
        self.assertEqual(s.state, IDLE)


if __name__ == "__main__":
    unittest.main()
