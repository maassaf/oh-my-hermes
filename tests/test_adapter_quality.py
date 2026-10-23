from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from _local_package import load_local_package

load_local_package()

from omh.adapter_quality import (
    build_adapter_quality_observation,
    build_adapter_quality_delivery_card,
    link_adapter_quality_session,
    prepare_adapter_quality_delivery,
    quality_session_control,
    record_adapter_quality_delivery,
    write_adapter_quality_observation,
)
import omh.workflows.external_effect_receipts as receipts_module
from omh.external_effect_receipts import (
    read_external_effect_mint_failures,
    read_external_effect_receipts,
)
from omh.local_store import FileLockTimeout
from omh.paths import OmhPaths, resolve_paths


class AdapterQualityTests(unittest.TestCase):
    def test_web_desktop_and_app_observations_are_surface_neutral(self) -> None:
        for surface_kind in ("web", "desktop", "app"):
            observation = build_adapter_quality_observation(
                observation_id=f"{surface_kind}-quality",
                subject_id="checkout",
                surface_kind=surface_kind,
                adapter_id="hermes-adapter",
                source_revision="build-42",
                checks=[{"check_id": "checkout", "kind": "functional", "status": "pass", "expected_summary": "Checkout opens", "actual_summary": "Checkout opens", "evidence_refs": ["adapter:check-1"]}],
                layout_checks=[{"check_id": "desktop", "scope": "desktop", "status": "pass", "summary": "No overlap", "evidence_refs": ["adapter:layout-1"]}],
                metrics=[{"metric_id": "startup", "name": "Startup", "value": 120.0, "unit": "ms", "threshold": 250.0, "comparison": "lte", "status": "pass", "evidence_refs": ["adapter:metric-1"]}],
            )
            self.assertEqual(observation["surface_kind"], surface_kind)
            self.assertEqual(observation["overall_evidence_state"], "observed_no_failures")

    def test_delivery_requires_matching_prepared_card_and_stales_on_revision_change(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = resolve_paths(Path(tmp) / ".omh", Path(tmp) / ".hermes")
            observation = build_adapter_quality_observation(
                observation_id="desktop-quality",
                subject_id="checkout",
                surface_kind="desktop",
                adapter_id="hermes-adapter",
                source_revision="build-42",
                checks=[],
                layout_checks=[],
                metrics=[],
            )
            card = build_adapter_quality_delivery_card(observation, renderer_target="slack")
            preparation = prepare_adapter_quality_delivery(paths, session_id="ws-quality", card=card)
            delivery = record_adapter_quality_delivery(paths, preparation=preparation, adapter="slack-adapter", delivery_result="delivered", external_message_ref="slack:message-1")

            self.assertEqual(delivery["observation_status"], "observed")
            stale_card = build_adapter_quality_delivery_card({**observation, "source_revision": "build-43"}, renderer_target="slack")
            self.assertNotEqual(stale_card["card_fingerprint"], delivery["card_fingerprint"])

    def test_raw_log_and_unbounded_metric_do_not_validate(self) -> None:
        with self.assertRaises(ValueError):
            build_adapter_quality_observation(
                observation_id="bad-quality",
                subject_id="checkout",
                surface_kind="web",
                adapter_id="hermes-adapter",
                source_revision="build-42",
                debug_signals=[{"signal_id": "log", "kind": "error", "severity": "error", "status": "observed", "summary": "https://host/?token=secret", "evidence_refs": ["adapter:log-1"]}],
                checks=[],
                layout_checks=[],
                metrics=[],
            )

    def test_session_control_fails_closed_until_selected_observation_is_current(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = resolve_paths(Path(tmp) / ".omh", Path(tmp) / ".hermes")
            link_adapter_quality_session(paths, session_id="ws-quality", subject_id="checkout", surface_kind="web", source_revision="build-42")
            self.assertEqual(quality_session_control(paths, "ws-quality")["status"], "linked_no_observation")
            observation = write_adapter_quality_observation(paths, build_adapter_quality_observation(observation_id="web-quality", subject_id="checkout", surface_kind="web", adapter_id="hermes-adapter", source_revision="build-42", checks=[], layout_checks=[], metrics=[]))
            self.assertEqual(observation["overall_evidence_state"], "partial_observed")
            linked = link_adapter_quality_session(paths, session_id="ws-quality", subject_id="checkout", surface_kind="web", source_revision="build-42", observation_id="web-quality")
            self.assertEqual(linked["status"], "prepared_not_observed")
            self.assertEqual(quality_session_control(paths, "ws-quality")["status"], "quality_observed")
            link_adapter_quality_session(paths, session_id="ws-quality", subject_id="checkout", surface_kind="web", source_revision="build-43", observation_id="web-quality")
            self.assertEqual(quality_session_control(paths, "ws-quality")["status"], "stale")

    def test_session_control_rejects_cross_subject_or_surface_observation(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = resolve_paths(Path(tmp) / ".omh", Path(tmp) / ".hermes")
            write_adapter_quality_observation(paths, build_adapter_quality_observation(observation_id="orders-quality", subject_id="orders", surface_kind="desktop", adapter_id="hermes-adapter", source_revision="build-42", checks=[], layout_checks=[], metrics=[]))
            link_adapter_quality_session(paths, session_id="ws-quality", subject_id="checkout", surface_kind="web", source_revision="build-42", observation_id="orders-quality")
            self.assertEqual(quality_session_control(paths, "ws-quality")["status"], "stale")

    def test_delivery_and_preparation_retries_are_idempotent(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = resolve_paths(Path(tmp) / ".omh", Path(tmp) / ".hermes")
            observation = build_adapter_quality_observation(observation_id="web-quality", subject_id="checkout", surface_kind="web", adapter_id="hermes-adapter", source_revision="build-42", checks=[], layout_checks=[], metrics=[])
            card = build_adapter_quality_delivery_card(observation, renderer_target="discord")
            first = prepare_adapter_quality_delivery(paths, session_id="ws-quality", card=card)
            second = prepare_adapter_quality_delivery(paths, session_id="ws-quality", card=card)
            self.assertEqual(first, second)
            delivered = record_adapter_quality_delivery(paths, preparation=first, adapter="discord-adapter", delivery_result="delivered", external_message_ref="discord:message-1")
            self.assertEqual(delivered, record_adapter_quality_delivery(paths, preparation=first, adapter="discord-adapter", delivery_result="delivered", external_message_ref="discord:message-1"))
            with self.assertRaises(ValueError):
                record_adapter_quality_delivery(paths, preparation=first, adapter="discord-adapter", delivery_result="failed")
        with self.assertRaises(ValueError):
            build_adapter_quality_observation(
                observation_id="bad-metric",
                subject_id="checkout",
                surface_kind="web",
                adapter_id="hermes-adapter",
                source_revision="build-42",
                checks=[],
                layout_checks=[],
                metrics=[{"metric_id": "startup", "name": "Startup", "value": 120.0, "unit": "ms", "threshold": 1_000_000_001.0, "comparison": "lte", "status": "pass", "evidence_refs": ["adapter:metric-1"]}],
            )


class AdapterDeliveryReceiptRobustnessTests(unittest.TestCase):
    """A receipt store failure must not cost the delivery, or the receipt (#836).

    The defect: the receipt was minted only on the *fresh* write of the delivery
    JSON, by a call that raised. A store that could not be written therefore
    raised `FileExistsError` out of `record_adapter_quality_delivery` after the
    delivery record was already on disk, and the retry -- no longer fresh --
    returned the stored record without ever minting. The observed delivery
    stayed un-receipted forever.
    """

    def _prepared(self, paths: OmhPaths, *, renderer_target: str = "slack") -> dict[str, object]:
        observation = build_adapter_quality_observation(
            observation_id="web-quality",
            subject_id="checkout",
            surface_kind="web",
            adapter_id="hermes-adapter",
            source_revision="build-42",
            checks=[],
            layout_checks=[],
            metrics=[],
        )
        card = build_adapter_quality_delivery_card(observation, renderer_target=renderer_target)
        return prepare_adapter_quality_delivery(paths, session_id="ws-quality", card=card)

    def _deliver(self, paths: OmhPaths, preparation: dict[str, object]) -> dict[str, object]:
        return record_adapter_quality_delivery(
            paths,
            preparation=preparation,
            adapter="slack-adapter",
            delivery_result="delivered",
            external_message_ref="slack:message-1",
        )

    def test_an_unwritable_store_neither_raises_nor_loses_the_delivery(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = resolve_paths(Path(tmp) / ".omh", Path(tmp) / ".hermes")
            preparation = self._prepared(paths)
            # The exact obstruction the review reproduced: the journal
            # directory the store lives in is occupied by a regular file.
            journal = paths.runtime_journal_dir
            journal.parent.mkdir(parents=True, exist_ok=True)
            journal.write_text("not a directory", encoding="utf-8")

            delivery = self._deliver(paths, preparation)

            self.assertEqual(delivery["observation_status"], "observed")
            self.assertEqual(delivery["delivery_result"], "delivered")
            self.assertTrue(journal.is_file(), "the obstruction is still there; nothing was written")

            # Clearing the obstruction and re-reporting the same delivery mints
            # exactly one receipt -- the retry is not fresh, so a fresh-only
            # mint would leave this delivery permanently un-receipted.
            journal.unlink()
            replayed = self._deliver(paths, preparation)
            self.assertEqual(replayed, delivery)

            receipts = read_external_effect_receipts(paths)
            self.assertEqual(len(receipts), 1)
            self.assertEqual(receipts[0]["action"], "message_sent")
            self.assertEqual(receipts[0]["acting_surface"], "adapter_quality_delivery")
            self.assertEqual(receipts[0]["observed_result"], "succeeded")

    def test_a_busy_store_lock_records_the_unminted_effect_instead_of_raising(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = resolve_paths(Path(tmp) / ".omh", Path(tmp) / ".hermes")
            preparation = self._prepared(paths)
            store_path = paths.runtime_external_effect_receipts_path

            # Only the receipt store's own lock is busy. The mint-failure log is
            # a separate file behind a separate sidecar lock, and it is exactly
            # the record that has to survive the store being unwritable, so
            # timing out every lock in the module would obstruct the thing under
            # test as well as the obstruction.
            real_file_lock = receipts_module.file_lock

            def busy_store_lock(path: Path, **kwargs: object):
                if path == store_path:
                    raise FileLockTimeout("could not acquire lock")
                return real_file_lock(path, **kwargs)

            with mock.patch(
                "omh.workflows.external_effect_receipts.file_lock",
                side_effect=busy_store_lock,
            ):
                delivery = self._deliver(paths, preparation)

            self.assertEqual(delivery["observation_status"], "observed")
            self.assertEqual(read_external_effect_receipts(paths), [])

            # Observable: the effect that went unreceipted is on record beside
            # the store, and `omh runtime receipts` reports it.
            failures = read_external_effect_mint_failures(store_path)
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0]["outcome"], "not_written")
            self.assertEqual(failures[0]["acting_surface"], "adapter_quality_delivery")
            self.assertEqual(failures[0]["effect_id"], f"delivery:{preparation['preparation_id']}")

            replayed = self._deliver(paths, preparation)

            self.assertEqual(replayed, delivery)
            self.assertEqual(len(read_external_effect_receipts(paths)), 1)

    def test_repeated_reports_of_one_delivery_mint_exactly_one_receipt(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = resolve_paths(Path(tmp) / ".omh", Path(tmp) / ".hermes")
            preparation = self._prepared(paths)

            for _ in range(4):
                self._deliver(paths, preparation)

            self.assertEqual(len(read_external_effect_receipts(paths)), 1)
            self.assertEqual(read_external_effect_mint_failures(paths.runtime_external_effect_receipts_path), [])

    def test_a_delivery_receipt_carries_no_run_and_is_listed_as_run_less(self) -> None:
        """A delivery belongs to a session, not to whatever run that session
        happens to be working on, so binding one would be an invented
        relationship. Visibility comes from being listed as run-less."""
        with TemporaryDirectory() as tmp:
            paths = resolve_paths(Path(tmp) / ".omh", Path(tmp) / ".hermes")
            self._deliver(paths, self._prepared(paths))

            self.assertEqual(read_external_effect_receipts(paths)[0]["run_id"], "")
            self.assertEqual(len(read_external_effect_receipts(paths, run_id="")), 1)


if __name__ == "__main__":
    unittest.main()
