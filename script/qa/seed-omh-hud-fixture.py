from __future__ import annotations

import json
from pathlib import Path
import sys

from omh import __version__
from omh.coding.executor_progress import (
    append_progress_event,
    build_progress_binding,
    build_progress_event,
    write_progress_binding,
)
from omh.paths import OmhPaths


def main() -> int:
    omh_home = Path(sys.argv[1])
    hermes_home = Path(sys.argv[2])
    paths = OmhPaths(omh_home=omh_home, hermes_home=hermes_home)
    fixtures = [
        (
            "run-300-explore",
            "codex",
            "repo_exploration",
            "Reviewing production code changes.",
            "gpt-5.6-sol",
            "medium",
            18_200,
            {
                "category": "deep",
                "turn_count": 3,
                "tool_count": 14,
                "cost_usd": 0.1346,
                "tokens_per_second": 45,
                "elapsed_seconds": 23,
                "cache_hit_percentage": 0,
                "context_percentage": 41.5,
            },
        ),
        (
            "run-200-librarian",
            "hermes_local",
            "progress_observed",
            "Reviewing the CLI migration briefing.",
            "claude-opus-5",
            "",
            4_300,
            {"category": "unspecified-high", "fallback_count": 2, "turn_count": 5, "tool_count": 9, "cost_usd": 0.263, "tokens_per_second": 89, "elapsed_seconds": 23},
        ),
        (
            "run-100-architect",
            "claude_code",
            "executor_blocked",
            "Checking documented integrity boundaries.",
            "claude-opus-5",
            "",
            None,
            {"category": "deep", "turn_count": 2, "tool_count": 7, "cost_usd": 0.1442, "tokens_per_second": 44, "elapsed_seconds": 22},
        ),
    ]
    for run_id, profile, event_type, summary, model, effort, tokens, metrics in fixtures:
        run_dir = paths.runtime_runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "skill": "ULW model routing review",
                    "phase": "executing",
                    "observation_status": "execution_observed",
                    "executor_target": "maestro" if run_id == "run-300-explore" else profile,
                }
            ),
            encoding="utf-8",
        )
        if run_id == "run-300-explore":
            (run_dir / "coding_delegation.json").write_text(
                json.dumps({"executor_handoff": {"executor_target": "maestro"}}),
                encoding="utf-8",
            )
        binding = build_progress_binding(
            target_type="run",
            target_id=run_id,
            executor_profile=profile,
            observed_hermes_execution=profile == "hermes_local",
        )
        write_progress_binding(paths, binding)
        event = build_progress_event(
            binding,
            event_type=event_type,
            summary=summary,
            signal={
                "routed_model": model,
                "routed_reasoning_effort": effort,
                "tokens_total": tokens,
                **metrics,
            },
        )
        append_progress_event(paths, binding, event)
    paths.runtime_dir.mkdir(parents=True, exist_ok=True)
    paths.runtime_state_path.write_text(
        json.dumps(
            {
                "schema_version": "omh_runtime_state/v1",
                "last_run_id": "run-300-explore",
                "version": __version__,
            }
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
