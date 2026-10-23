"""Contract tests for `language_diagnostic_evidence/v1`.

The three acceptance criteria of issue #822 each get a class, and each is
followed by the guards that keep it from being satisfied by accident:

- AC1 -- a clean result is labelled only as a fresh language-diagnostic check.
- AC2 -- introduced diagnostics are attributable to a workspace and interval.
- AC3 -- missing or stale diagnostics cannot satisfy verification.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import unittest

from _cli_harness import run_cli
from omh.quality import language_diagnostic_evidence as module
from omh.quality.language_diagnostic_evidence import (
    LANGUAGE_DIAGNOSTIC_CHECK_STATES,
    LANGUAGE_DIAGNOSTIC_CLAIMS,
    LANGUAGE_DIAGNOSTIC_CLAIM_BOUNDARY,
    LANGUAGE_DIAGNOSTIC_EVIDENCE_KEYS,
    LANGUAGE_DIAGNOSTIC_EVIDENCE_SCHEMA_VERSION,
    LANGUAGE_DIAGNOSTIC_ITEM_KEYS,
    LANGUAGE_DIAGNOSTIC_NOT_EVIDENCE_FOR,
    LANGUAGE_DIAGNOSTIC_OWNERS,
    LANGUAGE_DIAGNOSTIC_SUPPORTABLE_CLAIMS,
    LANGUAGE_DIAGNOSTIC_USABLE_VERDICTS,
    LANGUAGE_DIAGNOSTIC_VERDICTS,
    MAX_DIAGNOSTICS,
    LanguageDiagnosticEvidenceError,
    build_language_diagnostic_evidence,
    language_diagnostic_claim_support,
    language_diagnostic_supports_claim,
    validate_language_diagnostic_evidence,
)


def _record(**overrides: object) -> dict[str, object]:
    """A fresh, attributable, clean check unless a test says otherwise."""
    fields: dict[str, object] = {
        "owner": "claude-code",
        "provider": "pyright",
        "workspace_id": "local/omh",
        "baseline_revision": "rev-baseline",
        "end_revision": "rev-end",
        "diagnostics_revision": "rev-end",
    }
    fields.update(overrides)
    return build_language_diagnostic_evidence(**fields)  # type: ignore[arg-type]


def _diagnostic(**overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "severity": "error",
        "code": "reportUndefinedVariable",
        "path": "src/quality/example.py",
        "line": 41,
        "character": 8,
        "source": "pyright",
    }
    item.update(overrides)
    return item


def _every_reachable_record() -> dict[str, dict[str, object]]:
    """One record per verdict, so an invariant can be asserted over all of them."""
    records = {
        "no_new_diagnostics_observed": _record(),
        "new_diagnostics_observed": _record(introduced=[_diagnostic()]),
        "attribution_unavailable": _record(baseline_revision=""),
        "stale_diagnostics": _record(diagnostics_revision="rev-baseline"),
        "freshness_unknown": _record(diagnostics_revision=""),
        "provider_unsupported": _record(check_state="unsupported"),
        "provider_failed": _record(check_state="failed"),
        "not_observed": _record(check_state="not_observed"),
    }
    assert sorted(records) == sorted(LANGUAGE_DIAGNOSTIC_VERDICTS), "a verdict has no reachable record"
    for verdict, record in records.items():
        assert record["verdict"] == verdict, f"{verdict} fixture derived {record['verdict']}"
    return records


class CleanIsOnlyAFreshLanguageDiagnosticCheckTests(unittest.TestCase):
    """AC1: a clean result is labelled a language-diagnostic check, never verification."""

    def test_a_clean_check_says_no_new_diagnostics_were_observed(self) -> None:
        record = _record()

        self.assertEqual(record["verdict"], "no_new_diagnostics_observed")
        self.assertIn("No new diagnostics were observed", record["summary_label"])
        self.assertIn("fresh language-diagnostic check", record["summary_label"])

    def test_no_summary_label_ever_reads_as_verification(self) -> None:
        # The label is the sentence a human quotes, so the overstatement this
        # contract exists to prevent would appear here first.
        for verdict, record in _every_reachable_record().items():
            label = str(record["summary_label"]).lower()
            with self.subTest(verdict=verdict):
                self.assertIn("language-diagnostic", label)
                for word in ("verified", "verification", "passed", "all clear", "ready to merge", "safe to merge"):
                    self.assertNotIn(word, label)

    def test_a_clean_record_backs_no_claim_other_than_its_own(self) -> None:
        clean = _record()

        self.assertTrue(language_diagnostic_supports_claim(clean, "fresh_language_diagnostic_check"))
        for claim in LANGUAGE_DIAGNOSTIC_CLAIMS:
            if claim in LANGUAGE_DIAGNOSTIC_SUPPORTABLE_CLAIMS:
                continue
            with self.subTest(claim=claim):
                self.assertFalse(language_diagnostic_supports_claim(clean, claim))

    def test_no_record_of_any_verdict_backs_verification_tests_review_ci_or_merge(self) -> None:
        for verdict, record in _every_reachable_record().items():
            for claim in LANGUAGE_DIAGNOSTIC_CLAIMS:
                if claim in LANGUAGE_DIAGNOSTIC_SUPPORTABLE_CLAIMS:
                    continue
                with self.subTest(verdict=verdict, claim=claim):
                    self.assertFalse(language_diagnostic_supports_claim(record, claim))

    def test_a_clean_result_cannot_be_promoted_into_a_verification_claim(self) -> None:
        # Three promotion routes, all closed. This is the failure mode issue
        # #822 names, so each is asserted rather than assumed.
        clean = _record()

        # 1. Asking the predicate for verification. There is no argument that
        #    turns this True, including the claim it does support.
        self.assertFalse(language_diagnostic_supports_claim(clean, "verification"))
        self.assertNotIn("verification", LANGUAGE_DIAGNOSTIC_SUPPORTABLE_CLAIMS)

        # 2. Editing the record on the way to a status surface. The verdict
        #    vocabulary has no verification member, and validation re-derives
        #    the verdict rather than trusting it.
        self.assertNotIn("verification", " ".join(LANGUAGE_DIAGNOSTIC_VERDICTS))
        forged = dict(clean, verdict="new_diagnostics_observed")
        self.assertIn(
            "verdict must be derived as 'no_new_diagnostics_observed'",
            " ".join(validate_language_diagnostic_evidence(forged)),
        )
        self.assertFalse(language_diagnostic_supports_claim(forged, "fresh_language_diagnostic_check"))

        # 3. Rewriting the prose while leaving the verdict alone.
        relabelled = dict(clean, summary_label="Verified: the workspace passed every check.")
        self.assertIn(
            "summary_label must be the derived label",
            " ".join(validate_language_diagnostic_evidence(relabelled)),
        )
        self.assertFalse(language_diagnostic_supports_claim(relabelled, "fresh_language_diagnostic_check"))

    def test_the_record_names_every_claim_it_cannot_settle(self) -> None:
        record = _record()

        self.assertEqual(record["not_evidence_for"], list(LANGUAGE_DIAGNOSTIC_NOT_EVIDENCE_FOR))
        for claim in ("verification", "compilation", "test_execution", "review", "ci", "merge_readiness", "merge"):
            with self.subTest(claim=claim):
                self.assertIn(claim, record["not_evidence_for"])
        self.assertEqual(record["claim_boundary"], LANGUAGE_DIAGNOSTIC_CLAIM_BOUNDARY)
        for phrase in ("compilation", "test", "verification", "review", "CI", "merge"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, LANGUAGE_DIAGNOSTIC_CLAIM_BOUNDARY)

    def test_claim_support_partitions_every_claim(self) -> None:
        support = language_diagnostic_claim_support(_record())

        self.assertEqual(support["supported_claims"], ["fresh_language_diagnostic_check"])
        self.assertEqual(
            sorted([*support["supported_claims"], *support["unsupported_claims"]]),
            sorted(LANGUAGE_DIAGNOSTIC_CLAIMS),
        )

    def test_an_unknown_claim_name_is_refused_rather_than_defaulted(self) -> None:
        self.assertFalse(language_diagnostic_supports_claim(_record(), "everything_is_fine"))


class IntroducedDiagnosticsAreAttributableTests(unittest.TestCase):
    """AC2: an introduced diagnostic names the workspace and the interval it came from."""

    def test_an_introduced_diagnostic_carries_its_workspace_and_revision_interval(self) -> None:
        record = _record(introduced=[_diagnostic()], changed_paths=["src/quality/example.py"])

        self.assertEqual(record["verdict"], "new_diagnostics_observed")
        self.assertEqual(record["attribution"], "attributable")
        self.assertEqual(record["workspace_id"], "local/omh")
        self.assertEqual(record["baseline_revision"], "rev-baseline")
        self.assertEqual(record["end_revision"], "rev-end")
        self.assertEqual(record["introduced_count"], 1)
        self.assertIn("workspace local/omh between rev-baseline and rev-end", record["summary_label"])

    def test_a_missing_interval_endpoint_marks_attribution_unavailable(self) -> None:
        for missing in ("workspace_id", "baseline_revision", "end_revision"):
            record = _record(**{missing: "", "introduced": [_diagnostic()]})
            with self.subTest(missing=missing):
                self.assertEqual(record["attribution"], "unavailable")
                self.assertEqual(record["verdict"], "attribution_unavailable")
                self.assertFalse(language_diagnostic_supports_claim(record, "fresh_language_diagnostic_check"))
                self.assertIn("cannot be attributed", record["summary_label"])

    def test_resolved_and_introduced_are_reported_separately(self) -> None:
        record = _record(
            introduced=[_diagnostic(line=10)],
            resolved=[_diagnostic(line=20), _diagnostic(line=30)],
        )

        self.assertEqual(record["introduced_count"], 1)
        self.assertEqual(record["resolved_count"], 2)
        self.assertEqual([item["line"] for item in record["resolved"]], [20, 30])

    def test_a_diagnostic_is_normalized_to_severity_code_path_and_location(self) -> None:
        record = _record(introduced=[_diagnostic(severity="WARNING", path="src\\quality\\example.py")])
        item = record["introduced"][0]

        self.assertEqual(sorted(item), sorted(LANGUAGE_DIAGNOSTIC_ITEM_KEYS))
        self.assertEqual(item["severity"], "warning")
        self.assertEqual(item["path"], "src/quality/example.py")
        self.assertEqual(item["line"], 41)
        self.assertEqual(item["character"], 8)

    def test_a_diagnostic_carrying_a_source_body_is_refused_by_key_name(self) -> None:
        for body_key in ("message", "snippet", "source_text", "fix"):
            with self.subTest(body_key=body_key):
                with self.assertRaises(LanguageDiagnosticEvidenceError) as caught:
                    _record(introduced=[dict(_diagnostic(), **{body_key: "x = undefined_name  # boom"})])
                self.assertIn("metadata only", str(caught.exception))
                self.assertIn(body_key, str(caught.exception))

    def test_a_path_outside_the_workspace_is_refused(self) -> None:
        for path in ("/etc/passwd", "C:\\Users\\me\\secret.py", "../outside/other.py"):
            with self.subTest(path=path):
                with self.assertRaises(LanguageDiagnosticEvidenceError):
                    _record(changed_paths=[path])
                with self.assertRaises(LanguageDiagnosticEvidenceError):
                    _record(introduced=[_diagnostic(path=path)])

    def test_an_unsupported_severity_or_owner_is_refused(self) -> None:
        with self.assertRaises(LanguageDiagnosticEvidenceError):
            _record(introduced=[_diagnostic(severity="catastrophe")])
        with self.assertRaises(LanguageDiagnosticEvidenceError):
            _record(owner="some-vendor-agent")
        self.assertNotIn("unknown", LANGUAGE_DIAGNOSTIC_OWNERS)

    def test_a_non_integer_position_is_refused(self) -> None:
        for value in ("41", 4.5, True, -1):
            with self.subTest(value=value):
                with self.assertRaises(LanguageDiagnosticEvidenceError):
                    _record(introduced=[_diagnostic(line=value)])

    def test_diagnostic_and_path_lists_are_bounded(self) -> None:
        oversized = [_diagnostic(line=index) for index in range(MAX_DIAGNOSTICS + 1)]
        with self.assertRaises(LanguageDiagnosticEvidenceError) as caught:
            _record(introduced=oversized)
        self.assertIn(str(MAX_DIAGNOSTICS), str(caught.exception))


class MissingOrStaleCannotSatisfyVerificationTests(unittest.TestCase):
    """AC3: a check that did not happen, or happened elsewhere, settles nothing."""

    def test_diagnostics_observed_off_the_interval_end_are_stale(self) -> None:
        record = _record(diagnostics_revision="rev-baseline")

        self.assertEqual(record["freshness"], "stale")
        self.assertEqual(record["verdict"], "stale_diagnostics")
        self.assertFalse(language_diagnostic_supports_claim(record, "fresh_language_diagnostic_check"))
        self.assertIn("stale", record["summary_label"])

    def test_a_stale_check_with_zero_diagnostics_is_still_not_a_clean_result(self) -> None:
        # The dangerous case: nothing to report, reported from the wrong
        # revision. It must not collapse into `no_new_diagnostics_observed`.
        stale_clean = _record(diagnostics_revision="rev-baseline", introduced=[])

        self.assertEqual(stale_clean["introduced_count"], 0)
        self.assertNotEqual(stale_clean["verdict"], "no_new_diagnostics_observed")
        self.assertNotIn(stale_clean["verdict"], LANGUAGE_DIAGNOSTIC_USABLE_VERDICTS)
        self.assertFalse(language_diagnostic_supports_claim(stale_clean, "fresh_language_diagnostic_check"))

    def test_diagnostics_without_a_stated_revision_have_unknown_freshness(self) -> None:
        record = _record(diagnostics_revision="")

        self.assertEqual(record["freshness"], "unknown")
        self.assertEqual(record["verdict"], "freshness_unknown")
        self.assertFalse(language_diagnostic_supports_claim(record, "fresh_language_diagnostic_check"))

    def test_unsupported_failed_and_not_observed_states_are_preserved_not_collapsed(self) -> None:
        expected = {
            "unsupported": "provider_unsupported",
            "failed": "provider_failed",
            "not_observed": "not_observed",
        }
        for state, verdict in expected.items():
            record = _record(check_state=state)
            with self.subTest(state=state):
                self.assertEqual(record["check_state"], state)
                self.assertEqual(record["verdict"], verdict)
                self.assertEqual(record["freshness"], "unknown")
                self.assertEqual(record["attribution"], "unavailable")
                self.assertFalse(language_diagnostic_supports_claim(record, "fresh_language_diagnostic_check"))
        self.assertEqual(sorted([*expected, "observed"]), sorted(LANGUAGE_DIAGNOSTIC_CHECK_STATES))

    def test_only_the_two_fresh_attributable_verdicts_are_usable(self) -> None:
        for verdict, record in _every_reachable_record().items():
            with self.subTest(verdict=verdict):
                self.assertEqual(
                    language_diagnostic_supports_claim(record, "fresh_language_diagnostic_check"),
                    verdict in LANGUAGE_DIAGNOSTIC_USABLE_VERDICTS,
                )


class LanguageDiagnosticRecordShapeTests(unittest.TestCase):
    def test_the_key_set_is_exact_and_closed(self) -> None:
        record = _record()

        self.assertEqual(sorted(record), sorted(LANGUAGE_DIAGNOSTIC_EVIDENCE_KEYS))
        self.assertEqual(record["schema_version"], LANGUAGE_DIAGNOSTIC_EVIDENCE_SCHEMA_VERSION)
        self.assertEqual(record["privacy"], "metadata_only")
        self.assertEqual(validate_language_diagnostic_evidence(record), [])

    def test_an_extra_or_missing_key_is_reported(self) -> None:
        record = _record()

        self.assertIn(
            "unsupported keys: ['lsp_transcript']",
            " ".join(validate_language_diagnostic_evidence(dict(record, lsp_transcript="..."))),
        )
        without_verdict = {key: value for key, value in record.items() if key != "verdict"}
        self.assertIn("missing keys: ['verdict']", " ".join(validate_language_diagnostic_evidence(without_verdict)))

    def test_a_reordered_or_duplicated_diagnostic_list_is_reported(self) -> None:
        record = _record(introduced=[_diagnostic(line=10), _diagnostic(line=20)])
        first, second = record["introduced"]

        for broken in ([second, first], [first, first]):
            with self.subTest(broken=broken):
                self.assertIn(
                    "introduced must be sorted and unique",
                    " ".join(validate_language_diagnostic_evidence(dict(record, introduced=broken))),
                )

    def test_a_rejected_diagnostic_entry_reports_its_own_reason_only(self) -> None:
        record = _record(introduced=[_diagnostic(line=10), _diagnostic(line=20)])
        reported = validate_language_diagnostic_evidence(
            dict(record, introduced=[dict(record["introduced"][0], severity="catastrophe")], introduced_count=1)
        )

        self.assertTrue(any("severity is unsupported" in error for error in reported))
        self.assertFalse(any("sorted and unique" in error for error in reported))

    def test_a_non_object_payload_is_reported_rather_than_raising(self) -> None:
        self.assertEqual(
            validate_language_diagnostic_evidence(["not", "a", "record"]),
            ["language_diagnostic_evidence must be an object"],
        )
        self.assertFalse(language_diagnostic_supports_claim(None, "fresh_language_diagnostic_check"))

    def test_the_record_is_deterministic_and_holds_no_clock(self) -> None:
        first = _record(
            changed_paths=["src/b.py", "src/a.py"],
            introduced=[_diagnostic(line=20), _diagnostic(line=10)],
            observed_at="2026-08-09T00:00:00Z",
        )
        # Same observation, different report order and a later timestamp.
        second = _record(
            changed_paths=["src/a.py", "src/b.py", "src/a.py"],
            introduced=[_diagnostic(line=10), _diagnostic(line=20)],
            observed_at="2027-01-01T00:00:00Z",
        )

        self.assertEqual(first["record_id"], second["record_id"])
        self.assertEqual(first["changed_paths"], ["src/a.py", "src/b.py"])
        self.assertEqual({key: value for key, value in first.items() if key != "observed_at"},
                         {key: value for key, value in second.items() if key != "observed_at"})

    def test_the_record_id_changes_when_the_observation_changes(self) -> None:
        clean = _record()
        dirty = _record(introduced=[_diagnostic()])

        self.assertNotEqual(clean["record_id"], dirty["record_id"])
        self.assertTrue(str(clean["record_id"]).startswith("langdiag-"))

    def test_a_control_character_or_secret_shaped_reference_is_refused(self) -> None:
        for field, value in (
            ("provider", "pyright\x1b[2K\r"),
            ("workspace_id", "ws\nrogue"),
            ("config_digest", "ghp_0123456789abcdefghij"),
        ):
            with self.subTest(field=field):
                with self.assertRaises(LanguageDiagnosticEvidenceError):
                    _record(**{field: value})


class LanguageDiagnosticCliTests(unittest.TestCase):
    _ARGS = [
        "quality-evidence", "language-diagnostics",
        "--owner", "claude-code", "--provider", "pyright",
        "--workspace", "local/omh",
        "--baseline-revision", "rev-baseline", "--end-revision", "rev-end",
    ]

    def test_json_output_carries_the_record_and_its_claim_support(self) -> None:
        status, stdout, stderr = run_cli([
            *self._ARGS, "--diagnostics-revision", "rev-end",
            "--changed-paths", '["src/quality/example.py"]',
            "--introduced", '[{"severity":"error","code":"E1","path":"src/quality/example.py","line":4,"character":1,"source":"pyright"}]',
        ])

        self.assertEqual(status, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["record"]["verdict"], "new_diagnostics_observed")
        self.assertEqual(payload["record"]["introduced_count"], 1)
        self.assertEqual(payload["claim_support"]["supported_claims"], ["fresh_language_diagnostic_check"])
        self.assertIn("verification", payload["claim_support"]["unsupported_claims"])

    def test_plain_text_is_the_default_and_states_what_the_check_does_not_support(self) -> None:
        status, stdout, stderr = run_cli(
            [*self._ARGS, "--diagnostics-revision", "rev-end"], output_json=False
        )

        self.assertEqual(status, 0, stderr)
        self.assertFalse(stdout.lstrip().startswith("{"))
        self.assertIn("Verdict: no_new_diagnostics_observed", stdout)
        self.assertIn("No new diagnostics were observed", stdout)
        self.assertIn("Does not support: verification", stdout)
        self.assertNotIn("Verified", stdout)

    def test_a_stale_check_reports_stale_on_the_cli(self) -> None:
        status, stdout, stderr = run_cli(
            [*self._ARGS, "--diagnostics-revision", "rev-baseline"], output_json=False
        )

        self.assertEqual(status, 0, stderr)
        self.assertIn("Verdict: stale_diagnostics", stdout)
        self.assertIn("Supports: nothing", stdout)

    def test_a_refused_observation_exits_non_zero_with_the_reason(self) -> None:
        status, _, stderr = run_cli([
            *self._ARGS, "--diagnostics-revision", "rev-end",
            "--introduced", '[{"severity":"error","path":"src/a.py","message":"undefined name"}]',
        ])

        self.assertNotEqual(status, 0)
        self.assertIn("metadata only", stderr)


class LanguageDiagnosticModuleBoundaryTests(unittest.TestCase):
    """The contract records diagnostics; it must never be able to produce them."""

    def test_the_module_imports_nothing_that_could_start_a_server_or_touch_disk(self) -> None:
        # Derived from the import graph rather than from prose, so the module
        # docstring naming a socket cannot pass or fail the check.
        source = Path(module.__file__ or "").read_text(encoding="utf-8")
        imported: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level:
                imported.add((node.module or "").split(".")[0])

        self.assertEqual(imported, {"__future__", "hashlib", "re", "typing"})
        for capability in ("subprocess", "socket", "urllib", "http", "pathlib", "os", "shutil", "time", "datetime"):
            with self.subTest(capability=capability):
                self.assertNotIn(capability, imported)


if __name__ == "__main__":
    unittest.main()
