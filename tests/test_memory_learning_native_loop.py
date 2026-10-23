"""Contracts for the memory and learning skills that read Hermes' own loop.

Hermes holds some memory and skill writes for the person's approval under
`pending/`, its background review writes skills and memory after a turn, every
skill write lands in an audit ledger, and it runs one external memory provider
at a time. The memory and learning skills now read those surfaces as observed
evidence. These pin:

* the seven feature-surface skills state their own Why and Do-Not-Use;
* each skill's native-loop clause, as one whole sentence the model reads, in
  every projection the skill ships in;
* Hermes' `write_approval` is Hermes' gate on Hermes' writes: no body routes an
  `omh_memory` capture through a person or proposes turning the gate on;
* a write result carrying `staged: true` is never reported as landed;
* no body offers use or load counts as a reason to prune, archive, or retire a
  skill;
* memory-sync takes Hermes' memory caps from the plugin bundle's reader rather
  than restating them.
"""

from __future__ import annotations

import re
import unittest
from unittest import mock

from _local_package import load_local_package
from _skill_sentences import sentences

load_local_package()
from omh.plugin_bundle.omh.hermes_memory import HERMES_MEMORY_FILES
from omh.skills import render
from omh.skills.catalog import builtin_definitions, omh_skill_display_name
from omh.skills.catalog_portable import portable_skill_names
from omh.skills.catalog_types import _feature_surface_skill
from omh.skills.packaging import builtin_skill_templates
from omh.skills.render import agent_skill_templates

# Feature-surface skills that state their own Why and Do-Not-Use in this change.
OWN_WHY_FEATURE_SURFACE_SKILLS = (
    "memory-sync",
    "external-connector-readiness",
    "prompt-import-readiness",
    "instinct-ledger",
    "skill-scout",
    "skill-health",
    "workflow-learning",
)

# Every skill whose body this change wrote native-loop text into.
NATIVE_LOOP_SKILLS = (*OWN_WHY_FEATURE_SURFACE_SKILLS, "rules-distill")

MEMORY_SYNC_PENDING = (
    "Read the directory whatever `memory.write_approval` says now, since a record stays there until the person "
    "decides it. A pending write is not in `MEMORY.md` or `USER.md` yet: review it as a proposal, never as a "
    "remembered claim, and leave approving or rejecting it to the person (`/memory approve <id>` or "
    "`/memory reject <id>`)."
)
MEMORY_SYNC_STAGED = (
    "When the native write returns `staged: true` with a `pending_id`, nothing was saved even though `success` is "
    "true: Hermes' own write approval, the user's setting, is holding it, so report it as staged and waiting for the "
    "person's `/memory approve <id>`, never as an applied write, and do not repeat the write."
)
MEMORY_SYNC_DECLINED = (
    "When Hermes asks the person before the write and they decline, the error result saved nothing: report that "
    "entry as declined and do not retry it."
)
# Owner directive: OMH memory needs no operator step. Hermes' gate never
# reaches the model's own capture.
MEMORY_SYNC_CAPTURE_NOT_GATED = (
    "Hermes' `write_approval` gates only Hermes' own memory and skill writes, never this capture: OMH's own policy "
    "approves a safe capture at once (`approved_auto_safe`), and a capture waits for review only when its result "
    "names a `review_reason`."
)
MEMORY_SYNC_HANDS_OFF = (
    "A write Hermes holds in `pending/memory/` is the person's to decide: OMH never approves, rejects, edits, or "
    "deletes one, and never proposes turning `memory.write_approval` on or off."
)
PROVIDER_SLOT = (
    "On Hermes, the memory provider is one slot: Hermes runs its built-in memory and no more than one external provider, "
    "the one `memory.provider` names, so adopting another provider (honcho or mem0, for example) where that key reads "
    "`omh` displaces OMH's provider. OMH's per-turn recall and dreaming reminders then stop, and the `omh_memory` tool "
    "stays registered but refuses to recall or write records (`session_lens_unavailable`), because the session lens it "
    "reads comes from the provider. State that displacement in the posture before adoption, and keep the switch a "
    "proposed `memory.provider` diff the user applies."
)
INSTALL_SCANS = (
    "an MCP server started through `npx`, `uvx`, or `pipx` is checked against OSV malware advisories and refused on a "
    "match with an error that reads `MCP server '<name>': BLOCKED: ...`. A scan counts only when its output was seen: "
    "the plugin scan does not run with `plugins.scan_on_install` off, and the OSV check covers known malware only and "
    "lets the server start when its lookup fails, so a quiet install or launch is not a clean result."
)
IMPORT_PREVIEW = (
    "Seed `prompt_source_inventory/v1` from the user's `hermes import-agent claude-code --dry-run` (or `codex`) "
    "preview, which imports nothing (it only writes a default `config.yaml` when the Hermes home has none): it reports "
    "Claude Code's `commands/` directory as one skipped item with no direct Hermes equivalent, so this card enumerates "
    "the `*.md` files in it itself, and it reads no Codex prompt files at all, so those come from the files the user "
    "names."
)
SLASH_PRECEDENCE = (
    "In the Hermes CLI a slash name resolves to a built-in command first, then a quick command, a plugin command, a "
    "skill bundle, and a skill, so `slash_command_collision_report/v1` checks each candidate name against all five in "
    "that order."
)
SCOUT_TRUST = (
    "Keep the trust each shows (`official` for an official-source row, otherwise `builtin`, `trusted`, or "
    "`community`, highest first) beside the candidate as its source's standing, never as a review of its content, and "
    "record a scan verdict (`safe`, `caution`, or `dangerous`) only from install or `hermes skills audit` output that "
    "was seen, so a candidate nobody installed has none."
)
# Owner directive: skill breadth is the product; usage never decides removal.
HEALTH_NO_USAGE_PRUNE = (
    "Never present use, view, or load counts, `.usage.json` included, as a health, prune, archive, or retire signal: "
    "a rarely used skill is not an unhealthy one, and no health card recommends removing, archiving, or pruning a "
    "skill for being used little."
)
HEALTH_LEDGER = (
    "An empty ledger is unknown, not clean, because `skills.ledger: false` turns it off."
)
HEALTH_LINT = (
    "On Hermes, record an authoring finding only from the `lint_warnings` a `skill_manage` write returned, which are "
    "advisory and never proof a skill works; only some writes return them (an `edit` never does), so a write without "
    "them is not a clean lint."
)
LEARNING_UNREVIEWED = (
    "Read Hermes' skill audit ledger read-only (`skills/.curator_ledger.jsonl` under the active profile's Hermes home, "
    "or `hermes curator ledger --limit <n>`, which lists only the 20 newest entries by default) and report each entry "
    "whose `actor` is `agent` or `curator` (Hermes' curator or its background review) that no store-route record "
    "(`omh learning store-routes`) or approved `skill_update_candidate` accounts for as `unreviewed_native_write`, "
    "which means OMH holds no record of that write, not that nobody reviewed it."
)
# A ledger entry decides "landed" only when it is the approved write itself:
# never a curator archive or consolidation, an unrelated patch, or a rollback.
LEARNING_LANDED = (
    "Mark an approved `skill_update_candidate` landed only from a `skill_manage` result for that skill that succeeded "
    "without `staged: true`, or from a ledger entry that is dated after the approval, has `actor` `agent` or `user` and "
    "`action` `create`, `edit`, `patch`, or `write_file`, and lists the approved target file in its `after` manifest."
)
LEARNING_LANDED_NEVER = (
    "An entry by `curator`, or one that deletes, archives, rolls back, or purges, never marks it landed, and a staged "
    "result waits in `/skills pending` for the person and has not landed."
)
LEARNING_MEMORY_GAP = (
    "The ledger records skill writes only: a memory write by Hermes' background review is visible only when Hermes "
    "staged it under `pending/memory/` with `origin: background_review`, and is `not_observed` otherwise."
)
INSTINCT_LEDGER = (
    "an instinct that matches a ledger entry no store route accounts for is reported as `unreviewed_native_write` "
    "(OMH holds no record of that write, which is not to say nobody reviewed it), never counted as repeated evidence "
    "for the instinct."
)
DISTILL_LANDING = (
    "On Hermes, when the destination is the user's own Hermes skills, an approved candidate lands through "
    "`skill_manage`: report it landed only from a result that succeeded without `staged: true`, with the matching "
    "skill audit ledger entry as its record while the ledger is on, and report a staged write as waiting in "
    "`/skills pending` for the person's own `/skills approve <id>` under their `skills.write_approval` setting."
)
DISTILL_CURATOR_REPORT = (
    "it records which skills the curator consolidated, archived, or created in that run, and one report is one "
    "observation, never repeated evidence by itself."
)

NATIVE_LOOP_CLAUSES = {
    "memory-sync": (
        MEMORY_SYNC_PENDING,
        MEMORY_SYNC_STAGED,
        MEMORY_SYNC_DECLINED,
        MEMORY_SYNC_CAPTURE_NOT_GATED,
        MEMORY_SYNC_HANDS_OFF,
    ),
    "external-connector-readiness": (PROVIDER_SLOT, INSTALL_SCANS),
    "prompt-import-readiness": (IMPORT_PREVIEW, SLASH_PRECEDENCE),
    "skill-scout": (SCOUT_TRUST,),
    "skill-health": (HEALTH_NO_USAGE_PRUNE, HEALTH_LEDGER, HEALTH_LINT),
    "workflow-learning": (LEARNING_UNREVIEWED, LEARNING_LANDED, LEARNING_LANDED_NEVER, LEARNING_MEMORY_GAP),
    "instinct-ledger": (INSTINCT_LEDGER,),
    "rules-distill": (DISTILL_LANDING, DISTILL_CURATOR_REPORT),
}

_NEGATION = r"(?:never|not|no|nothing|without)"
_NEGATED = re.compile(rf"\b{_NEGATION}\b", re.IGNORECASE)
_CODE_SPAN = re.compile(r"`[^`]*`")
# A clause: a negation in another clause of the sentence does not govern this
# one ("OMH never approves one, and proposes turning it on").
_CLAUSE_END = re.compile(r"[;,]|:\s")
# A boundary a negation does not reach across even when commas only list
# items ("never present use, view, or load counts as a prune signal" stays one
# reach; "... low load counts, so archive it" is two).
_REACH_END = re.compile(r";|:\s|,\s+(?:so|then)\b")
# Setting Hermes' approval gate: enabling it, turning or switching the gate
# itself on (the verb with the gate as its object, so the noun in "the
# person's own switch on Hermes' writes" is not one), proposing a change to it,
# or writing a value into it.
_SETS_GATE = re.compile(
    r"\b(?:enables?|enabled|enabling)\b"
    r"|\b(?:turn|switch)(?:s|es|ed|ing)?\s+(?:it|the gate|`[^`]*write_approval`)\s+on\b"
    r"|\b(?:turn|switch)(?:s|es|ed|ing)?\s+on\s+(?:the gate|`[^`]*write_approval`)"
    r"|\bpropos(?:e|es|ed|ing)\b.*?write_approval"
    r"|write_approval`?\s*(?::|=|\bto\b)\s*`?(?:true|on|yes|1)\b",
    re.IGNORECASE,
)
# A use or load count beside a word that removes a skill.
_USAGE_COUNT = re.compile(r"\.usage\.json|\b(?:use|usage|view|load)\s+counts?\b", re.IGNORECASE)
_REMOVAL = re.compile(
    r"\b(?:prune|pruning|retire|retiring|archive|archiving|remove|removing|delete|deleting)\b", re.IGNORECASE
)
# A staged write reported as done.
_STAGED = "`staged: true`"
_LANDED = re.compile(r"\b(?:landed|saved|applied|committed)\b", re.IGNORECASE)
_STAGED_NEGATED = re.compile(rf"\b{_NEGATION}\s+(?:\w+\s+){{0,2}}`staged: true`", re.IGNORECASE)
# A person's approval step beside an OMH capture.
_CAPTURE = re.compile(r"\bcaptur(?:e|es|ed|ing)\b", re.IGNORECASE)
_PERSON_APPROVAL = re.compile(r"/memory approve|/memory pending|omh memory approve|omh memory review")


def _segments(text: str, boundary: re.Pattern[str]) -> list[str]:
    """Split on `boundary` outside code spans, so `staged: true` stays one token."""
    masked = _CODE_SPAN.sub(lambda match: "`" + "x" * (len(match.group(0)) - 2) + "`", text)
    pieces, start = [], 0
    for match in boundary.finditer(masked):
        pieces.append(text[start : match.start()])
        start = match.end()
    pieces.append(text[start:])
    return pieces


def _governed(segment: str, position: int) -> bool:
    return _NEGATED.search(segment[:position]) is not None


def _gate_settings_ungoverned(sentence: str) -> list[str]:
    """Clauses that set Hermes' write approval with no negation before the setting."""
    found = []
    for clause in _segments(sentence, _CLAUSE_END):
        match = _SETS_GATE.search(clause) if "write_approval" in clause else None
        if match is not None and not _governed(clause, match.start()):
            found.append(clause.strip())
    return found


def _removals_ungoverned(sentence: str) -> list[str]:
    """Removal words beside a use or load count that no negation within reach governs."""
    if not _USAGE_COUNT.search(sentence):
        return []
    return [
        match.group(0)
        for segment in _segments(sentence, _REACH_END)
        for match in _REMOVAL.finditer(segment)
        if not _governed(segment, match.start())
    ]


def _staged_landings_ungoverned(sentence: str) -> list[str]:
    """Clauses that call a write landed in a sentence naming `staged: true`, unless the clause negates either."""
    if _STAGED not in sentence:
        return []
    found = []
    for clause in _segments(sentence, _CLAUSE_END):
        if _STAGED_NEGATED.search(clause):
            continue
        if any(not _governed(clause, match.start()) for match in _LANDED.finditer(clause)):
            found.append(clause.strip())
    return found


def _capture_held_for_a_person(sentence: str) -> bool:
    """A capture sentence that names a person's approval step outside a negation."""
    if not _CAPTURE.search(sentence):
        return False
    return any(
        not _governed(clause, match.start())
        for clause in _segments(sentence, _CLAUSE_END)
        for match in _PERSON_APPROVAL.finditer(clause)
    )


def _bodies() -> list[tuple[str, str, str]]:
    """(projection, display name, content) for every Hermes and portable body."""
    return [
        *(("hermes", omh_skill_display_name(template.name), template.content) for template in builtin_skill_templates()),
        *(("portable", template.name, template.content) for template in agent_skill_templates()),
    ]


def _shape(**override: object):
    return _feature_surface_skill(
        "skill-health",
        "Probe description for the shape.",
        ("probe trigger",),
        "Use when probing the shape.",
        category="operations",
        phase="probe",
        next_action="probe_next_action",
        boundary="A probe card is not evidence.",
        good_prompt="probe good prompt",
        bad_prompt="probe bad prompt",
        **override,
    )


class OwnWhyTests(unittest.TestCase):
    def test_the_memory_and_learning_skills_carry_none_of_the_generic_lines(self) -> None:
        generic = _shape()
        generic_lines = set(generic.do_not_use_when)
        definitions = {definition.name: definition for definition in builtin_definitions()}
        for name in OWN_WHY_FEATURE_SURFACE_SKILLS:
            with self.subTest(skill=name):
                definition = definitions[name]
                self.assertNotIn("so Hermes users can ask for this workflow in chat", definition.why_this_exists)
                self.assertTrue(definition.why_this_exists.startswith(f"`{name}` exists because "))
                self.assertEqual(generic_lines & set(definition.do_not_use_when), set())


class NativeLoopClauseTests(unittest.TestCase):
    def test_each_body_states_its_native_loop_clause_in_every_projection_it_ships_in(self) -> None:
        hermes = {template.name: template.content for template in builtin_skill_templates()}
        portable = {template.name: template.content for template in agent_skill_templates()}
        shipped_portable = set(portable_skill_names())
        for name, clauses in NATIVE_LOOP_CLAUSES.items():
            display = omh_skill_display_name(name)
            for clause in clauses:
                with self.subTest(skill=name, clause=clause[:60]):
                    self.assertIn(clause, hermes[name])
                    if display in shipped_portable:
                        self.assertIn(clause, portable[display])

    def test_the_provider_slot_replaces_the_unusable_understatement(self) -> None:
        for projection, name, content in _bodies():
            with self.subTest(body=f"{projection}:{name}"):
                self.assertNotIn("never makes OMH memory unusable", content)


class MemoryCapsTests(unittest.TestCase):
    def test_memory_sync_states_the_caps_the_bundle_reader_falls_back_to(self) -> None:
        body = render.memory_sync_skill().content
        for label, key, default in HERMES_MEMORY_FILES:
            with self.subTest(file=label):
                self.assertIn(f"`memory.{key}`", body)
                self.assertIn(f"{default:,} characters for {label}", body)

    def test_the_caps_follow_the_bundle_reader_rather_than_a_copy(self) -> None:
        moved = (("MEMORY.md", "memory_char_limit", 3100), ("USER.md", "user_char_limit", 1900))
        with mock.patch.object(render, "HERMES_MEMORY_FILES", moved):
            body = render.memory_sync_skill().content
        self.assertIn("3,100 characters for MEMORY.md and 1,900 characters for USER.md", body)
        self.assertNotIn("2,200", body)


class OwnerDirectiveTests(unittest.TestCase):
    def test_no_body_sets_hermes_write_approval(self) -> None:
        clauses = [
            (projection, name, clause)
            for projection, name, content in _bodies()
            for sentence in sentences(content)
            if "write_approval" in sentence
            for clause in _segments(sentence, _CLAUSE_END)
            if "write_approval" in clause and _SETS_GATE.search(clause)
        ]
        self.assertTrue(clauses, "no clause names setting the gate; the check below would be vacuous")
        for projection, name, clause in clauses:
            with self.subTest(body=f"{projection}:{name}", clause=clause[:80]):
                self.assertEqual(_gate_settings_ungoverned(clause), [])

    def test_no_body_holds_a_capture_for_a_person(self) -> None:
        for projection, name, content in _bodies():
            for sentence in sentences(content):
                if _capture_held_for_a_person(sentence):
                    with self.subTest(body=f"{projection}:{name}", sentence=sentence[:80]):
                        self.fail(f"holds an OMH capture for a person's approval: {sentence}")

    def test_no_native_loop_body_names_the_operator_approve_verb(self) -> None:
        names = {omh_skill_display_name(name) for name in NATIVE_LOOP_SKILLS}
        for projection, name, content in _bodies():
            if name in names:
                with self.subTest(body=f"{projection}:{name}"):
                    self.assertNotIn("omh memory approve", content)

    def test_no_body_offers_a_use_count_as_a_reason_to_remove_a_skill(self) -> None:
        mentions = [
            (projection, name, sentence)
            for projection, name, content in _bodies()
            for sentence in sentences(content)
            if _USAGE_COUNT.search(sentence) and _REMOVAL.search(sentence)
        ]
        self.assertTrue(mentions, "no body ties a count to removal; the check below would be vacuous")
        for projection, name, sentence in mentions:
            with self.subTest(body=f"{projection}:{name}", sentence=sentence[:80]):
                self.assertEqual(_removals_ungoverned(sentence), [])

    def test_a_staged_write_is_never_reported_as_landed(self) -> None:
        mentions = [
            (projection, name, sentence)
            for projection, name, content in _bodies()
            for sentence in sentences(content)
            if _STAGED in sentence
        ]
        self.assertTrue(mentions, "no body names a staged result; the check below would be vacuous")
        for projection, name, sentence in mentions:
            with self.subTest(body=f"{projection}:{name}", sentence=sentence[:80]):
                self.assertEqual(_staged_landings_ungoverned(sentence), [])


class GuardPrecisionTests(unittest.TestCase):
    """Each guard flags the sentence it exists for and passes a neutral one."""

    def test_the_gate_check_flags_a_proposal_and_a_switch_on(self) -> None:
        for sentence in (
            "Propose `skills.write_approval: true` as a config diff the user applies.",
            "Set `memory.write_approval` to true before the review.",
            "Turn `memory.write_approval` on for this user.",
            "OMH never approves a pending write, and proposes turning `memory.write_approval` on.",
        ):
            with self.subTest(sentence=sentence):
                self.assertNotEqual(_gate_settings_ungoverned(sentence), [])

    def test_the_gate_check_reads_a_neutral_mention_as_neutral(self) -> None:
        for sentence in (
            "`memory.write_approval` is the person's own switch on Hermes' memory writes.",
            "Read the directory whatever `memory.write_approval` says now.",
            "OMH never approves, rejects, edits, or deletes one, and never proposes turning `memory.write_approval` on or off.",
        ):
            with self.subTest(sentence=sentence):
                self.assertEqual(_gate_settings_ungoverned(sentence), [])

    def test_the_count_check_needs_the_negation_to_reach_the_removal(self) -> None:
        self.assertNotEqual(_removals_ungoverned("A skill that is not loaded often has low load counts, so archive it."), [])
        self.assertNotEqual(_removals_ungoverned("Archive a skill whose `.usage.json` use count stayed at zero."), [])
        self.assertEqual(_removals_ungoverned(HEALTH_NO_USAGE_PRUNE), [])

    def test_the_staged_check_needs_the_negation_in_the_landing_clause(self) -> None:
        self.assertNotEqual(_staged_landings_ungoverned("A result carrying `staged: true` counts as landed, not as waiting."), [])
        self.assertNotEqual(_staged_landings_ungoverned("A result carrying `staged: true` is a saved write."), [])
        self.assertEqual(_staged_landings_ungoverned(MEMORY_SYNC_STAGED), [])
        self.assertEqual(_staged_landings_ungoverned(LEARNING_LANDED), [])

    def test_the_capture_check_flags_any_person_approval_step(self) -> None:
        self.assertTrue(_capture_held_for_a_person("Hold every `omh_memory` capture until the person runs `/memory approve <id>`."))
        self.assertTrue(_capture_held_for_a_person("A capture waits until the operator runs `omh memory review`."))
        self.assertFalse(_capture_held_for_a_person(MEMORY_SYNC_CAPTURE_NOT_GATED))


if __name__ == "__main__":
    unittest.main()
