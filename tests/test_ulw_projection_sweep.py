"""Plan Q10 guard: no user-visible producer projects a retired ULW engine name.

#954 stage 5 (window=0) retires `team`, `ultraprocess`, `ralph`, and
`ultragoal` from the installable/routable surface. This sweep walks the
user-visible producers — parity capabilities, coding executor handoff
templates, the Hermes coding-team path contract, capability-family
projection, harness payloads, and every rendered skill/reference body — and
fails when a retired canonical name appears in a projected payload outside an
explicitly recorded deliberate keep. Each keep names its reason, so a new
projection of a retired name is a reviewed decision, not drift.

`team` is an ordinary English word, so its pattern matches only
invocation-shaped uses (`$team`, backticked `team`, "Use OMH team"), never
prose like "coding team" or "team-readiness".
"""

from __future__ import annotations

import dataclasses
import json
import re
import unittest

from omh.capabilities.families import capability_family_projection
from omh.coding.executors import (
    RUNTIME_PROFILE_DETAILS,
    hermes_coding_team_path_contract,
    public_executor_options,
)
from omh.quality.parity import PARITY_CAPABILITIES
from omh.skills.catalog import builtin_harnesses, retired_ulw_engine_names
from omh.skills.packaging import (
    builtin_skill_reference_templates,
    builtin_skill_templates,
)

_CONTEXT_CHARS = 80

# (surface prefix, retired name, required context substring, reason).
# A finding is allowed only when a keep row matches its surface prefix and
# retired name and the keep's context substring appears around the match.
DELIBERATE_KEEPS: tuple[tuple[str, str, str, str], ...] = (
    (
        "executors:profiles",
        "ultragoal",
        "$ultragoal {message}",
        "OMX runtime catalog: `$ultragoal` is the OMX runtime's own skill syntax, "
        "another product's surface that OMH documents but does not own (#954 keep).",
    ),
    (
        "executors:profiles",
        "team",
        "$team {message}",
        "OMX runtime catalog: `$team` is the OMX runtime's own skill syntax, "
        "another product's surface that OMH documents but does not own (#954 keep).",
    ),
    (
        "reference:oh-my-hermes/references/workflow-registry.md",
        "team",
        "A bare common word such as `team`",
        "Negative guidance: names the bare word precisely to say it must NOT "
        "route on its own; removing it would weaken the anti-overroute rule.",
    ),
)


def _pattern_for(retired_name: str) -> re.Pattern[str]:
    if retired_name == "team":
        return re.compile(r"\$team\b|`team`|\bUse OMH team\b|\bOMH team\b")
    return re.compile(rf"\b{re.escape(retired_name)}\b", re.IGNORECASE)


def _surfaces() -> list[tuple[str, str]]:
    surfaces: list[tuple[str, str]] = [
        ("parity", json.dumps([capability.to_dict() for capability in PARITY_CAPABILITIES])),
        ("executors:profiles", json.dumps(RUNTIME_PROFILE_DETAILS)),
        ("executors:options", json.dumps(public_executor_options())),
        (
            "executors:team-path",
            json.dumps(hermes_coding_team_path_contract("hermes-runtime")),
        ),
        ("capability-families", json.dumps(capability_family_projection())),
    ]
    for harness in builtin_harnesses():
        surfaces.append(
            (f"harness:{harness.name}", json.dumps(dataclasses.asdict(harness)))
        )
    for template in builtin_skill_templates():
        surfaces.append((f"skill:{template.name}", template.content))
    for reference in builtin_skill_reference_templates():
        surfaces.append(
            (
                f"reference:{reference.skill_name}/{reference.relative_path}",
                reference.content,
            )
        )
    return surfaces


def _findings() -> list[tuple[str, str, str]]:
    """Every (surface, retired name, context window) match across producers."""
    findings: list[tuple[str, str, str]] = []
    for retired_name in retired_ulw_engine_names():
        pattern = _pattern_for(retired_name)
        for surface, text in _surfaces():
            for match in pattern.finditer(text):
                start = max(0, match.start() - _CONTEXT_CHARS)
                context = text[start : match.end() + _CONTEXT_CHARS]
                findings.append((surface, retired_name, context))
    return findings


def _is_kept(finding: tuple[str, str, str]) -> bool:
    surface, retired_name, context = finding
    return any(
        surface.startswith(keep_surface) and retired_name == keep_name and keep_context in context
        for keep_surface, keep_name, keep_context, _reason in DELIBERATE_KEEPS
    )


class RetiredNameProjectionSweepTests(unittest.TestCase):
    def test_retired_engine_set_matches_the_stage_five_fold(self) -> None:
        self.assertEqual(
            set(retired_ulw_engine_names()),
            {"team", "ultraprocess", "ralph", "ultragoal"},
        )

    def test_no_producer_projects_a_retired_name_outside_recorded_keeps(self) -> None:
        unexpected = [finding for finding in _findings() if not _is_kept(finding)]
        self.assertEqual(
            unexpected,
            [],
            "A user-visible producer projects a retired ULW engine name. "
            "Repoint the copy to `ultrawork`'s matching capability, or record "
            "a deliberate keep with its reason in DELIBERATE_KEEPS: "
            + json.dumps(unexpected, ensure_ascii=False, indent=2),
        )

    def test_sweep_is_not_vacuous(self) -> None:
        """The scanner must still see the recorded keeps; if a keep's context
        disappears the keep row is stale and must be pruned."""
        findings = _findings()
        for keep_surface, keep_name, keep_context, _reason in DELIBERATE_KEEPS:
            self.assertTrue(
                any(
                    surface.startswith(keep_surface)
                    and retired_name == keep_name
                    and keep_context in context
                    for surface, retired_name, context in findings
                ),
                f"Stale keep: {keep_surface} / {keep_name} / {keep_context!r} "
                "no longer matches any finding; remove or update the keep row.",
            )


if __name__ == "__main__":
    unittest.main()
