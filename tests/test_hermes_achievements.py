from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from _cli_harness import run_cli
from _local_package import load_local_package

load_local_package()
from omh.paths import OmhPaths
from omh.plugin_bundle.omh import runtime_reader
from omh.workflows.hermes_achievements import (
    filter_badges,
    find_badge,
    observe_achievements,
    render_achievements_markdown,
)


class HermesAchievementsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.omh_home = root / "omh"
        self.hermes_home = root / "hermes"
        # `plugin_dir` is the legacy install directory; `data_dir` is where the
        # bundled plugin keeps its files now (Hermes `plugin_data_dir()`).
        self.plugin_dir = self.hermes_home / "plugins" / "hermes-achievements"
        self.data_dir = self.hermes_home / "plugin-data" / "hermes-achievements"
        self.paths = OmhPaths(omh_home=self.omh_home, hermes_home=self.hermes_home)

    def _write_plugin_file(self, name: str, data: object, *, directory: Path | None = None) -> None:
        target = self.plugin_dir if directory is None else directory
        target.mkdir(parents=True, exist_ok=True)
        (target / name).write_text(json.dumps(data), encoding="utf-8")

    def _write_plugin_data_artifacts(self) -> None:
        # The wire shape the bundled plugin writes: `progress` is the raw
        # metric count, `progress_pct` the percent toward the next tier,
        # tiers are capitalized, timestamps are epoch seconds, and state.json
        # records the first tier earned as `first_tier`.
        self._write_plugin_file(
            "scan_snapshot.json",
            {
                "generated_at": 1_760_000_000,
                "achievements": [
                    {
                        "id": "let_him_cook",
                        "name": "Let Him Cook",
                        "category": "Agent Autonomy",
                        "kind": "best_session",
                        "unlocked": True,
                        "discovered": True,
                        "state": "unlocked",
                        "tier": "Silver",
                        "progress": 640,
                        "next_tier": "Gold",
                        "next_threshold": 1200,
                        "progress_pct": 20,
                        "unlocked_at": 1_759_000_000,
                        "evidence": {"session_id": "s-1", "title": "PRIVATE SESSION TITLE", "value": 640},
                    },
                    {
                        "id": "patch_wizard",
                        "name": "Patch Wizard",
                        "category": "Tool Mastery",
                        "kind": "lifetime",
                        "unlocked": False,
                        "discovered": True,
                        "state": "discovered",
                        "tier": None,
                        "progress": 1,
                        "next_tier": "Copper",
                        "next_threshold": 250,
                        "progress_pct": 0,
                    },
                    {
                        "id": "port_3000_taken",
                        "name": "???",
                        "category": "Debugging Chaos",
                        "secret": True,
                        "unlocked": False,
                        "discovered": False,
                        "state": "secret",
                        "tier": None,
                        "progress": 0,
                        "progress_pct": 0,
                    },
                ],
                "sessions": [{"session_id": "s-1", "title": "PRIVATE SESSION TITLE", "tool_call_count": 640}],
                "unlocked_count": 1,
                "discovered_count": 1,
                "secret_count": 1,
                "total_count": 3,
            },
            directory=self.data_dir,
        )
        self._write_plugin_file(
            "state.json",
            {
                "unlocks": {
                    "let_him_cook": {"unlocked_at": 1_759_000_000, "first_tier": "Copper", "evidence": None},
                    "weekend_warrior": {"unlocked_at": 1_759_500_000, "first_tier": "Copper", "evidence": None},
                }
            },
            directory=self.data_dir,
        )

    def _base_args(self) -> list[str]:
        return ["--omh-home", str(self.omh_home), "--hermes-home", str(self.hermes_home)]

    def _write_list_shaped_artifacts(self) -> None:
        self._write_plugin_file(
            "scan_snapshot.json",
            {
                "completed_at": "2026-07-01T10:00:00Z",
                "achievements": [
                    {
                        "id": "red-text",
                        "name": "Red Text Connoisseur",
                        "tier": "gold",
                        "category": "debugging",
                        "state": "unlocked",
                        "progress": 1.0,
                        "unlocked_at": "2026-06-30T09:00:00Z",
                    },
                    {
                        "id": "let-him-cook",
                        "name": "Let Him Cook",
                        "tier": "silver",
                        "category": "toolchain",
                        "state": "discovered",
                        "progress": {"current": 3, "target": 10},
                    },
                    {
                        "id": "night-owl",
                        "name": "Night Owl",
                        "category": "lifestyle",
                        "secret": True,
                    },
                ],
            },
        )
        self._write_plugin_file(
            "state.json",
            {
                "unlocked": {
                    "red-text": {"unlocked_at": "2026-06-30T09:00:00Z", "tier": "gold"},
                    "weekend-warrior": {
                        "name": "Weekend Warrior",
                        "tier": "copper",
                        "category": "lifestyle",
                        "unlocked_at": "2026-07-02T08:00:00Z",
                    },
                }
            },
        )


class ObserveAchievementsTests(HermesAchievementsTestCase):
    def test_missing_artifacts_report_not_observed(self) -> None:
        payload = observe_achievements(self.paths)

        self.assertEqual(payload["status"], "not_observed")
        self.assertFalse(payload["observed"])
        self.assertEqual(payload["errors"], [])
        self.assertEqual(payload["badges"], [])
        self.assertEqual(payload["summary"]["unlocked_count"], 0)
        self.assertEqual(payload["summary"]["total_count"], 0)
        self.assertEqual(payload["privacy"], "metadata_only")

    def test_list_shaped_snapshot_merges_state_unlocks(self) -> None:
        self._write_list_shaped_artifacts()

        payload = observe_achievements(self.paths)

        self.assertEqual(payload["status"], "observed")
        self.assertEqual(payload["errors"], [])
        summary = payload["summary"]
        self.assertEqual(summary["total_count"], 4)
        self.assertEqual(summary["unlocked_count"], 2)
        self.assertEqual(summary["discovered_count"], 1)
        self.assertEqual(summary["secret_count"], 1)
        self.assertEqual(summary["top_tier"], "gold")
        self.assertEqual(summary["last_scan_at"], "2026-07-01T10:00:00Z")
        self.assertEqual(
            summary["categories"],
            [
                {"category": "debugging", "total": 1, "unlocked": 1},
                {"category": "lifestyle", "total": 2, "unlocked": 1},
                {"category": "toolchain", "total": 1, "unlocked": 0},
            ],
        )
        recent_ids = [badge["badge_id"] for badge in payload["recent_unlocks"]]
        self.assertEqual(recent_ids, ["weekend-warrior", "red-text"])
        in_progress = find_badge(payload["badges"], "let-him-cook")
        self.assertIsNotNone(in_progress)
        self.assertEqual(in_progress["progress_percent"], 30.0)

    def test_dict_shaped_snapshot_and_list_shaped_state(self) -> None:
        self._write_plugin_file(
            "scan_snapshot.json",
            {
                "badges": {
                    "toolchain-maxxer": {"title": "Toolchain Maxxer", "tier": "diamond", "group": "toolchain"},
                    "claude-confidant": {"title": "Claude Confidant", "group": "models", "unlocked": True},
                }
            },
        )
        self._write_plugin_file("state.json", {"unlocked": ["toolchain-maxxer"]})

        payload = observe_achievements(self.paths)

        self.assertEqual(payload["summary"]["total_count"], 2)
        self.assertEqual(payload["summary"]["unlocked_count"], 2)
        self.assertEqual(payload["summary"]["top_tier"], "diamond")
        badge = find_badge(payload["badges"], "toolchain-maxxer")
        self.assertEqual(badge["name"], "Toolchain Maxxer")
        self.assertEqual(badge["state"], "unlocked")

    def test_corrupt_snapshot_degrades_to_state_only_observation(self) -> None:
        self.plugin_dir.mkdir(parents=True, exist_ok=True)
        (self.plugin_dir / "scan_snapshot.json").write_text("{not json", encoding="utf-8")
        self._write_plugin_file("state.json", {"unlocked": {"red-text": {"tier": "gold"}}})

        payload = observe_achievements(self.paths)

        self.assertEqual(payload["status"], "observed")
        self.assertFalse(payload["source"]["snapshot_observed"])
        self.assertTrue(payload["source"]["state_observed"])
        self.assertEqual(len(payload["errors"]), 1)
        self.assertIn("scan_snapshot.json", payload["errors"][0])
        self.assertEqual(payload["summary"]["unlocked_count"], 1)

    def test_unknown_shapes_degrade_without_raising(self) -> None:
        self._write_plugin_file("scan_snapshot.json", {"achievements": "not-a-collection", "extra": 5})
        self._write_plugin_file("state.json", {"unlocked": 7})

        payload = observe_achievements(self.paths)

        self.assertEqual(payload["status"], "observed")
        self.assertEqual(payload["badges"], [])
        self.assertEqual(payload["summary"]["unlocked_count"], 0)

    def test_filter_badges_by_category_state_and_limit(self) -> None:
        self._write_list_shaped_artifacts()
        badges = observe_achievements(self.paths)["badges"]

        lifestyle = filter_badges(badges, category="lifestyle")
        self.assertEqual({badge["badge_id"] for badge in lifestyle}, {"night-owl", "weekend-warrior"})
        unlocked = filter_badges(badges, state="unlocked")
        self.assertEqual({badge["badge_id"] for badge in unlocked}, {"red-text", "weekend-warrior"})
        self.assertEqual(len(filter_badges(badges, limit=2)), 2)

    def test_markdown_projection_is_deterministic_metadata_only(self) -> None:
        self._write_list_shaped_artifacts()
        payload = observe_achievements(self.paths)
        payload["generated_at"] = "2026-07-04T00:00:00Z"

        first = render_achievements_markdown(payload)
        second = render_achievements_markdown(payload)

        self.assertEqual(first, second)
        self.assertIn("# Hermes Achievements (OMH observation)", first)
        self.assertIn("- Unlocked: 2 / 4", first)
        self.assertIn("## debugging", first)
        self.assertIn("| Badge | Tier | State | Progress | Unlocked at |", first)
        self.assertIn("Red Text Connoisseur (`red-text`)", first)
        self.assertIn("Boundary:", first)


class PluginDataLayoutTests(HermesAchievementsTestCase):
    def test_plugin_data_layout_is_read_with_upstream_field_meanings(self) -> None:
        self._write_plugin_data_artifacts()

        payload = observe_achievements(self.paths, now=1_760_000_060)

        self.assertEqual(payload["status"], "observed")
        self.assertEqual(payload["errors"], [])
        source = payload["source"]
        self.assertEqual(source["snapshot_location"], "plugin_data")
        self.assertEqual(source["state_location"], "plugin_data")
        self.assertEqual(source["snapshot_path"], str(self.data_dir / "scan_snapshot.json"))
        self.assertEqual(source["state_path"], str(self.data_dir / "state.json"))
        self.assertEqual(source["data_dir"], str(self.data_dir))
        self.assertEqual(source["legacy_dir"], str(self.plugin_dir))
        summary = payload["summary"]
        self.assertEqual(summary["total_count"], 4)
        self.assertEqual(summary["unlocked_count"], 2)
        self.assertEqual(summary["secret_count"], 1)
        self.assertEqual(summary["top_tier"], "silver")
        # A raw count of 1 (or 640) is not a fraction or a percent.
        self.assertEqual(find_badge(payload["badges"], "patch_wizard")["progress_percent"], 0.0)
        # `first_tier` fills a tier the snapshot does not carry and never lowers one it does.
        self.assertEqual(find_badge(payload["badges"], "weekend_warrior")["tier"], "copper")
        self.assertEqual(find_badge(payload["badges"], "let_him_cook")["tier"], "silver")
        # The unlock floor leaves the snapshot's next-tier progress in place.
        self.assertEqual(find_badge(payload["badges"], "let_him_cook")["progress_percent"], 20.0)
        self.assertEqual(find_badge(payload["badges"], "weekend_warrior")["progress_percent"], 100.0)
        self.assertNotIn("PRIVATE SESSION TITLE", json.dumps(payload))

    def test_staleness_follows_the_plugin_ttl_rule_on_an_injected_clock(self) -> None:
        self._write_plugin_data_artifacts()

        at_ttl = observe_achievements(self.paths, now=1_760_000_120)["snapshot_freshness"]
        past_ttl = observe_achievements(self.paths, now=1_760_000_121)["snapshot_freshness"]

        self.assertEqual(at_ttl["generated_at"], 1_760_000_000)
        self.assertEqual(at_ttl["ttl_seconds"], 120)
        self.assertEqual(at_ttl["age_seconds"], 120)
        self.assertFalse(at_ttl["stale"])
        self.assertEqual(past_ttl["age_seconds"], 121)
        self.assertTrue(past_ttl["stale"])

    def test_snapshot_without_generated_at_is_stale(self) -> None:
        self._write_list_shaped_artifacts()

        freshness = observe_achievements(self.paths, now=1_760_000_000)["snapshot_freshness"]

        self.assertIsNone(freshness["generated_at"])
        self.assertIsNone(freshness["age_seconds"])
        self.assertTrue(freshness["stale"])

    def test_zero_generated_at_counts_as_missing(self) -> None:
        # The plugin's own rule treats a non-positive timestamp as no snapshot time.
        self._write_plugin_file("scan_snapshot.json", {"generated_at": 0, "achievements": []}, directory=self.data_dir)

        freshness = observe_achievements(self.paths, now=1_760_000_000)["snapshot_freshness"]

        self.assertIsNone(freshness["generated_at"])
        self.assertIsNone(freshness["age_seconds"])
        self.assertTrue(freshness["stale"])

    def test_numbers_too_large_for_a_float_degrade_instead_of_raising(self) -> None:
        huge = 10**400
        self._write_plugin_file(
            "scan_snapshot.json",
            {
                "generated_at": huge,
                "achievements": [
                    {"id": "pct", "state": "discovered", "progress": 3, "progress_pct": huge},
                    {"id": "raw", "state": "discovered", "progress": huge},
                    {"id": "ratio", "state": "discovered", "progress": {"current": huge, "target": 10}},
                    {"id": "dict_percent", "state": "discovered", "progress": {"percent": huge}},
                    {"id": "dict_target", "state": "discovered", "progress": {"current": 1, "target": huge}},
                ],
            },
            directory=self.data_dir,
        )

        payload = observe_achievements(self.paths, now=1_760_000_000)

        self.assertEqual(payload["status"], "observed")
        self.assertIsNone(payload["snapshot_freshness"]["generated_at"])
        self.assertTrue(payload["snapshot_freshness"]["stale"])
        # An unusable `progress_pct` does not fall back to the raw count.
        self.assertIsNone(find_badge(payload["badges"], "pct")["progress_percent"])
        for badge_id in ("raw", "ratio", "dict_percent", "dict_target"):
            self.assertIsNone(find_badge(payload["badges"], badge_id)["progress_percent"], badge_id)
        status, stdout, stderr = run_cli([*self._base_args(), "context", "brief", "--json"])
        self.assertEqual(status, 0, stderr)
        status, stdout, stderr = run_cli([*self._base_args(), "achievements", "summary"], output_json=False)
        self.assertEqual(status, 0, stderr)

    def test_nan_and_infinity_degrade_instead_of_raising(self) -> None:
        # Python's JSON reader accepts these literals, so write them as raw text.
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "scan_snapshot.json").write_text(
            '{"generated_at": Infinity, "achievements": ['
            '{"id": "pct", "state": "discovered", "progress": 3, "progress_pct": NaN},'
            '{"id": "dict_percent", "state": "discovered", "progress": {"percent": Infinity}},'
            '{"id": "raw", "state": "discovered", "progress": -Infinity}]}',
            encoding="utf-8",
        )

        payload = observe_achievements(self.paths, now=1_760_000_000)

        self.assertIsNone(payload["snapshot_freshness"]["generated_at"])
        self.assertTrue(payload["snapshot_freshness"]["stale"])
        for badge_id in ("pct", "dict_percent", "raw"):
            self.assertIsNone(find_badge(payload["badges"], badge_id)["progress_percent"], badge_id)
        status, _, stderr = run_cli([*self._base_args(), "achievements", "summary"], output_json=False)
        self.assertEqual(status, 0, stderr)
        status, _, stderr = run_cli([*self._base_args(), "context", "brief", "--json"])
        self.assertEqual(status, 0, stderr)

    def test_state_tier_key_raises_a_tier_and_never_lowers_one(self) -> None:
        self._write_plugin_file(
            "scan_snapshot.json",
            {
                "achievements": [
                    {"id": "kept", "state": "unlocked", "tier": "Gold"},
                    {"id": "raised", "state": "unlocked", "tier": "Copper"},
                    {"id": "filled", "state": "discovered"},
                ]
            },
            directory=self.data_dir,
        )
        self._write_plugin_file(
            "state.json",
            {
                "unlocked": {
                    "kept": {"tier": "copper", "first_tier": "copper"},
                    "raised": {"tier": "gold"},
                    "filled": {"tier": "silver", "first_tier": "copper"},
                }
            },
            directory=self.data_dir,
        )

        badges = observe_achievements(self.paths)["badges"]

        self.assertEqual(find_badge(badges, "kept")["tier"], "gold")
        self.assertEqual(find_badge(badges, "raised")["tier"], "gold")
        self.assertEqual(find_badge(badges, "filled")["tier"], "silver")

    def test_legacy_only_install_dir_is_read_and_reported(self) -> None:
        self._write_list_shaped_artifacts()

        source = observe_achievements(self.paths)["source"]

        self.assertEqual(source["snapshot_location"], "legacy_plugins_dir")
        self.assertEqual(source["state_location"], "legacy_plugins_dir")
        self.assertEqual(source["snapshot_path"], str(self.plugin_dir / "scan_snapshot.json"))
        self.assertEqual(source["state_path"], str(self.plugin_dir / "state.json"))

    def test_plugin_data_wins_over_legacy_per_file(self) -> None:
        self._write_list_shaped_artifacts()
        self._write_plugin_file(
            "scan_snapshot.json",
            {"generated_at": 1_760_000_000, "achievements": [{"id": "fresh-badge", "state": "discovered"}]},
            directory=self.data_dir,
        )

        payload = observe_achievements(self.paths)

        # The plugin migrates file by file, so a state.json not yet copied
        # forward is still read from the legacy directory.
        self.assertEqual(payload["source"]["snapshot_location"], "plugin_data")
        self.assertEqual(payload["source"]["state_location"], "legacy_plugins_dir")
        self.assertIsNotNone(find_badge(payload["badges"], "fresh-badge"))
        self.assertIsNone(find_badge(payload["badges"], "let-him-cook"))
        self.assertEqual(payload["snapshot_freshness"]["generated_at"], 1_760_000_000)

    def test_unreadable_plugin_data_file_does_not_fall_back_to_legacy(self) -> None:
        self._write_list_shaped_artifacts()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "scan_snapshot.json").write_text("{not json", encoding="utf-8")

        payload = observe_achievements(self.paths)

        self.assertEqual(payload["source"]["snapshot_location"], "plugin_data")
        self.assertFalse(payload["source"]["snapshot_observed"])
        self.assertEqual(len(payload["errors"]), 1)
        self.assertIsNone(find_badge(payload["badges"], "let-him-cook"))

    def test_markdown_reports_snapshot_freshness(self) -> None:
        self._write_plugin_data_artifacts()

        document = render_achievements_markdown(observe_achievements(self.paths, now=1_760_000_060))

        self.assertIn(
            "- Snapshot: fresh by the plugin's 120s rescan rule (generated 2025-10-09T08:53:20Z, age 60s)",
            document,
        )

    def test_epoch_timestamps_render_as_utc_and_stay_raw_in_json(self) -> None:
        self._write_plugin_data_artifacts()

        payload = observe_achievements(self.paths, now=1_760_000_060)
        document = render_achievements_markdown(payload)

        self.assertEqual(payload["summary"]["last_scan_at"], "1760000000")
        self.assertEqual(find_badge(payload["badges"], "let_him_cook")["unlocked_at"], "1759000000")
        self.assertIn("- Last plugin scan: 2025-10-09T08:53:20Z", document)
        self.assertIn("| Let Him Cook (`let_him_cook`) | silver | unlocked | 20% | 2025-09-27T19:06:40Z |", document)
        self.assertNotIn("1759000000", document)

    def test_iso_timestamps_render_as_written(self) -> None:
        self._write_list_shaped_artifacts()

        document = render_achievements_markdown(observe_achievements(self.paths))

        self.assertIn("- Last plugin scan: 2026-07-01T10:00:00Z", document)
        self.assertIn("| 2026-06-30T09:00:00Z |", document)

    def test_files_saved_with_a_byte_order_mark_are_read(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "scan_snapshot.json").write_text(
            "\ufeff" + json.dumps({"generated_at": 1_760_000_000, "achievements": [{"id": "a", "state": "unlocked"}]}),
            encoding="utf-8",
        )
        (self.data_dir / "state.json").write_text(
            "\ufeff" + json.dumps({"unlocks": {"b": {"unlocked_at": 1_760_000_000}}}),
            encoding="utf-8",
        )

        payload = observe_achievements(self.paths)

        self.assertEqual(payload["errors"], [])
        self.assertTrue(payload["source"]["snapshot_observed"])
        self.assertTrue(payload["source"]["state_observed"])
        self.assertEqual(payload["summary"]["unlocked_count"], 2)


class AgentProfileTests(HermesAchievementsTestCase):
    def test_profile_derived_from_observed_badges(self) -> None:
        self._write_list_shaped_artifacts()

        profile = observe_achievements(self.paths)["agent_profile"]

        self.assertEqual(profile["schema_version"], "hermes_achievements_agent_profile/v1")
        self.assertTrue(profile["observed"])
        self.assertEqual(profile["derivation"], "derived_from_observed_badges")
        self.assertEqual(profile["strengths"], ["debugging", "lifestyle"])
        self.assertEqual(profile["gaps"], ["toolchain"])
        self.assertEqual(profile["top_tier"], "gold")

    def test_agent_summary_file_is_not_read(self) -> None:
        # The bundled plugin has no writer for this file, so a leftover copy
        # in either directory must not stand in for the observed badges.
        self._write_list_shaped_artifacts()
        stray = {"strengths": ["autonomous toolchains"], "gaps": ["skill usage"], "top_tier": "diamond"}
        self._write_plugin_file("agent_summary.json", stray)
        self._write_plugin_file("agent_summary.json", stray, directory=self.data_dir)

        profile = observe_achievements(self.paths)["agent_profile"]

        self.assertEqual(profile["derivation"], "derived_from_observed_badges")
        self.assertEqual(profile["strengths"], ["debugging", "lifestyle"])
        self.assertEqual(profile["top_tier"], "gold")
        self.assertNotIn("source", profile)

    def test_profile_without_artifacts_is_unobserved(self) -> None:
        profile = observe_achievements(self.paths)["agent_profile"]

        self.assertFalse(profile["observed"])
        self.assertEqual(profile["derivation"], "none")
        self.assertEqual(profile["strengths"], [])
        self.assertEqual(profile["gaps"], [])


class ContextBriefInjectionTests(HermesAchievementsTestCase):
    def test_context_brief_includes_profile_when_observed(self) -> None:
        self._write_list_shaped_artifacts()

        status, stdout, stderr = run_cli([*self._base_args(), "context", "brief", "--json"])

        self.assertEqual(status, 0, stderr)
        payload = json.loads(stdout)
        profile = payload["achievements_profile"]
        self.assertEqual(profile["derivation"], "derived_from_observed_badges")
        self.assertEqual(profile["gaps"], ["toolchain"])
        self.assertIn("advisory", profile["routing_rule"])

    def test_context_brief_omits_profile_without_artifacts(self) -> None:
        status, stdout, stderr = run_cli([*self._base_args(), "context", "brief", "--json"])

        self.assertEqual(status, 0, stderr)
        self.assertNotIn("achievements_profile", json.loads(stdout))


class AchievementsCapabilityTests(HermesAchievementsTestCase):
    def test_capability_section_lists_achievement_evidence(self) -> None:
        status, stdout, stderr = run_cli(["capabilities", "list", "--section", "achievement_evidence", "--json"])

        self.assertEqual(status, 0, stderr)
        payload = json.loads(stdout)
        sections = {section["section"]: section["ids"] for section in payload["sections"]}
        self.assertEqual(sections["achievement_evidence"], ["hermes_achievements_observation"])

    def test_capability_inspect_returns_contract(self) -> None:
        status, stdout, stderr = run_cli(
            ["capabilities", "inspect", "hermes_achievements_observation", "--section", "achievements", "--json"]
        )

        self.assertEqual(status, 0, stderr)
        payload = json.loads(stdout)
        capability = payload["capability"]
        self.assertEqual(capability["schema_version"], "achievement_evidence_contract/v1")
        self.assertEqual(capability["claim_kind"], "observed_badge_metadata")
        self.assertIn("productivity", capability["not_evidence_for"])


class AchievementsRoutingTests(HermesAchievementsTestCase):
    def test_route_hint_matches_badge_questions(self) -> None:
        status, stdout, stderr = run_cli(["chat", "route-hint", "show my unlocked badges", "--json"])

        self.assertEqual(status, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["route_hint"]["selected_workflow"], "achievements")


class AchievementsCliTests(HermesAchievementsTestCase):
    def test_summary_json_reports_not_observed_without_artifacts(self) -> None:
        status, stdout, stderr = run_cli([*self._base_args(), "achievements", "summary", "--json"])

        self.assertEqual(status, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["schema_version"], "hermes_achievements_observation/v1")
        self.assertEqual(payload["status"], "not_observed")

    def test_summary_text_without_artifacts_names_both_directories(self) -> None:
        status, stdout, stderr = run_cli([*self._base_args(), "achievements", "summary"], output_json=False)

        self.assertEqual(status, 0, stderr)
        # The CLI resolves `--hermes-home`, so compare resolved paths.
        self.assertIn(f"Looked in: {self.data_dir.resolve()}, then {self.plugin_dir.resolve()}", stdout)

    def test_summary_text_reports_plugin_data_snapshot_staleness(self) -> None:
        self._write_plugin_data_artifacts()

        status, stdout, stderr = run_cli([*self._base_args(), "achievements", "summary"], output_json=False)

        self.assertEqual(status, 0, stderr)
        self.assertIn("Unlocked: 2 / 4", stdout)
        self.assertIn("Snapshot: stale by the plugin's 120s rescan rule (generated 2025-10-09T08:53:20Z", stdout)
        self.assertIn("Last plugin scan: 2025-10-09T08:53:20Z", stdout)
        self.assertIn("- weekend_warrior [copper] (2025-10-03T14:00:00Z)", stdout)

    def test_show_text_renders_unlock_time_as_utc(self) -> None:
        self._write_plugin_data_artifacts()

        status, stdout, stderr = run_cli(
            [*self._base_args(), "achievements", "show", "let_him_cook"], output_json=False
        )

        self.assertEqual(status, 0, stderr)
        self.assertIn("Unlocked At: 2025-09-27T19:06:40Z", stdout)

    def test_summary_text_prints_unlocks_and_boundary(self) -> None:
        self._write_list_shaped_artifacts()

        status, stdout, stderr = run_cli([*self._base_args(), "achievements", "summary"], output_json=False)

        self.assertEqual(status, 0, stderr)
        self.assertIn("Unlocked: 2 / 4", stdout)
        self.assertIn("Top tier: gold", stdout)
        self.assertIn("Weekend Warrior", stdout)
        self.assertIn("Boundary:", stdout)

    def test_list_applies_filters(self) -> None:
        self._write_list_shaped_artifacts()

        status, stdout, stderr = run_cli(
            [*self._base_args(), "achievements", "list", "--state", "unlocked", "--json"]
        )

        self.assertEqual(status, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["count"], 2)
        self.assertEqual(
            {badge["badge_id"] for badge in payload["badges"]},
            {"red-text", "weekend-warrior"},
        )

    def test_show_returns_badge_and_fails_cleanly_when_missing(self) -> None:
        self._write_list_shaped_artifacts()

        status, stdout, stderr = run_cli([*self._base_args(), "achievements", "show", "red-text", "--json"])
        self.assertEqual(status, 0, stderr)
        self.assertEqual(json.loads(stdout)["badge"]["tier"], "gold")

        status, _, stderr = run_cli([*self._base_args(), "achievements", "show", "no-such-badge", "--json"])
        self.assertEqual(status, 2)
        self.assertIn("badge not found", stderr)

    def test_export_writes_markdown_file(self) -> None:
        self._write_list_shaped_artifacts()
        out_path = Path(self._tmp.name) / "achievements.md"

        status, stdout, stderr = run_cli(
            [*self._base_args(), "achievements", "export", "--format", "md", "--out", str(out_path), "--json"]
        )

        self.assertEqual(status, 0, stderr)
        receipt = json.loads(stdout)
        self.assertEqual(receipt["written_path"], str(out_path))
        content = out_path.read_text(encoding="utf-8")
        self.assertIn("# Hermes Achievements (OMH observation)", content)
        self.assertIn("Boundary:", content)

    def test_export_json_stdout_parses(self) -> None:
        self._write_list_shaped_artifacts()

        status, stdout, stderr = run_cli(
            [*self._base_args(), "achievements", "export", "--format", "json"], output_json=False
        )

        self.assertEqual(status, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["summary"]["unlocked_count"], 2)


class AchievementsHudTests(HermesAchievementsTestCase):
    def test_full_preset_hud_includes_achievements_segment(self) -> None:
        self._write_list_shaped_artifacts()

        status, stdout, stderr = run_cli([*self._base_args(), "hud", "--preset", "full", "--json"])

        self.assertEqual(status, 0, stderr)
        payload = json.loads(stdout)
        self.assertTrue(payload["achievements"]["observed"])
        self.assertEqual(payload["achievements"]["unlocked_count"], 2)
        self.assertEqual(payload["achievements"]["total_count"], 3)
        self.assertIn("ach:2/3", payload["display"]["segments"])

    def test_full_preset_hud_reads_plugin_data_layout(self) -> None:
        self._write_plugin_data_artifacts()

        status, stdout, stderr = run_cli([*self._base_args(), "hud", "--preset", "full", "--json"])

        self.assertEqual(status, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(
            payload["achievements"],
            {"observed": True, "unlocked_count": 2, "total_count": 3, "total_status": "observed"},
        )
        self.assertIn("ach:2/3", payload["display"]["segments"])

    def _write_oversized_snapshot(self, *, sessions_first: bool) -> None:
        # Shaped like the plugin's own file: per-session stats for a long
        # history make it far larger than the HUD read cap.
        sessions = [
            {"session_id": f"s-{index:05d}", "title": f"session {index}", "tool_call_count": index}
            for index in range(6000)
        ]
        badges = [
            {"id": "let_him_cook", "state": "unlocked", "unlocked": True},
            {"id": "patch_wizard", "state": "discovered", "unlocked": False},
            {"id": "port_3000_taken", "state": "secret", "unlocked": False},
        ]
        members = {"generated_at": 1_760_000_000, "total_count": 3, "unlocked_count": 1}
        if sessions_first:
            text = json.dumps({"sessions": sessions, "achievements": badges, **members}, indent=2)
        else:
            text = json.dumps({"achievements": badges, "sessions": sessions, **members}, indent=2, sort_keys=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.data_dir / "scan_snapshot.json"
        path.write_text(text, encoding="utf-8")
        self.assertGreater(path.stat().st_size, runtime_reader.MAX_HUD_METADATA_BYTES)
        self._write_plugin_file(
            "state.json",
            {"unlocks": {"let_him_cook": {"unlocked_at": 1}, "weekend_warrior": {"unlocked_at": 2}}},
            directory=self.data_dir,
        )

    def test_full_preset_hud_counts_badges_in_a_snapshot_over_the_read_cap(self) -> None:
        self._write_oversized_snapshot(sessions_first=False)

        status, stdout, stderr = run_cli([*self._base_args(), "hud", "--preset", "full", "--json"])

        self.assertEqual(status, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(
            payload["achievements"],
            {"observed": True, "unlocked_count": 2, "total_count": 3, "total_status": "observed"},
        )
        self.assertIn("ach:2/3", payload["display"]["segments"])
        self.assertNotIn("s-00001", stdout)

    def test_full_preset_hud_reports_an_unreadable_total_as_not_observed(self) -> None:
        self._write_oversized_snapshot(sessions_first=True)

        status, stdout, stderr = run_cli([*self._base_args(), "hud", "--preset", "full", "--json"])

        self.assertEqual(status, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(
            payload["achievements"],
            {"observed": True, "unlocked_count": 2, "total_count": None, "total_status": "not_observed"},
        )
        self.assertIn("ach:2", payload["display"]["segments"])
        self.assertFalse(any(segment.startswith("ach:2/") for segment in payload["display"]["segments"]))

    def test_hud_reads_a_state_file_saved_with_a_byte_order_mark(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "state.json").write_text(
            "\ufeff" + json.dumps({"unlocks": {"a": {}, "b": {}}}), encoding="utf-8"
        )
        (self.data_dir / "scan_snapshot.json").write_text(
            "\ufeff" + json.dumps({"achievements": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}), encoding="utf-8"
        )

        status, stdout, stderr = run_cli([*self._base_args(), "hud", "--preset", "full", "--json"])

        self.assertEqual(status, 0, stderr)
        self.assertIn("ach:2/3", json.loads(stdout)["display"]["segments"])

    def test_leading_member_reader_never_takes_a_member_the_cap_cuts_off(self) -> None:
        path = self.hermes_home / "doc.json"
        self.hermes_home.mkdir(parents=True, exist_ok=True)
        text = '{"achievements": [1], "count": 123456789}'
        path.write_text(text, encoding="utf-8")
        cut = text.index("123456789") + 5

        with mock.patch.object(runtime_reader, "MAX_HUD_METADATA_BYTES", cut):
            members = runtime_reader._read_hud_json_leading_members(path, root=self.hermes_home)

        self.assertEqual(members, {"achievements": [1]})

    def test_leading_member_reader_survives_a_cap_inside_a_multibyte_character(self) -> None:
        path = self.hermes_home / "doc.json"
        self.hermes_home.mkdir(parents=True, exist_ok=True)
        raw = '{"achievements": [1], "title": "\u00e9\u00e9"}'.encode("utf-8")
        cut = raw.index("\u00e9".encode("utf-8")) + 1
        path.write_bytes(raw)

        with mock.patch.object(runtime_reader, "MAX_HUD_METADATA_BYTES", cut):
            members = runtime_reader._read_hud_json_leading_members(path, root=self.hermes_home)

        self.assertEqual(members, {"achievements": [1]})

    def test_hud_snapshot_without_a_badge_list_and_no_state_is_not_observed(self) -> None:
        self._write_plugin_file(
            "scan_snapshot.json", {"generated_at": 1_760_000_000, "sessions": []}, directory=self.data_dir
        )

        status, stdout, stderr = run_cli([*self._base_args(), "hud", "--preset", "full", "--json"])

        self.assertEqual(status, 0, stderr)
        payload = json.loads(stdout)
        self.assertFalse(payload["achievements"]["observed"])
        self.assertIsNone(payload["achievements"]["unlocked_count"])
        self.assertFalse(any(segment.startswith("ach:") for segment in payload["display"]["segments"]))

    def test_hud_survives_a_badge_list_nested_past_the_recursion_limit(self) -> None:
        depth = 100_000
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "scan_snapshot.json").write_text(
            '{"achievements": ' + "[" * depth + "]" * depth + "}", encoding="utf-8"
        )

        summary = runtime_reader._achievements_summary(self.hermes_home)

        self.assertEqual(
            summary,
            {"observed": False, "unlocked_count": None, "total_count": None, "total_status": "not_observed"},
        )

    def test_leading_member_reader_applies_the_hidden_content_filter(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.data_dir / "scan_snapshot.json"
        path.write_text(json.dumps({"achievements": [{"id": "a", "reasoning": "hidden"}]}), encoding="utf-8")

        members = runtime_reader._read_hud_json_leading_members(path, root=self.hermes_home)

        self.assertEqual(members, {})

    def test_leading_member_reader_survives_recursion_in_the_hidden_content_filter(self) -> None:
        # The filter recurses in Python, so a list the C decoder accepts can
        # still be too deep for it; how deep depends on the interpreter.
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.data_dir / "scan_snapshot.json"
        path.write_text(json.dumps({"achievements": [{"id": "a"}]}), encoding="utf-8")

        with mock.patch.object(runtime_reader, "_has_raw_or_hidden_content", side_effect=RecursionError):
            members = runtime_reader._read_hud_json_leading_members(path, root=self.hermes_home)

        self.assertEqual(members, {})

    def test_full_preset_hud_prefers_plugin_data_over_legacy(self) -> None:
        self._write_list_shaped_artifacts()
        self._write_plugin_file(
            "scan_snapshot.json",
            {"generated_at": 1_760_000_000, "achievements": [{"id": "only", "state": "unlocked"}]},
            directory=self.data_dir,
        )
        self._write_plugin_file("state.json", {"unlocks": {"only": {"unlocked_at": 1_760_000_000}}}, directory=self.data_dir)

        status, stdout, stderr = run_cli([*self._base_args(), "hud", "--preset", "full", "--json"])

        self.assertEqual(status, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(
            payload["achievements"],
            {"observed": True, "unlocked_count": 1, "total_count": 1, "total_status": "observed"},
        )
        self.assertIn("ach:1/1", payload["display"]["segments"])

    def test_focused_preset_hud_omits_achievements_segment(self) -> None:
        self._write_list_shaped_artifacts()

        status, stdout, stderr = run_cli([*self._base_args(), "hud", "--json"])

        self.assertEqual(status, 0, stderr)
        payload = json.loads(stdout)
        self.assertNotIn("ach:2/3", payload["display"]["segments"])

    def test_hud_without_artifacts_reports_unobserved_achievements(self) -> None:
        status, stdout, stderr = run_cli([*self._base_args(), "hud", "--preset", "full", "--json"])

        self.assertEqual(status, 0, stderr)
        payload = json.loads(stdout)
        self.assertFalse(payload["achievements"]["observed"])
        self.assertNotIn("ach:", payload["display"]["line"])


if __name__ == "__main__":
    unittest.main()
