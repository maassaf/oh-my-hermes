"""One setUp for every test class whose tests can set the fanout interrupt flag (#1482).

`dispatch_fanout` sets the process-global `_INTERRUPT_FLAG` on an interrupt or
a dispatcher exception and leaves it for the next dispatch entry, and
`terminate_live_unit_groups` sets it too. A later test in the same process
that spawns through `signal_safe_unit_runner` directly is then SIGTERMed on
register-then-check. CI shard plans reorder tests run to run, so the leak shows
only when such a test lands after a leaker. Clearing only before each test is
not enough: the flag a test sets must not outlive it either.
"""

from __future__ import annotations

import unittest

from _local_package import load_local_package

load_local_package()

from omh.coding.fanout_dispatch import _INTERRUPT_FLAG  # noqa: E402


class InterruptFlagIsolatedTestCase(unittest.TestCase):
    """Each test starts with the interrupt flag clear and leaves it clear."""

    def setUp(self) -> None:
        super().setUp()
        _INTERRUPT_FLAG.clear()
        self.addCleanup(_INTERRUPT_FLAG.clear)
