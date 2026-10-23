"""Contracts for `capability_inspiration_snapshot/v1`.

Three properties are load-bearing and each has its own section below.

Determinism. The digest is canonical over the observed material, so the same
evidence read twice -- at a different time, by a different observer, supplied in
a different order -- produces the same number. Anything else makes "has the
inspiration changed?" unanswerable, because every re-observation would look like
a change.

Supersession. Changed evidence mints a new snapshot that links to the old one.
The old one is asserted to be byte-identical afterwards and to still validate:
the whole reason to link rather than overwrite is that the evidence which
actually shaped the shipped design has to stay readable.

Evidence boundary. A citation is checked field by field against a list of words
that would read as "OMH looked just now". The two claim-boundary strings are
excluded from that scan and checked separately, because their job is to state
the negation and a naive scan would fail them for saying the word.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _local_package import load_local_package

load_local_package()
from omh.local_store import atomic_write_text
from omh.quality.capability_inspiration_snapshot import (
    CAPABILITY_INSPIRATION_CITATION_CLAIM_BOUNDARY,
    CAPABILITY_INSPIRATION_CITATION_KEYS,
    CAPABILITY_INSPIRATION_CITATION_SCHEMA_VERSION,
    CAPABILITY_INSPIRATION_CLAIM_BOUNDARY,
    CAPABILITY_INSPIRATION_COMPARISON_SCHEMA_VERSION,
    CAPABILITY_INSPIRATION_NOT_OBSERVED,
    CAPABILITY_INSPIRATION_SNAPSHOT_KEYS,
    CAPABILITY_INSPIRATION_SNAPSHOT_SCHEMA_VERSION,
    CAPABILITY_INSPIRATION_SOURCE_KEYS,
    COMPARISON_VERDICTS,
    MAX_FINDINGS,
    REVISION_STABILITIES,
    SOURCE_AVAILABILITY_STATES,
    SOURCE_CHANGE_KINDS,
    CapabilityInspirationSnapshotError,
    build_capability_inspiration_snapshot,
    build_capability_inspiration_source,
    capability_inspiration_chain_errors,
    capability_inspiration_citation,
    compare_capability_inspiration_snapshots,
    revision_stability,
    validate_capability_inspiration_citation,
    validate_capability_inspiration_comparison,
    validate_capability_inspiration_snapshot,
)


_PLUGIN_DOCS = "https://docs.openclaw.ai/plugins"
_MARKETPLACE_DOCS = "https://code.claude.com/docs/en/plugin-marketplaces"
_IMMUTABLE_REVISION = "b" * 40
_LATER_REVISION = "c" * 40

# Words that would make a cited snapshot read as a live check. Every one of them
# is absent from this family's field names and closed vocabularies by
# construction, which is the property AC3 asks for.
_ASSERTED_AVAILABILITY = (
    "available",
    "reachable",
    "online",
    "verified",
    "fetched",
    "downloaded",
    "live",
    "observed_now",
    "up to date",
)

# Every module that could turn this into a fetcher. The snapshot records what a
# caller supplied; a network import here would make that claim unenforceable.
_NETWORK_MODULES = (
    "http",
    "httpx",
    "requests",
    "socket",
    "ssl",
    "subprocess",
    "urllib",
)


def _source(
    uri: str = _PLUGIN_DOCS,
    *,
    revision: str = _IMMUTABLE_REVISION,
    content: str = "plugin manifest reference, section 3",
    license_note: str = "Apache-2.0 as displayed on the page footer",
    observation_provenance: str = "user",
) -> dict[str, object]:
    return build_capability_inspiration_source(
        uri=uri,
        revision=revision,
        content=content,
        license_note=license_note,
        observation_provenance=observation_provenance,
    )


def _snapshot(
    *,
    sources: list[dict[str, object]] | None = None,
    observer: str = "maintainer reading the published docs",
    observed_at: str = "2026-08-09T10:00:00Z",
    findings: tuple[str, ...] = ("Plugin manifests declare tools and hooks separately.",),
    derived_requirements: tuple[str, ...] = (
        "OMH names a capability family without installing a plugin.",
        "OMH keeps the derived requirement separate from the cited evidence.",
    ),
    reviewer_ref: str = "",
    supersedes_snapshot_ref: str = "",
) -> dict[str, object]:
    return build_capability_inspiration_snapshot(
        capability_id="capability-family-projection",
        observed_sources=sources if sources is not None else [_source()],
        observer=observer,
        observed_at=observed_at,
        findings=findings,
        derived_requirements=derived_requirements,
        reviewer_ref=reviewer_ref,
        supersedes_snapshot_ref=supersedes_snapshot_ref,
    )


class UnchangedEvidenceReproducesTheDigestTests(unittest.TestCase):
    """AC1: the digest is canonical over the observed material."""

    def test_two_separate_builds_of_the_same_evidence_agree(self) -> None:
        first = _snapshot()
        second = _snapshot()

        self.assertEqual(first, second)
        self.assertEqual(validate_capability_inspiration_snapshot(first), [])
        self.assertEqual(
            first["schema_version"], CAPABILITY_INSPIRATION_SNAPSHOT_SCHEMA_VERSION
        )

    def test_observation_metadata_is_outside_the_digest(self) -> None:
        first = _snapshot(observer="maintainer A", observed_at="2026-01-01T00:00:00Z")
        second = _snapshot(
            observer="maintainer B",
            observed_at="2026-08-09T23:59:59Z",
            reviewer_ref="review-2026-08-09",
        )

        self.assertNotEqual(first["observed_at"], second["observed_at"])
        self.assertNotEqual(first["observer"], second["observer"])
        for key in ("evidence_digest", "derived_requirements_digest", "snapshot_digest", "snapshot_id"):
            with self.subTest(key=key):
                self.assertEqual(first[key], second[key])

    def test_source_order_is_not_meaningful(self) -> None:
        forward = _snapshot(sources=[_source(), _source(_MARKETPLACE_DOCS, content="marketplace reference")])
        reverse = _snapshot(sources=[_source(_MARKETPLACE_DOCS, content="marketplace reference"), _source()])

        self.assertEqual(forward, reverse)
        self.assertEqual(
            [source["uri"] for source in forward["observed_sources"]],
            sorted(source["uri"] for source in forward["observed_sources"]),
        )

    def test_requirement_order_is_meaningful(self) -> None:
        forward = _snapshot(derived_requirements=("first requirement", "second requirement"))
        reverse = _snapshot(derived_requirements=("second requirement", "first requirement"))

        self.assertNotEqual(
            forward["derived_requirements_digest"], reverse["derived_requirements_digest"]
        )
        self.assertNotEqual(forward["snapshot_digest"], reverse["snapshot_digest"])

    def test_content_digest_survives_a_file_round_trip(self) -> None:
        content = "plugin manifest reference, section 3\nsecond line\n"
        with TemporaryDirectory() as raw_root:
            path = Path(raw_root) / "observed.txt"
            atomic_write_text(path, content)
            from_disk = path.read_bytes().decode("utf-8")

        direct = _source(content=content)
        round_tripped = _source(content=from_disk)

        self.assertEqual(from_disk, content)
        self.assertEqual(direct["content_digest"], round_tripped["content_digest"])
        self.assertEqual(
            direct["content_digest"], hashlib.sha256(content.encode("utf-8")).hexdigest()
        )

    def test_revision_stability_is_derived_conservatively(self) -> None:
        cases = {
            _IMMUTABLE_REVISION: "immutable",
            "d" * 64: "immutable",
            "main": "mutable",
            "v1.2.3": "mutable",
            "": "unknown",
        }
        for revision, expected in cases.items():
            with self.subTest(revision=revision):
                self.assertEqual(revision_stability(revision), expected)
                self.assertIn(expected, REVISION_STABILITIES)

    def test_a_mutable_revision_is_visible_on_the_snapshot(self) -> None:
        snapshot = _snapshot(sources=[_source(revision="main")])

        self.assertEqual(snapshot["observed_sources"][0]["revision_stability"], "mutable")
        self.assertEqual(validate_capability_inspiration_snapshot(snapshot), [])

    def test_the_module_cannot_fetch(self) -> None:
        module_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "quality"
            / "capability_inspiration_snapshot.py"
        )
        source = module_path.read_bytes().decode("utf-8")
        imported = {
            line.split()[1].split(".")[0]
            for line in source.splitlines()
            if line.startswith("import ") or line.startswith("from ")
        }

        for module in _NETWORK_MODULES:
            with self.subTest(module=module):
                self.assertNotIn(module, imported)


class ChangedEvidenceSupersedesTests(unittest.TestCase):
    """AC2: the earlier snapshot survives and the report names the difference."""

    def test_a_changed_revision_mints_a_superseding_snapshot(self) -> None:
        earlier = _snapshot()
        frozen = json.dumps(earlier, sort_keys=True)

        later = _snapshot(
            sources=[_source(revision=_LATER_REVISION, content="plugin manifest reference, section 4")],
            observed_at="2026-09-01T10:00:00Z",
            supersedes_snapshot_ref=str(earlier["snapshot_id"]),
        )

        self.assertNotEqual(earlier["snapshot_id"], later["snapshot_id"])
        self.assertNotEqual(earlier["evidence_digest"], later["evidence_digest"])
        self.assertEqual(later["supersedes_snapshot_ref"], earlier["snapshot_id"])
        self.assertEqual(json.dumps(earlier, sort_keys=True), frozen)
        self.assertEqual(validate_capability_inspiration_snapshot(earlier), [])
        self.assertEqual(capability_inspiration_chain_errors([earlier, later]), [])

    def test_the_report_names_what_moved(self) -> None:
        earlier = _snapshot()
        later = _snapshot(
            sources=[_source(revision=_LATER_REVISION, content="plugin manifest reference, section 4")],
            supersedes_snapshot_ref=str(earlier["snapshot_id"]),
        )

        report = compare_capability_inspiration_snapshots(earlier, later)

        self.assertEqual(validate_capability_inspiration_comparison(report), [])
        self.assertEqual(report["schema_version"], CAPABILITY_INSPIRATION_COMPARISON_SCHEMA_VERSION)
        self.assertEqual(report["verdict"], "changed")
        self.assertTrue(report["supersedes_earlier"])
        self.assertEqual(report["earlier_snapshot_id"], earlier["snapshot_id"])
        self.assertEqual(report["later_snapshot_id"], later["snapshot_id"])
        self.assertEqual(
            report["changed_sources"],
            [
                {
                    "uri": _PLUGIN_DOCS,
                    "earlier_source_id": earlier["observed_sources"][0]["source_id"],
                    "later_source_id": later["observed_sources"][0]["source_id"],
                    "changes": ["content_changed", "revision_changed"],
                }
            ],
        )

    def test_added_and_removed_sources_are_named(self) -> None:
        earlier = _snapshot()
        later = _snapshot(sources=[_source(_MARKETPLACE_DOCS, content="marketplace reference")])

        report = compare_capability_inspiration_snapshots(earlier, later)

        self.assertEqual(validate_capability_inspiration_comparison(report), [])
        self.assertEqual(
            [(entry["uri"], entry["changes"]) for entry in report["changed_sources"]],
            [(_MARKETPLACE_DOCS, ["added"]), (_PLUGIN_DOCS, ["removed"])],
        )

    def test_a_license_observation_alone_is_a_change(self) -> None:
        earlier = _snapshot()
        later = _snapshot(sources=[_source(license_note="MIT as displayed on the page footer")])

        report = compare_capability_inspiration_snapshots(earlier, later)

        self.assertEqual(report["verdict"], "changed")
        self.assertEqual(report["changed_sources"][0]["changes"], ["license_changed"])

    def test_unchanged_evidence_reports_current(self) -> None:
        report = compare_capability_inspiration_snapshots(_snapshot(), _snapshot(observer="someone else"))

        self.assertEqual(validate_capability_inspiration_comparison(report), [])
        self.assertEqual(report["verdict"], "current")
        self.assertEqual(report["changed_sources"], [])
        self.assertFalse(report["derived_requirements_changed"])

    def test_derived_requirements_are_reported_apart_from_the_evidence(self) -> None:
        earlier = _snapshot()
        later = _snapshot(derived_requirements=("OMH names a capability family without installing a plugin.",))

        report = compare_capability_inspiration_snapshots(earlier, later)

        self.assertEqual(report["verdict"], "current")
        self.assertTrue(report["derived_requirements_changed"])

    def test_evidence_that_could_not_be_re_observed_is_unverifiable(self) -> None:
        earlier = _snapshot()

        report = compare_capability_inspiration_snapshots(earlier, None)

        self.assertEqual(validate_capability_inspiration_comparison(report), [])
        self.assertEqual(report["verdict"], "unverifiable")
        self.assertEqual(report["later_snapshot_id"], "")
        self.assertEqual(report["changed_sources"], [])
        self.assertEqual(sorted(COMPARISON_VERDICTS), ["changed", "current", "unverifiable"])

    def test_unchanged_evidence_cannot_supersede_itself(self) -> None:
        earlier = _snapshot()
        repeat = _snapshot(supersedes_snapshot_ref=str(earlier["snapshot_id"]))

        self.assertEqual(repeat["snapshot_id"], earlier["snapshot_id"])
        self.assertIn(
            "capability_inspiration_snapshot supersedes_snapshot_ref must not name itself",
            validate_capability_inspiration_snapshot(repeat),
        )


class CitedSnapshotsClaimNoAvailabilityTests(unittest.TestCase):
    """AC3: a handoff can cite the snapshot; availability stays unobserved."""

    def test_a_citation_is_bindable_and_valid(self) -> None:
        snapshot = _snapshot(reviewer_ref="review-2026-08-09")

        citation = capability_inspiration_citation(snapshot)

        self.assertEqual(validate_capability_inspiration_citation(citation), [])
        self.assertEqual(citation["schema_version"], CAPABILITY_INSPIRATION_CITATION_SCHEMA_VERSION)
        self.assertEqual(sorted(citation), sorted(CAPABILITY_INSPIRATION_CITATION_KEYS))
        self.assertEqual(citation["snapshot_digest"], snapshot["snapshot_digest"])
        self.assertEqual(citation["evidence_digest"], snapshot["evidence_digest"])
        self.assertEqual(citation["cited_source_count"], 1)

    def test_availability_has_one_state_and_it_is_unobserved(self) -> None:
        citation = capability_inspiration_citation(_snapshot())

        self.assertEqual(SOURCE_AVAILABILITY_STATES, ("not_observed",))
        self.assertEqual(citation["source_availability"], "not_observed")
        self.assertIn(citation["source_availability"], SOURCE_AVAILABILITY_STATES)

    def test_every_unobserved_claim_is_declared(self) -> None:
        citation = capability_inspiration_citation(_snapshot())

        self.assertEqual(
            citation["not_evidence_until_observed"], list(CAPABILITY_INSPIRATION_NOT_OBSERVED)
        )
        for claim in CAPABILITY_INSPIRATION_NOT_OBSERVED:
            with self.subTest(claim=claim):
                self.assertIn(claim, citation["not_evidence_until_observed"])

    def test_nothing_in_a_cited_handoff_reads_as_observed_now(self) -> None:
        citation = capability_inspiration_citation(_snapshot())
        handoff = {
            "capability_id": citation["capability_id"],
            "inspiration_citation": citation,
            "scope": "Implement the OMH-native capability family projection.",
        }
        scanned = dict(citation)
        scanned.pop("claim_boundary")
        rendered = json.dumps({**handoff, "inspiration_citation": scanned}, sort_keys=True).lower()

        for phrase in _ASSERTED_AVAILABILITY:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, rendered)

    def test_the_boundary_states_the_negation_it_owes(self) -> None:
        citation = capability_inspiration_citation(_snapshot())

        self.assertEqual(citation["claim_boundary"], CAPABILITY_INSPIRATION_CITATION_CLAIM_BOUNDARY)
        self.assertIn("not a live source check", citation["claim_boundary"])
        self.assertIn("stay unobserved", citation["claim_boundary"])
        self.assertIn("no fetch", CAPABILITY_INSPIRATION_CLAIM_BOUNDARY.lower())

    def test_a_citation_carries_no_source_uri(self) -> None:
        citation = capability_inspiration_citation(_snapshot())
        rendered = json.dumps(citation, sort_keys=True)

        self.assertNotIn(_PLUGIN_DOCS, rendered)
        self.assertNotIn("://", rendered)


class RefusalTests(unittest.TestCase):
    """The guards that keep a snapshot from asserting something untrue."""

    def test_a_snapshot_with_no_observer_is_refused(self) -> None:
        for observer in ("", "   "):
            with self.subTest(observer=observer):
                with self.assertRaises(CapabilityInspirationSnapshotError) as caught:
                    _snapshot(observer=observer)
                self.assertIn("observer is required", str(caught.exception))

    def test_a_snapshot_with_no_source_is_refused(self) -> None:
        with self.assertRaises(CapabilityInspirationSnapshotError) as caught:
            _snapshot(sources=[])

        self.assertIn("at least one observed source", str(caught.exception))

    def test_a_source_with_no_uri_is_refused(self) -> None:
        with self.assertRaises(CapabilityInspirationSnapshotError) as caught:
            build_capability_inspiration_source(uri="   ")

        self.assertIn("uri is required", str(caught.exception))

    def test_a_repeated_uri_is_refused(self) -> None:
        with self.assertRaises(CapabilityInspirationSnapshotError) as caught:
            _snapshot(sources=[_source(), _source(revision=_LATER_REVISION)])

        self.assertIn("repeats uri", str(caught.exception))

    def test_an_unsupported_source_key_is_refused(self) -> None:
        source = _source()
        source["content"] = "the caller expected this to be digested here"

        with self.assertRaises(CapabilityInspirationSnapshotError) as caught:
            _snapshot(sources=[source])

        self.assertIn("unsupported keys", str(caught.exception))

    def test_findings_stay_bounded(self) -> None:
        with self.assertRaises(CapabilityInspirationSnapshotError) as caught:
            _snapshot(findings=tuple(f"finding {index}" for index in range(MAX_FINDINGS + 1)))

        self.assertIn(f"exceeds {MAX_FINDINGS} entries", str(caught.exception))

    def test_an_overlong_line_is_refused(self) -> None:
        with self.assertRaises(CapabilityInspirationSnapshotError) as caught:
            _snapshot(findings=("x" * 201,))

        self.assertIn("exceeds 200 characters", str(caught.exception))

    def test_a_chain_fork_is_refused(self) -> None:
        earlier = _snapshot()
        one = _snapshot(
            sources=[_source(revision=_LATER_REVISION)],
            supersedes_snapshot_ref=str(earlier["snapshot_id"]),
        )
        other = _snapshot(
            sources=[_source(_MARKETPLACE_DOCS, content="marketplace reference")],
            supersedes_snapshot_ref=str(earlier["snapshot_id"]),
        )

        errors = capability_inspiration_chain_errors([earlier, one, other])

        self.assertEqual(len(errors), 1)
        self.assertIn("forks the chain", errors[0])
        self.assertIn(str(earlier["snapshot_id"]), errors[0])

    def test_a_chain_link_to_an_unknown_snapshot_is_refused(self) -> None:
        orphan = _snapshot(supersedes_snapshot_ref="capability-inspiration-0000000000000000")

        errors = capability_inspiration_chain_errors([orphan])

        self.assertEqual(len(errors), 1)
        self.assertIn("does not name an earlier snapshot", errors[0])

    def test_a_repeated_snapshot_id_is_refused(self) -> None:
        snapshot = _snapshot()

        errors = capability_inspiration_chain_errors([snapshot, _snapshot()])

        self.assertEqual(len(errors), 1)
        self.assertIn("snapshot_id is not unique", errors[0])
        self.assertIn(str(snapshot["snapshot_id"]), errors[0])

    def test_a_self_linking_snapshot_is_refused_by_the_chain_walk(self) -> None:
        snapshot = _snapshot()
        snapshot["supersedes_snapshot_ref"] = snapshot["snapshot_id"]

        errors = capability_inspiration_chain_errors([snapshot])

        self.assertEqual(len(errors), 1)
        self.assertIn("must not name itself", errors[0])


class ValidatorTests(unittest.TestCase):
    """The key set is closed in both directions and the digests are re-derived."""

    def test_the_built_snapshot_carries_exactly_the_declared_keys(self) -> None:
        snapshot = _snapshot()

        self.assertEqual(sorted(snapshot), sorted(CAPABILITY_INSPIRATION_SNAPSHOT_KEYS))
        self.assertEqual(
            sorted(snapshot["observed_sources"][0]), sorted(CAPABILITY_INSPIRATION_SOURCE_KEYS)
        )

    def test_an_extra_key_is_refused(self) -> None:
        snapshot = _snapshot()
        snapshot["observed_availability"] = "available"

        self.assertIn(
            "capability_inspiration_snapshot has unsupported keys: ['observed_availability']",
            validate_capability_inspiration_snapshot(snapshot),
        )

    def test_a_missing_key_is_refused(self) -> None:
        snapshot = _snapshot()
        del snapshot["evidence_digest"]

        self.assertIn(
            "capability_inspiration_snapshot is missing keys: ['evidence_digest']",
            validate_capability_inspiration_snapshot(snapshot),
        )

    def test_a_digest_that_does_not_follow_from_the_material_is_refused(self) -> None:
        snapshot = _snapshot()
        snapshot["derived_requirements"] = ["a requirement nobody digested"]

        errors = validate_capability_inspiration_snapshot(snapshot)

        self.assertIn(
            "capability_inspiration_snapshot derived_requirements_digest does not match its derived requirements",
            errors,
        )
        self.assertIn(
            "capability_inspiration_snapshot snapshot_digest does not match its frozen material", errors
        )

    def test_a_weakened_claim_boundary_is_refused(self) -> None:
        snapshot = _snapshot()
        snapshot["claim_boundary"] = "OMH checked this source."

        self.assertIn(
            "capability_inspiration_snapshot claim_boundary must state that OMH observed nothing itself",
            validate_capability_inspiration_snapshot(snapshot),
        )

    def test_a_dropped_boundary_declaration_is_refused(self) -> None:
        snapshot = _snapshot()
        snapshot["not_evidence_until_observed"] = ["source_availability"]

        self.assertIn(
            "capability_inspiration_snapshot not_evidence_until_observed must be "
            f"{list(CAPABILITY_INSPIRATION_NOT_OBSERVED)}",
            validate_capability_inspiration_snapshot(snapshot),
        )

    def test_a_mislabelled_revision_stability_is_refused(self) -> None:
        snapshot = _snapshot(sources=[_source(revision="main")])
        snapshot["observed_sources"][0]["revision_stability"] = "immutable"

        self.assertIn(
            "capability_inspiration_snapshot observed source revision_stability does not match its revision",
            validate_capability_inspiration_snapshot(snapshot),
        )

    def test_a_comparison_that_contradicts_its_verdict_is_refused(self) -> None:
        report = compare_capability_inspiration_snapshots(_snapshot(), _snapshot())
        report["changed_sources"] = [
            {
                "uri": _PLUGIN_DOCS,
                "earlier_source_id": "inspiration-source-0000000000000000",
                "later_source_id": "inspiration-source-1111111111111111",
                "changes": ["revision_changed"],
            }
        ]

        self.assertIn(
            "capability_inspiration_comparison verdict current cannot name changed sources",
            validate_capability_inspiration_comparison(report),
        )

    def test_an_unknown_change_kind_is_refused(self) -> None:
        report = compare_capability_inspiration_snapshots(_snapshot(), None)
        report["changed_sources"] = [
            {
                "uri": _PLUGIN_DOCS,
                "earlier_source_id": "",
                "later_source_id": "",
                "changes": ["rewritten"],
            }
        ]

        self.assertIn(
            f"capability_inspiration_comparison changed source changes must name one or more of {list(SOURCE_CHANGE_KINDS)}",
            validate_capability_inspiration_comparison(report),
        )

    def test_a_citation_without_a_source_is_refused(self) -> None:
        citation = capability_inspiration_citation(_snapshot())
        citation["cited_source_count"] = 0

        self.assertIn(
            "capability_inspiration_citation cited_source_count must be a positive integer",
            validate_capability_inspiration_citation(citation),
        )

    def test_non_objects_are_refused_by_every_validator(self) -> None:
        for validate in (
            validate_capability_inspiration_snapshot,
            validate_capability_inspiration_citation,
            validate_capability_inspiration_comparison,
        ):
            with self.subTest(validate=validate.__name__):
                self.assertEqual(len(validate("not an object")), 1)


if __name__ == "__main__":
    unittest.main()
