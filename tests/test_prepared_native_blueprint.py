"""`automation-blueprint` prepares Hermes' own Automation Blueprint, under its own name.

Hermes ships Automation Blueprints: a skill whose frontmatter carries
`metadata.hermes.blueprint`, activated by `/blueprint`, by accepting a Suggested
Cron Job, or by a `cronjob_manage` create. OMH's record used to be called
`hermes_ops_blueprint/v1` and carried none of those fields, so two different
things shared one name. These tests pin three things: the renamed record
projects onto the native block and cron call, records already sitting in a
user's `.omh/hermes-ops/` store stay readable, and the skill maps OMH's
failure decisions onto what Hermes cron does instead of asking for policies the
scheduler ignores.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _local_package import load_local_package

load_local_package()
from omh.hermes_ops import (
    build_scheduled_ops_blueprint,
    list_hermes_ops_blueprints,
    show_hermes_ops_blueprint,
    validate_hermes_ops_blueprint,
    validate_hermes_ops_store,
)
from omh.paths import OmhPaths
from omh.skills.catalog import builtin_definitions, omh_skill_display_name
from omh.skills.packaging import builtin_skill_templates
from omh.skills.render import automation_blueprint_reference_templates
from omh.workflows.recurring_intents import (
    FAILURE_POLICY_DECISIONS,
    UNSET_POSTURE,
    build_recurring_intent,
    validate_recurring_intent,
)

# The fields Hermes' `tools/blueprints.py` reads from `metadata.hermes.blueprint`
# at both the plugin floor (tag v2026.9.7) and upstream main 63d0b94120. Written
# out here rather than imported so a widened OMH tuple cannot pass by agreeing
# with itself.
HERMES_BLUEPRINT_FIELDS = {"schedule", "deliver", "prompt", "no_agent", "model", "provider", "enabled_toolsets"}
LEGACY_RECORD = Path("tests/fixtures/hermes_ops_blueprint_v1_before_native_rename.json")
CHANGE_ONLY_REQUEST = "every morning check competitor news and send a Slack digest only if something changed"


def _paths_from_tmp(tmp: str) -> OmhPaths:
    root = Path(tmp)
    return OmhPaths(root / ".omh", root / ".hermes")


def _definition():
    return next(item for item in builtin_definitions() if item.name == "automation-blueprint")


def _reference() -> str:
    (template,) = automation_blueprint_reference_templates()
    return template.content


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    return text[start:] if end == -1 else text[start:end]


class PreparedNativeBlueprintRecordTests(unittest.TestCase):
    def test_record_projects_onto_the_native_block_and_cron_call(self) -> None:
        blueprint = build_scheduled_ops_blueprint(CHANGE_ONLY_REQUEST, created_at="2026-06-16T00:00:00Z")
        native = blueprint["native_blueprint"]
        create = native["cronjob_manage_create"]

        self.assertEqual(blueprint["schema_version"], "prepared_native_blueprint/v1")
        self.assertEqual(native["frontmatter_key"], "metadata.hermes.blueprint")
        self.assertLessEqual(set(native["block"]), HERMES_BLUEPRINT_FIELDS)
        self.assertEqual(native["block"]["deliver"], "slack")
        self.assertTrue(
            native["block"]["prompt"].endswith("If nothing changed since the previous run, reply with [SILENT] alone.")
        )
        self.assertEqual(create["action"], "create")
        self.assertEqual(create["prompt"], native["block"]["prompt"])
        self.assertNotIn("model", create)
        self.assertNotIn("provider", create)
        self.assertIs(create["continuity"], True)
        suggested = [item["skill"] for item in blueprint["skill_suggestions"] if item["skill"] != "automation-blueprint"]
        self.assertEqual(create["skills"], [omh_skill_display_name(name) for name in suggested])
        self.assertEqual(native["needs_confirmation"], ["schedule", "deliver"])
        self.assertEqual(native["activation_owner"], "user_or_hermes")
        self.assertEqual(validate_hermes_ops_blueprint(blueprint), [])

    def test_current_thread_delivery_is_origin_and_a_settled_schedule_passes_through(self) -> None:
        blueprint = build_scheduled_ops_blueprint(
            "daily status digest at 9am",
            schedule="every day at 9am",
            created_at="2026-06-16T00:00:00Z",
        )
        native = blueprint["native_blueprint"]

        self.assertEqual(native["block"]["deliver"], "origin")
        self.assertEqual(native["block"]["schedule"], "every day at 9am")
        self.assertEqual(native["block"]["prompt"], blueprint["request_summary"])
        self.assertNotIn("continuity", native["cronjob_manage_create"])
        # Nothing said whether an unchanged run should stay quiet, so the
        # prompt is not settled either: it is where that rule would go.
        self.assertTrue(blueprint["silence_policy"]["requires_confirmation"])
        self.assertEqual(native["needs_confirmation"], ["prompt"])

    def test_the_skills_own_good_example_reports_only_on_change(self) -> None:
        prompt = _definition().good_example.prompt
        self.assertIn("only if status changes", prompt)
        blueprint = build_scheduled_ops_blueprint(prompt, created_at="2026-06-16T00:00:00Z")
        native = blueprint["native_blueprint"]

        self.assertEqual(blueprint["silence_policy"]["mode"], "only_report_changes")
        self.assertFalse(blueprint["silence_policy"]["requires_confirmation"])
        self.assertTrue(
            native["block"]["prompt"].endswith("If nothing changed since the previous run, reply with [SILENT] alone.")
        )
        self.assertNotIn("prompt", native["needs_confirmation"])

    def test_an_unsettled_silence_rule_leaves_the_prompt_to_confirm(self) -> None:
        blueprint = build_scheduled_ops_blueprint(
            "every weekday run an uptime check and send a Slack digest",
            created_at="2026-06-16T00:00:00Z",
        )

        self.assertEqual(blueprint["silence_policy"]["mode"], "report_each_run")
        self.assertTrue(blueprint["silence_policy"]["requires_confirmation"])
        self.assertIn("prompt", blueprint["native_blueprint"]["needs_confirmation"])

    def test_a_long_request_stores_only_the_preview_and_leaves_the_prompt_open(self) -> None:
        # The record is metadata-only by default, so it never keeps the raw
        # request. A preview is not a self-contained cron prompt, so the
        # prompt stays open and is written from the conversation at activation.
        request = (
            "every morning "
            + "check the competitor pricing page, the release notes feed, and the vendor status page for each supplier; " * 4
            + "then summarise what moved and why it matters for our renewal decisions this quarter."
        )
        clean = " ".join(request.split())
        self.assertGreater(len(clean), 400)
        blueprint = build_scheduled_ops_blueprint(
            request, silence="only if something changed", created_at="2026-06-16T00:00:00Z"
        )
        native = blueprint["native_blueprint"]

        self.assertFalse(blueprint["silence_policy"]["requires_confirmation"])
        self.assertTrue(blueprint["request_summary"].endswith("..."))
        self.assertTrue(native["block"]["prompt"].startswith(blueprint["request_summary"]))
        self.assertEqual(native["cronjob_manage_create"]["prompt"], native["block"]["prompt"])
        self.assertNotIn(clean, json.dumps(blueprint, ensure_ascii=False))
        self.assertIn("prompt", native["needs_confirmation"])

    def test_reference_says_the_stored_prompt_is_a_bounded_preview(self) -> None:
        section = " ".join(_section(_reference(), "## The native blueprint").split())

        self.assertIn(
            "The stored prompt is a bounded preview of the request, not a self-contained cron prompt: while `prompt` "
            "is open, write the full prompt from the request in the conversation when the job is created.",
            section,
        )

    def test_validator_refuses_a_native_record_that_drops_or_bends_the_projection(self) -> None:
        cases = (
            ("dropped", lambda record: record.pop("native_blueprint"), "native_blueprint must be an object"),
            (
                "foreign frontmatter key",
                lambda record: record["native_blueprint"].update(frontmatter_key="metadata.omh.blueprint"),
                "native_blueprint.frontmatter_key must be metadata.hermes.blueprint",
            ),
            (
                "field Hermes does not read",
                lambda record: record["native_blueprint"]["block"].update(skills=["omh-research-brief"]),
                "native_blueprint.block may hold only Hermes blueprint fields",
            ),
            (
                "model pin",
                lambda record: record["native_blueprint"]["cronjob_manage_create"].update(model="any-model"),
                "native_blueprint.cronjob_manage_create must not pin a model",
            ),
            (
                "provider pin alone",
                lambda record: record["native_blueprint"]["cronjob_manage_create"].update(provider="any-provider"),
                "native_blueprint.cronjob_manage_create must not pin a model",
            ),
            (
                "pinned flag",
                lambda record: record["native_blueprint"]["cronjob_manage_create"].update(pinned=True),
                "native_blueprint.cronjob_manage_create must not pin a model",
            ),
            (
                "argument cronjob_manage does not take",
                lambda record: record["native_blueprint"]["cronjob_manage_create"].update(base_url="https://example.invalid"),
                "native_blueprint.cronjob_manage_create may hold only the arguments the cronjob_manage schema advertises",
            ),
            (
                "not a create call",
                lambda record: record["native_blueprint"]["cronjob_manage_create"].update(action="run"),
                "native_blueprint.cronjob_manage_create must be a cronjob_manage create call",
            ),
            (
                "OMH as activator",
                lambda record: record["native_blueprint"].update(activation_owner="omh"),
                "native_blueprint.activation_owner must be user_or_hermes; OMH creates no cron job",
            ),
        )
        for label, mutate, expected in cases:
            with self.subTest(label=label):
                blueprint = build_scheduled_ops_blueprint(CHANGE_ONLY_REQUEST, created_at="2026-06-16T00:00:00Z")
                mutate(blueprint)

                self.assertIn(expected, "; ".join(validate_hermes_ops_blueprint(blueprint)))


class LegacyBlueprintCompatTests(unittest.TestCase):
    """A record or index written before the rename is the user's state and stays valid."""

    def test_a_record_written_before_the_rename_still_validates(self) -> None:
        legacy = json.loads(LEGACY_RECORD.read_text(encoding="utf-8"))

        self.assertEqual(legacy["schema_version"], "hermes_ops_blueprint/v1")
        self.assertNotIn("native_blueprint", legacy)
        self.assertEqual(validate_hermes_ops_blueprint(legacy), [])

    def test_a_store_written_before_the_rename_still_lists_shows_and_validates(self) -> None:
        legacy = json.loads(LEGACY_RECORD.read_text(encoding="utf-8"))
        with TemporaryDirectory() as tmp:
            paths = _paths_from_tmp(tmp)
            paths.hermes_ops_blueprints_dir.mkdir(parents=True)
            (paths.hermes_ops_blueprints_dir / f"{legacy['blueprint_id']}.json").write_text(
                LEGACY_RECORD.read_text(encoding="utf-8"), encoding="utf-8"
            )
            paths.hermes_ops_index_path.write_text(
                json.dumps({"schema_version": "hermes_ops_blueprint_index/v1", "authority": "cache_only", "blueprints": []}),
                encoding="utf-8",
            )

            validation = validate_hermes_ops_store(paths)

            self.assertEqual(validation["errors"], [])
            self.assertTrue(validation["ok"])
            self.assertEqual(validation["blueprint_count"], 1)
            self.assertEqual(len(list_hermes_ops_blueprints(paths)), 1)
            self.assertEqual(show_hermes_ops_blueprint(paths, legacy["blueprint_id"])["schema_version"], "hermes_ops_blueprint/v1")

    def test_an_unknown_schema_id_is_still_refused(self) -> None:
        blueprint = build_scheduled_ops_blueprint(CHANGE_ONLY_REQUEST, created_at="2026-06-16T00:00:00Z")
        blueprint["schema_version"] = "hermes_ops_blueprint/v2"

        self.assertIn(
            "schema_version must be prepared_native_blueprint/v1 (or legacy hermes_ops_blueprint/v1)",
            validate_hermes_ops_blueprint(blueprint),
        )

    def test_a_recurring_intent_saved_before_the_rename_still_validates(self) -> None:
        intent = build_recurring_intent(CHANGE_ONLY_REQUEST, created_at="2026-06-16T00:00:00Z")
        self.assertEqual(intent["derived_from"]["schema_version"], "prepared_native_blueprint/v1")

        intent["derived_from"]["schema_version"] = "hermes_ops_blueprint/v1"
        self.assertEqual(validate_recurring_intent(intent), [])

        intent["derived_from"]["schema_version"] = "scheduled_ops/v1"
        self.assertIn(
            "derived_from.schema_version must be one of prepared_native_blueprint/v1, hermes_ops_blueprint/v1",
            validate_recurring_intent(intent),
        )


class SkillMapsOntoNativeCronTests(unittest.TestCase):
    def test_quality_bar_states_the_cron_tool_presence_rule_and_its_fallback(self) -> None:
        self.assertTrue(
            any(
                "the matching `cronjob_manage` create call when `cronjob_manage` is in your tool list or tool_search "
                "finds it, otherwise give the user the equivalent `hermes cron create` command;"
                in line
                for line in _definition().quality_bar
            )
        )

    def test_quality_bar_names_where_a_model_pin_is_set(self) -> None:
        self.assertTrue(
            any(
                "A model pin is the user's to set: the block's own `model` and `provider` keys, `hermes cron create` or "
                "`hermes cron edit` with `--model` and `--provider`, or `pinned=true` only on the user's explicit request "
                "where `cronjob_manage` offers it."
                in line
                for line in _definition().quality_bar
            )
        )

    def test_reference_states_the_routes_into_hermes_and_where_pins_live(self) -> None:
        section = " ".join(_section(_reference(), "## The native blueprint").split())

        self.assertIn(
            "Hermes reads it only from a skill's frontmatter, and a job made from it preloads that one skill and "
            "carries no `continuity`",
            section,
        )
        self.assertIn(
            "otherwise handed to the user as the equivalent "
            '`hermes cron create "<schedule>" "<prompt>" --deliver <target> --skill <name> --continuity` command',
            section,
        )
        self.assertIn("`cronjob_manage` takes no `model` or `provider` argument, and the prepared call never pins.", section)
        self.assertIn("`enabled_toolsets` is left unset, which keeps every default tool;", section)
        self.assertNotIn("does not take one from the agent", section)

    def test_every_failure_posture_is_mapped_onto_hermes_cron(self) -> None:
        section = _section(_reference(), "### What Hermes cron already decides")
        for row in ("| Overlap |", "| Missed run |", "| Retry |", "| Backfill |", "| Failure pause |"):
            with self.subTest(row=row):
                self.assertIn(row, section)
        for decision in FAILURE_POLICY_DECISIONS:
            for posture in decision.choices:
                if posture == UNSET_POSTURE:
                    continue
                with self.subTest(decision=decision.decision, posture=posture):
                    self.assertIn(f"`{posture}`", section)

    def test_postures_hermes_cron_cannot_honour_are_named_as_such(self) -> None:
        section = _section(_reference(), "### What Hermes cron already decides")

        self.assertIn("`queue_after_running` and `allow_concurrent` are not honoured.", section)
        self.assertIn("`retry_bounded` is not honoured outside that narrow case.", section)
        self.assertIn("`pause_after_consecutive_failures` holds only when someone acts on that notice.", section)

    def test_the_two_one_off_run_paths_are_told_apart(self) -> None:
        section = _section(_reference(), "### What Hermes cron already decides")

        self.assertIn(
            "One extra run is `cronjob_manage` action `run`, which fires at once in the background, or "
            "`hermes cron run`, which runs it on the scheduler's next tick.",
            section,
        )

    def test_post_floor_scheduler_features_are_presence_rules_not_versions(self) -> None:
        reference = _reference()
        body = next(template.content for template in builtin_skill_templates() if template.name == "automation-blueprint")

        self.assertIn("Where the installed Hermes ships `cron/unreachable_retry.py`,", reference)
        self.assertIn(
            "a recurring job's fire that failed on a transient network or DNS error before any model call",
            " ".join(reference.split()),
        )
        self.assertIn("`cron.retry_unreachable: false` turns it off", reference)
        self.assertIn("Some builds also read a global `cron.catch_up_missed` switch", reference)
        version = re.compile(r"\b0\.2\d\.\d|\bv2026\.\d")
        self.assertIsNone(version.search(reference))
        self.assertIsNone(version.search(body))

    def test_activation_is_the_users_or_hermes_never_omh(self) -> None:
        self.assertTrue(
            any(rule.startswith("OMH creates, pauses, and removes no cron job:") for rule in _definition().safety_rules)
        )
        self.assertIn("Activation is never OMH's", _section(_reference(), "## The native blueprint"))


if __name__ == "__main__":
    unittest.main()
