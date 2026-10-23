"""A capacity-reuse reservation never outlives the unit dispatch that made it.

Reusing a capacity-refused unit's worktree writes the unit's in-flight marker
inside `ensure_fanout_unit_worktree`, before `_dispatch_unit` has spawned
anything. Every way out of the dispatch after that point -- the unfenced
refusal, the reused worktree's preflight refusal, an exception -- must clear
it, or `fanout status` keeps reading the refused unit as in flight. A
worktree the dispatch did NOT get is the opposite case: its marker can be
another dispatch's, and the refusal must leave it alone.

The reservation is stood in for by a fake `ensure_fanout_unit_worktree` that
does what the real one does on reuse: the worktree exists, the marker is
written, `reused` comes back. Building a real capacity-refused prior run
(`five_issue_cases.capacity`, case C2) costs real processes and proves
nothing more about what `_dispatch_unit` does with the marker afterwards.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from _local_package import load_local_package

load_local_package()

from _fanout_interrupt_flag import InterruptFlagIsolatedTestCase  # noqa: E402
from omh.coding.fanout import build_fanout_contract  # noqa: E402
from omh.coding.fanout_artifacts import write_fanout_contract  # noqa: E402
from omh.coding.fanout_dispatch import dispatch_fanout, signal_safe_unit_runner  # noqa: E402
from omh.coding.inflight import read_inflight_markers, write_inflight_marker  # noqa: E402
from omh.coding.workspace_preflight import CHECK_OBJECTS_PRESENT  # noqa: E402
from omh.system.paths import OmhPaths  # noqa: E402

_GOAL = "Prove a refused unit's reservation marker is cleared."


class _Completed:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


def _git(cwd: Path, *argv: str) -> str:
    return subprocess.run(["git", *argv], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def _repo(root: Path) -> tuple[Path, str]:
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "seed.txt")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "seed")
    return repo, _git(repo, "rev-parse", "HEAD")


def _injected_runner(argv, **kwargs):
    """Git runs for real; the unit's own CLI is never reached by these tests."""
    if argv[0] == "git":
        return subprocess.run(argv, **kwargs)
    raise AssertionError(f"a refused unit spawned {argv[0]}")


def _ready(*_args: object, **_kwargs: object) -> dict[str, object]:
    return {"status": "ready"}


class ReservationMarkerTests(InterruptFlagIsolatedTestCase):
    def _setup(self, root: Path) -> tuple[OmhPaths, Path, str, dict[str, object]]:
        repo, base = _repo(root)
        paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
        contract = write_fanout_contract(paths, build_fanout_contract(_GOAL, [
            {"unit_id": "core", "title": "Core", "owner": "codex", "file_scope": ["src/"]},
        ]))
        return paths, repo, base, contract

    def _reserving_ensure(self, paths: OmhPaths, fanout_id: str, reserved: list[str]):
        def ensure(paths_arg, *, repo_root, unit_id, branch, base_sha, run_ref, **_kwargs):
            worktree = repo_root.parent / f"{repo_root.name}-fanout-{unit_id}"
            _git(repo_root, "worktree", "add", "-q", str(worktree), "-b", branch, base_sha)
            write_inflight_marker(paths_arg, fanout_id, unit_id,
                                  {"owner": "codex", "run_ref": run_ref, "worktree": str(worktree)})
            reserved.append(unit_id)
            return {"status": "reused", "observed": True, "created": False, "reused": True,
                    "worktree_path": str(worktree)}

        return ensure

    def _dispatch(self, paths, repo, base, contract, *, runner=_injected_runner, **kwargs):
        return dispatch_fanout(paths, contract, goal_text=_GOAL, repo_root=repo, base_sha=base,
                               concurrency=1, runner=runner, readiness=_ready, max_retries=0, **kwargs)

    def _markers(self, paths: OmhPaths, contract: dict[str, object]) -> list[str]:
        return [str(marker["unit_id"]) for marker in read_inflight_markers(paths, fanout_id=str(contract["fanout_id"]))]

    def test_a_reused_worktree_preflight_refusal_clears_the_marker(self) -> None:
        blocked = {
            "ok": False,
            "blocking": [CHECK_OBJECTS_PRESENT],
            "checks": [{"name": CHECK_OBJECTS_PRESENT, "ok": False, "detail": "base commit absent"}],
        }
        with TemporaryDirectory() as tmp:
            paths, repo, base, contract = self._setup(Path(tmp))
            reserved: list[str] = []
            with (
                mock.patch("omh.coding.worktree_creator.ensure_fanout_unit_worktree",
                           self._reserving_ensure(paths, str(contract["fanout_id"]), reserved)),
                mock.patch("omh.coding.fanout_dispatch.probe_workspace", return_value=blocked),
            ):
                summary = self._dispatch(paths, repo, base, contract)

            self.assertEqual(reserved, ["core"])
            (unit,) = summary["units"]
            self.assertEqual(unit["reason_code"], "workspace_preflight_blocked")
            self.assertEqual(unit["unit_state"], "data_missing")
            self.assertEqual(self._markers(paths, contract), [])

    def test_an_unfenced_refusal_after_the_reservation_clears_the_marker(self) -> None:
        with TemporaryDirectory() as tmp:
            paths, repo, base, contract = self._setup(Path(tmp))
            reserved: list[str] = []
            with (
                mock.patch("omh.coding.worktree_creator.ensure_fanout_unit_worktree",
                           self._reserving_ensure(paths, str(contract["fanout_id"]), reserved)),
                # No backend on any host: the refusal under test, not the host's fence.
                mock.patch("omh.coding.fanout_confinement.backend_available", return_value=False),
                mock.patch("omh.coding.fanout_dispatch.negotiate_session_capability", return_value=None),
            ):
                summary = self._dispatch(paths, repo, base, contract, runner=signal_safe_unit_runner)

            self.assertEqual(reserved, ["core"])
            (unit,) = summary["units"]
            self.assertEqual(unit["reason_code"], "filesystem_confinement_unavailable")
            self.assertEqual(unit["unit_state"], "permission_blocked")
            self.assertEqual(self._markers(paths, contract), [])

    def test_an_exception_after_the_reservation_clears_the_marker_and_still_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            paths, repo, base, contract = self._setup(Path(tmp))
            reserved: list[str] = []
            with (
                mock.patch("omh.coding.worktree_creator.ensure_fanout_unit_worktree",
                           self._reserving_ensure(paths, str(contract["fanout_id"]), reserved)),
                mock.patch("omh.coding.fanout_dispatch.plan_and_link_shared_artifacts",
                           side_effect=RuntimeError("injected before the spawn")),
                self.assertRaisesRegex(RuntimeError, "injected before the spawn"),
            ):
                self._dispatch(paths, repo, base, contract)

            self.assertEqual(reserved, ["core"])
            self.assertEqual(self._markers(paths, contract), [])

    def test_a_worktree_this_dispatch_did_not_get_keeps_the_other_dispatchs_marker(self) -> None:
        # The capacity reuse refuses because ANOTHER dispatch holds the unit:
        # its marker is the reason for the refusal and must survive it.
        with TemporaryDirectory() as tmp:
            paths, repo, base, contract = self._setup(Path(tmp))
            fanout_id = str(contract["fanout_id"])
            write_inflight_marker(paths, fanout_id, "core", {"owner": "codex", "worktree": "elsewhere"})
            refused = {"status": "failed", "created": False, "refusal": "capacity_reuse_unsafe",
                       "reason": "Prior workspace incarnation or in-flight state cannot be discarded",
                       "worktree_path": str(repo.parent / f"{repo.name}-fanout-core")}
            with mock.patch("omh.coding.worktree_creator.ensure_fanout_unit_worktree", return_value=refused):
                summary = self._dispatch(paths, repo, base, contract)

            (unit,) = summary["units"]
            self.assertEqual(unit["refusal"], "capacity_reuse_unsafe")
            self.assertEqual(self._markers(paths, contract), ["core"])


if __name__ == "__main__":
    unittest.main()
