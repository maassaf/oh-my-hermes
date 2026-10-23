"""The interrupt flag must not outlive a test that sets it (#1482).

`dispatch_fanout` sets the process-global `_INTERRUPT_FLAG` on an interrupt or
a dispatcher exception and leaves it for the next dispatch entry. A later test
in the same process that spawns through `signal_safe_unit_runner` directly is
then SIGTERMed on register-then-check. CI shard plans reorder tests run to run,
so the leak only shows when a leaker lands before such a test.

This covers the tests named in `_LEAKERS`, no others: a new test that sets the
flag is caught only once it is listed here. Each listed test was observed
setting the flag with the shared guard's cleanup disabled. Deriving the list
instead would re-run every test of every class that inherits
`InterruptFlagIsolatedTestCase` -- about a minute when this was written,
against a few seconds for the list -- so the list stays hand-kept.
"""

from __future__ import annotations

import io
import unittest

from omh.coding.fanout_dispatch import _INTERRUPT_FLAG

# Tests known to set the flag: dispatch_fanout's interrupt or exception branch,
# or a direct `terminate_live_unit_groups`.
_LEAKERS = (
    "test_fanout_admission.FanoutInterruptedAdmissionTests"
    ".test_interrupt_cleanup_observes_a_collected_clean_completion_once",
    "test_fanout_cancellation_states.InterruptedRunningUnitTests"
    ".test_a_signal_killed_unit_records_cancelled_rather_than_a_model_failure",
    "test_fanout_capacity_reservation.ReservationMarkerTests"
    ".test_an_exception_after_the_reservation_clears_the_marker_and_still_raises",
    "test_fanout_dispatch.FanoutUnitResultIntakeTests"
    ".test_d5_dispatcher_exception_preserves_summary_and_reraises",
    "test_fanout_dispatch.FanoutUnitResultIntakeTests"
    ".test_d5_dispatcher_fault_keeps_completed_sibling_evidence",
    "test_fanout_signal_safety.AsyncSignalTests"
    ".test_sigterm_mid_flight_kills_groups_and_blocks_new_spawns",
    "test_fanout_signal_safety.InterruptedDispatchTests"
    ".test_keyboard_interrupt_marks_unstarted_units_and_returns_the_summary",
    "test_fanout_signal_safety.InterruptedDispatchTests"
    ".test_system_exit_re_raises_after_the_summary_is_written",
    "test_fanout_signal_safety.SignalSafeRunnerTests"
    ".test_terminate_live_unit_groups_reaps_a_registered_unit",
    "test_fanout_executor_sessions.SessionsIntegration"
    ".test_s1_observer_failure_preserves_historical_id_without_resume",
)


class InterruptFlagIsolationTests(unittest.TestCase):
    def test_known_leakers_leave_the_flag_clear(self) -> None:
        self.addCleanup(_INTERRUPT_FLAG.clear)
        for name in _LEAKERS:
            with self.subTest(test=name):
                suite = unittest.defaultTestLoader.loadTestsFromName(name)
                result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
                self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
                self.assertFalse(
                    _INTERRUPT_FLAG.is_set(),
                    f"{name} left the process-global interrupt flag set",
                )


if __name__ == "__main__":
    unittest.main()
