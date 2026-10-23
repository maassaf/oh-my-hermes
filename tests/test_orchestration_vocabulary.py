"""Contract tests for the orchestration vocabulary (plan §5.5, §11.3 PR A).

The vocabulary is a pure data contract: these tests pin its schema version,
the exact nine-element term-id set, the allowed audiences, resolvable
machinery references, the vocabulary separation between Hermes mixture and
Maestro, executor neutrality, and the structural subset relation between
`EXTERNAL_CLI_PROFILES` and the maestro facade's serviceable profiles.
"""

from __future__ import annotations

import importlib
import unittest

from _local_package import load_local_package

load_local_package()

from omh.coding.executors import EXTERNAL_CLI_PROFILES
from omh.coding.maestro.facade import _EXTERNAL_PROFILES
from omh.coding.orchestration_vocabulary import (
    CHOOSE_EXECUTOR_REASON,
    ORCHESTRATION_VOCABULARY_AUDIENCES,
    ORCHESTRATION_VOCABULARY_SCHEMA_VERSION,
    orchestration_vocabulary_payload,
)
from omh.routing.action_copy import NEXT_ACTION_LABELS


EXPECTED_TERM_IDS = {
    "hermes_harness",
    "hermes_mixture",
    "hermes_subagent",
    "coding_owner",
    "maestro",
    "external_runtime_handoff",
    "fanout_dispatch",
    "prepared",
    "observed",
}

# Mixture is per-role model binding for Hermes subagents; Maestro hands an
# external CLI the work. Neither entry may describe its units with the
# worker/team vocabulary, or the two paths blur back together.
_WORKER_TEAM_TOKENS = {"worker", "workers", "team", "teams"}

_EXTERNAL_CLI_NAME_MENTIONS = ("codex", "claude code", "claude-code")


def owner_neutrality_findings(terms: tuple[dict[str, object], ...]) -> list[tuple[str, str]]:
    """Return (reason, term) findings where a meaning names a CLI as default.

    The reason code `owner_neutrality_lost` matches the reason code the
    equivalence gates use for the same class of defect: copy that promotes a
    specific external CLI to the implicit default coding owner.
    """
    findings: list[tuple[str, str]] = []
    for entry in terms:
        meaning = str(entry["meaning"]).casefold()
        if "default" in meaning and any(name in meaning for name in _EXTERNAL_CLI_NAME_MENTIONS):
            findings.append(("owner_neutrality_lost", str(entry["term"])))
    return findings


def _resolve_reference(reference: str) -> object:
    """Resolve a dotted machinery reference by import, raising if it fails."""
    parts = reference.split(".")
    last_error: Exception | None = None
    for split in range(len(parts), 0, -1):
        module_name = ".".join(parts[:split])
        try:
            resolved: object = importlib.import_module(module_name)
        except ImportError as exc:
            last_error = exc
            continue
        for attribute in parts[split:]:
            resolved = getattr(resolved, attribute)
        return resolved
    raise ImportError(f"unresolvable machinery reference: {reference}") from last_error


def _term_entries() -> dict[str, dict[str, object]]:
    payload = orchestration_vocabulary_payload()
    return {str(entry["term"]): entry for entry in payload["terms"]}


class OrchestrationVocabularyPinTests(unittest.TestCase):
    def test_schema_version_and_exact_nine_term_set(self) -> None:
        payload = orchestration_vocabulary_payload()
        self.assertEqual(payload["schema_version"], ORCHESTRATION_VOCABULARY_SCHEMA_VERSION)
        self.assertEqual(payload["schema_version"], "omh_orchestration_vocabulary/v1")

        term_ids = {str(entry["term"]) for entry in payload["terms"]}
        missing = EXPECTED_TERM_IDS - term_ids
        extra = term_ids - EXPECTED_TERM_IDS
        self.assertFalse(missing, f"missing vocabulary terms: {sorted(missing)}")
        self.assertFalse(extra, f"unexpected vocabulary terms: {sorted(extra)}")
        self.assertEqual(len(payload["terms"]), 9)

    def test_payload_is_fresh_per_call(self) -> None:
        first = orchestration_vocabulary_payload()
        first["terms"][0]["meaning"] = "mutated"
        second = orchestration_vocabulary_payload()
        self.assertNotEqual(second["terms"][0]["meaning"], "mutated")

    def test_audiences_drawn_from_the_three_allowed_values(self) -> None:
        self.assertEqual(
            ORCHESTRATION_VOCABULARY_AUDIENCES, ("normal_user", "operator", "maintainer")
        )
        for term, entry in _term_entries().items():
            self.assertIn(
                entry["audience"], ORCHESTRATION_VOCABULARY_AUDIENCES, f"term {term}"
            )

    def test_every_machinery_reference_resolves_by_import(self) -> None:
        for term, entry in _term_entries().items():
            for reference in entry["machinery"]:
                resolved = _resolve_reference(str(reference))
                self.assertIsNotNone(resolved, f"term {term}: {reference}")

    def test_entry_fields_are_complete(self) -> None:
        for term, entry in _term_entries().items():
            self.assertEqual(
                set(entry), {"term", "meaning", "audience", "entry_points", "machinery"},
                f"term {term}",
            )
            self.assertTrue(str(entry["meaning"]).strip(), f"term {term} needs a meaning")
            self.assertIsInstance(entry["entry_points"], tuple, f"term {term}")
            self.assertIsInstance(entry["machinery"], tuple, f"term {term}")


class VocabularySeparationTests(unittest.TestCase):
    def test_mixture_and_maestro_share_no_worker_team_language(self) -> None:
        entries = _term_entries()
        for term in ("hermes_mixture", "maestro"):
            words = set(str(entries[term]["meaning"]).casefold().replace(",", " ").split())
            overlap = words & _WORKER_TEAM_TOKENS
            self.assertFalse(overlap, f"term {term} uses worker/team language: {sorted(overlap)}")

    def test_maestro_machinery_names_the_narrow_profile_set(self) -> None:
        maestro = _term_entries()["maestro"]
        self.assertIn("omh.coding.executors.EXTERNAL_CLI_PROFILES", maestro["machinery"])
        for reference in maestro["machinery"]:
            self.assertNotIn("_EXTERNAL_PROFILES", str(reference))

    def test_maestro_prepares_and_never_claims_invocation(self) -> None:
        meaning = str(_term_entries()["maestro"]["meaning"])
        self.assertIn("prepares a handoff for", meaning)
        self.assertNotIn("invoke", meaning.casefold())

    def test_owner_neutrality_holds_and_mutant_is_caught(self) -> None:
        payload = orchestration_vocabulary_payload()
        self.assertEqual(owner_neutrality_findings(payload["terms"]), [])

        # Mutant (`owner_neutrality_lost`-shaped): rewrite maestro.meaning to
        # name Codex as the default coding owner; the assertion must fail
        # with that reason code.
        mutated = tuple(dict(entry) for entry in payload["terms"])
        for entry in mutated:
            if entry["term"] == "maestro":
                entry["meaning"] = "Codex is the default coding owner for prepared handoffs."
        findings = owner_neutrality_findings(mutated)
        self.assertIn(("owner_neutrality_lost", "maestro"), findings)


class ExternalCliProfileSetTests(unittest.TestCase):
    def test_external_cli_profiles_membership(self) -> None:
        self.assertEqual(EXTERNAL_CLI_PROFILES, ("claude-code", "codex"))

    def test_structural_subset_regression_guard(self) -> None:
        # True by construction: the facade derives its membership from
        # `EXTERNAL_CLI_PROFILES`. The assertion stays as a regression guard.
        self.assertTrue(set(EXTERNAL_CLI_PROFILES) < set(_EXTERNAL_PROFILES))
        self.assertEqual(
            set(_EXTERNAL_PROFILES) - set(EXTERNAL_CLI_PROFILES),
            {"omx-runtime", "omo-runtime", "omc-runtime", "generic"},
        )


class ChooseExecutorCopyTests(unittest.TestCase):
    def test_choose_executor_reason_is_consumed_by_action_copy(self) -> None:
        self.assertEqual(NEXT_ACTION_LABELS["choose_executor"], CHOOSE_EXECUTOR_REASON)
        self.assertEqual(CHOOSE_EXECUTOR_REASON, "asking the user to pick the coding agent")


if __name__ == "__main__":
    unittest.main()
