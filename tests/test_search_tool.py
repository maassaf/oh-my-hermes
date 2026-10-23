"""Registered search integration, synthetic inputs only; never retrieval."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from _local_package import load_local_package
from _module_patch import patch_modules

load_local_package()

from omh.plugin_bundle.omh import register  # noqa: E402
from omh.workflows.search_amplification import build_search_plan  # noqa: E402
from test_search_amplification import envelope, evidence, fixture, observation  # noqa: E402


class SearchContext:
    def __init__(self):
        self.tools = {}
        self.hooks = {}

    def register_tool(self, name, toolset, schema, handler, **kwargs):
        self.tools[name] = (schema, handler)

    def register_hook(self, name, handler):
        self.hooks[name] = handler


def registered(test):
    ctx = SearchContext()
    register(ctx)
    test.assertIn("omh_search", ctx.tools, "native search helper is not registered")
    return ctx.tools["omh_search"][1]


class SearchToolTests(unittest.TestCase):
    def test_merge_reused_capability_binding_and_tool_cannot_change(self):
        handler = registered(self)
        for field, value in (("tool_name", "host.other"), ("principal_id", "other"),
                             ("provider_id", "other"), ("scope_id", "other"),
                             ("source_kind", "connected_app"), ("grant_id", "other")):
            with self.subTest(field=field):
                spec, caps = fixture()
                other = dict(spec["scope"]["grants"][0], grant_id="other")
                if field != "tool_name":
                    other[field] = value
                # Distinct valid grant for every binding mutation. Grant-only
                # changes also change principal to avoid duplicate scope bindings.
                if field == "grant_id":
                    other["principal_id"] = "other"
                if field != "tool_name":
                    spec["scope"]["grants"].append(other)
                host = {"session_id": self.id() + field, "user_task": "Find document"}
                planned = json.loads(handler({"action": "plan", "payload": {
                    "spec": spec, "capabilities": caps}}, **host))
                self.assertFalse(planned["degraded"], planned)
                current = deepcopy(caps)
                cap = current["capabilities"][0]
                cap[field] = value
                if field != "tool_name":
                    cap.update({key: other[key] for key in
                                ("grant_id", "source_kind", "provider_id", "principal_id", "scope_id")})
                args = {"action": "merge", "payload": {"candidate_sets": [], "scope": {
                    "schema_version": "search_merge_context/v1", "scope": spec["scope"],
                    "capabilities": current}}}
                before = deepcopy(args)
                result = json.loads(handler(args, **host))
                self.assertTrue(result["degraded"], result)
                self.assertEqual(result["reason"], "scope_mismatch:/payload/scope/capabilities/capabilities/0")
                self.assertEqual(args, before)
                # Refusal is not a reset or a poisoned binding.
                args["payload"]["scope"]["capabilities"] = caps
                self.assertFalse(json.loads(handler(args, **host))["degraded"])

    def test_merge_intersects_planned_current_operations_and_ignores_new_authority(self):
        handler = registered(self)
        cases = [
            ("added_read", ["search"], ["search", "read"], "available", "full_text", "c", 0),
            ("snippet", ["search"], ["search", "read"], "available", "snippet", "c", 1),
            ("removed_read", ["search", "read"], ["search"], "available", "full_text", "c", 0),
            ("removed_search", ["search", "read"], ["read"], "available", "snippet", "c", 0),
            ("new_id", ["search", "read"], ["search", "read"], "available", "full_text", "new", 0),
            ("unavailable", ["search", "read"], ["search", "read"], "unavailable", "full_text", "c", 0),
            ("denied", ["search", "read"], ["search", "read"], "denied", "full_text", "c", 0),
            ("unknown", ["search", "read"], ["search", "read"], "unknown", "full_text", "c", 0),
        ]
        for label, old_ops, new_ops, availability, read_state, cid, count in cases:
            with self.subTest(label=label):
                spec, caps = fixture()
                caps["capabilities"][0].update(operations=old_ops, availability=availability)
                host = {"session_id": self.id() + label, "user_task": "Find document"}
                planned = json.loads(handler({"action": "plan", "payload": {
                    "spec": spec, "capabilities": caps}}, **host))
                self.assertFalse(planned["degraded"], planned)
                current = deepcopy(caps)
                current["capabilities"][0].update(operations=new_ops, availability="available", capability_id=cid)
                sets = [dict(schema_version="search_candidate_set/v1", set_id="set", query_id="q0",
                             capability_id=cid, candidates=[evidence(read_state=read_state)["candidate"]])]
                args = {"action": "merge", "payload": {"candidate_sets": sets, "scope": {
                    "schema_version": "search_merge_context/v1", "scope": spec["scope"],
                    "capabilities": current}}}
                before = deepcopy(args)
                result = json.loads(handler(args, **host))
                self.assertFalse(result["degraded"], result)
                self.assertEqual(result["result"]["candidate_count"], count)
                if not count:
                    self.assertEqual(result["result"]["excluded"][0]["candidate_id"], "candidate0")
                    self.assertNotIn("Synthetic only", json.dumps(result))
                self.assertEqual(args, before)

    def test_merge_current_revocation_preserves_other_planned_source(self):
        handler = registered(self)
        for status in ("missing", "denied", "unavailable", "unknown", "available"):
            with self.subTest(status=status):
                spec, caps = fixture()
                caps["capabilities"].append(dict(caps["capabilities"][0], capability_id="other", tool_name="host.other"))
                spec["queries"][0]["capability_ids"].append("other")
                host = {"session_id": self.id() + status, "user_task": "Find document"}
                self.assertFalse(json.loads(handler({"action": "plan", "payload": {
                    "spec": spec, "capabilities": caps}}, **host))["degraded"])
                current = deepcopy(caps)
                if status == "missing":
                    current["capabilities"].pop(0)
                else:
                    current["capabilities"][0]["availability"] = status
                sets = [dict(schema_version="search_candidate_set/v1", set_id="set" + cid, query_id="q0",
                             capability_id=cid, candidates=[dict(evidence()["candidate"], candidate_id=cid,
                                                                 source_id=cid, title="PRIVATE" if cid == "c" else "Retained")])
                        for cid in ("c", "other")]
                result = json.loads(handler({"action": "merge", "payload": {"candidate_sets": sets, "scope": {
                    "schema_version": "search_merge_context/v1", "scope": spec["scope"],
                    "capabilities": current}}}, **host))
                self.assertFalse(result["degraded"], result)
                self.assertEqual(result["result"]["candidate_count"], 2 if status == "available" else 1)
                self.assertIn("Retained", json.dumps(result))
                if status != "available":
                    self.assertNotIn("PRIVATE", json.dumps(result))

    def test_merge_filtered_capabilities_still_validate_candidate_binding(self):
        handler = registered(self)
        spec, caps = fixture()
        spec["scope"]["grants"].append(dict(spec["scope"]["grants"][0], grant_id="other", principal_id="other"))
        host = {"session_id": self.id(), "user_task": "Find document"}
        self.assertFalse(json.loads(handler({"action": "plan", "payload": {
            "spec": spec, "capabilities": caps}}, **host))["degraded"])
        current = deepcopy(caps)
        current["capabilities"][0]["capability_id"] = "new"
        candidate = dict(evidence()["candidate"], principal_id="other")
        result = json.loads(handler({"action": "merge", "payload": {"candidate_sets": [
            dict(schema_version="search_candidate_set/v1", set_id="set", query_id="q0",
                 capability_id="new", candidates=[candidate])], "scope": {
                     "schema_version": "search_merge_context/v1", "scope": spec["scope"],
                     "capabilities": current}}}, **host))
        self.assertTrue(result["degraded"], result)
        self.assertEqual(result["reason"], "scope_mismatch:/candidate_sets/0/candidates/0")

    def test_merge_snapshot_cannot_precede_bound_plan_clock(self):
        handler = registered(self)
        spec, caps = fixture()
        caps["checked_at"] = "2026-10-23T03:00:00.000001Z"
        host = {"session_id": self.id(), "user_task": "Find document"}
        self.assertFalse(json.loads(handler({"action": "plan", "payload": {
            "spec": spec, "capabilities": caps}}, **host))["degraded"])
        for checked, refused in (("2026-10-22T03:00:00Z", True),
                                 ("2026-10-23T03:00:00Z", True),
                                 ("2026-10-23T12:00:00.000001+09:00", False),
                                 ("2026-10-23T03:00:00.000002", False),
                                 ("2026-10-24", False), ("not-a-date", True)):
            with self.subTest(checked=checked):
                current = deepcopy(caps)
                current["checked_at"] = checked
                current["capabilities"][0]["observed_at"] = "2026-10-22T03:00:00Z"
                result = json.loads(handler({"action": "merge", "payload": {"candidate_sets": [], "scope": {
                    "schema_version": "search_merge_context/v1", "scope": spec["scope"],
                    "capabilities": current}}}, **host))
                self.assertEqual(result["degraded"], refused, result)
                if refused:
                    self.assertTrue(result["reason"].startswith("invalid_time:"), result)

    def test_response_size_refusal_is_atomic_for_first_later_assessments_and_plan(self):
        from omh.plugin_bundle.omh import search_state
        from omh.plugin_bundle.omh.tools import search_tool

        handler = registered(self)
        # A real bounded input expands because every requirement repeats long
        # observation IDs. No producer mock, cap relaxation or hidden-state reset.
        spec, caps = fixture()
        reqids = ["r" + str(i) for i in range(100)]
        spec["requirements"] = [dict(spec["requirements"][0], requirement_id=rid) for rid in reqids]
        spec["queries"][0]["requirement_ids"] = reqids
        spec["queries"].append(dict(spec["queries"][0], query_id="q1", round=1))
        ledger = [dict(observation(status="denied", number=i), attempted=False,
                       observation_id=str(i) + ":" + "x" * 2040, requirement_ids=reqids) for i in range(100)]
        for delivered in (False, True):
            with self.subTest(delivered=delivered):
                host = {"session_id": self.id() + str(delivered), "user_task": "Find document"}
                plan_args = {"action": "plan", "payload": {"spec": spec, "capabilities": caps}}
                plan = json.loads(handler(plan_args, **host))["result"]
                previous = None
                if delivered:
                    previous = json.loads(handler({"action": "assess", "payload": {
                        "plan": plan, "observations": envelope(caps)}}, **host))["result"]["state"]
                    self.assertEqual(previous["used_follow_up_rounds"], 1)
                args = {"action": "assess", "payload": {
                    "plan": plan, "observations": envelope(caps, ledger, previous)}}
                before_args = deepcopy(args)
                session, request = search_state._host_key(host)
                before = deepcopy(search_state._sessions[session][request])
                self.assertLess(len(json.dumps(args).encode()), search_tool.MAX_INPUT_BYTES)
                failures = [json.loads(handler(args, **host)) for _ in range(2)]
                for result in failures:
                    self.assertEqual(result.get("reason"), "bounds:/result", result)
                    self.assertNotIn("result", result)
                self.assertEqual(search_state._sessions[session][request], before)
                self.assertEqual(args, before_args)
                small = dict(observation(status="denied"), attempted=False, requirement_ids=reqids)
                recovered = json.loads(handler({"action": "assess", "payload": {
                    "plan": plan, "observations": envelope(caps, [small], previous)}}, **host))
                self.assertFalse(recovered["degraded"], recovered)
                state = recovered["result"]["state"]
                self.assertEqual(state["revision"], 2 if delivered else 1)
                self.assertEqual(state["used_follow_up_rounds"], 1)
                self.assertEqual(recovered["result"]["follow_up_query_ids"], [] if delivered else ["q1"])
                self.assertTrue(json.loads(handler(plan_args, **host))["degraded"])
        # A plan that cannot be returned claims neither a host slot nor a request ID.
        host = {"session_id": self.id() + "plan", "user_task": "Find document"}
        plan_args = {"action": "plan", "payload": {"spec": spec, "capabilities": caps}}
        with patch.object(search_tool, "MAX_OUTPUT_BYTES", 0):
            failed = [json.loads(handler(plan_args, **host)) for _ in range(2)]
        self.assertEqual([row.get("reason") for row in failed], ["bounds:/result"] * 2)
        self.assertNotIn(search_state._host_key(host)[0], search_state._sessions)
        self.assertFalse(json.loads(handler(plan_args, **host))["degraded"])

    def test_serialization_failure_keeps_last_delivered_state_and_clean_retry(self):
        from omh.plugin_bundle.omh import search_state
        from omh.plugin_bundle.omh.tools import search_tool

        handler = registered(self)
        dumps = json.dumps
        for phase in ("result", "wire"):
            for stage in ("plan", "first", "later"):
                with self.subTest(phase=phase, stage=stage):
                    spec, caps = fixture()
                    host = {"session_id": self.id() + phase + stage, "user_task": "Find document"}
                    args = {"action": "plan", "payload": {"spec": spec, "capabilities": caps}}
                    if stage != "plan":
                        plan = json.loads(handler(args, **host))["result"]
                        previous = None
                        if stage == "later":
                            previous = json.loads(handler({"action": "assess", "payload": {
                                "plan": plan, "observations": envelope(caps)}}, **host))["result"]["state"]
                        args = {"action": "assess", "payload": {
                            "plan": plan, "observations": envelope(caps, previous=previous)}}
                    before = deepcopy(search_state._sessions)

                    def fail_response(value, *pos, **kw):
                        schema = "search_plan/v1" if stage == "plan" else "search_coverage/v1"
                        if isinstance(value, dict) and ((phase == "result" and value.get("schema_version") == schema)
                                                       or (phase == "wire" and "result" in value
                                                           and value.get("plugin_tool") == "omh_search")):
                            raise TypeError("PRIVATE serialization failure")
                        return dumps(value, *pos, **kw)

                    with patch.object(search_tool.json, "dumps", side_effect=fail_response):
                        try:
                            failures = [json.loads(handler(args, **host)) for _ in range(2)]
                        except TypeError:
                            self.fail("Response serialization escaped the classified handler boundary")
                    for result in failures:
                        self.assertTrue(result["degraded"], result)
                        self.assertNotIn("result", result)
                        self.assertNotIn("PRIVATE", dumps(result))
                    self.assertEqual(search_state._sessions, before)
                    recovered = json.loads(handler(args, **host))
                    self.assertFalse(recovered["degraded"], recovered)
                    if stage != "plan":
                        self.assertEqual(recovered["result"]["state"]["revision"], 2 if stage == "later" else 1)

    def test_backend_fault_is_clean_and_aggregate_input_is_bounded_before_encoding(self):
        handler = registered(self)
        spec, caps = fixture()
        with patch("omh.workflows.search_amplification.build_search_plan", side_effect=RuntimeError("PRIVATE body")):
            result = json.loads(handler({"action": "plan", "payload": {"spec": spec, "capabilities": caps}},
                                        session_id=self.id(), user_task="request"))
        self.assertEqual(result["error"], "package_backend_error")
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_aggregate_input_is_bounded_before_encoding(self):
        from omh.plugin_bundle.omh.tools import search_tool

        with patch.object(search_tool.json, "dumps", side_effect=AssertionError("oversized tree serialized")):
            with self.assertRaisesRegex(ValueError, "bounds:/payload"):
                search_tool._bounded_json(["a" * 32768] * 130)

    def test_no_network_process_provider_or_raw_search_body_persistence(self):
        from omh.plugin_bundle.omh import search_state
        from omh.workflows import search_evidence  # noqa: F401

        handler = registered(self)
        spec, caps = fixture()
        spec["query"] = "PRIVATE raw query /signed?opaque=01"
        host = {"session_id": self.id(), "task_id": "host-terminal-resource", "user_task": "PRIVATE host request"}
        with patch("socket.socket", side_effect=AssertionError("socket")), \
             patch("subprocess.Popen", side_effect=AssertionError("process")), \
             patch("builtins.open", side_effect=AssertionError("body persistence")):
            plan = json.loads(handler({"action": "plan", "payload": {"spec": spec, "capabilities": caps}}, **host))
            merged = json.loads(handler({"action": "merge", "payload": {"candidate_sets": [], "scope": {
                "schema_version": "search_merge_context/v1", "scope": spec["scope"], "capabilities": caps}}}, **host))
            assessed = json.loads(handler({"action": "assess", "payload": {
                "plan": plan["result"], "observations": envelope(caps)}}, **host))
        for result in (plan, merged, assessed):
            self.assertFalse(result["degraded"], result)
        self.assertNotIn("PRIVATE", json.dumps(search_state._sessions))
        for records in search_state._sessions.values():
            for record in records.values():
                self.assertEqual(set(record), {"request_id", "plan", "scope", "state", "capabilities", "clock_floor"})
                for key in ("request_id", "plan", "scope", "state"):
                    if record[key] is not None:
                        self.assertRegex(record[key], r"^[0-9a-f]{64}$")
                self.assertIs(type(record["clock_floor"]), int)
                self.assertGreater(record["clock_floor"], 0)
                self.assertLess(record["clock_floor"], 10**18)
                self.assertLessEqual(len(record["capabilities"]), 64)
                for cid, (binding, flags) in record["capabilities"].items():
                    self.assertRegex(cid, r"^[0-9a-f]{64}$")
                    self.assertRegex(binding, r"^[0-9a-f]{64}$")
                    self.assertIs(type(flags), int)
                    self.assertIn(flags, (0, 1, 2, 3))

    def test_standalone_backend_absence_never_returns_a_pretend_plan(self):
        handler = registered(self)
        spec, caps = fixture()
        with patch_modules({"omh.workflows.search_amplification": None}):
            result = json.loads(handler({"action": "plan", "payload": {"spec": spec, "capabilities": caps}},
                                        session_id=self.id(), user_task="request"))
        self.assertEqual(result["error"], "backend_unavailable")
        self.assertNotIn("result", result)

    def test_concurrent_same_previous_state_advances_once_and_failed_calls_do_not_advance(self):
        from concurrent.futures import ThreadPoolExecutor

        handler = registered(self)
        spec, caps = fixture()
        host = {"session_id": self.id(), "user_task": "Find document"}
        plan = json.loads(handler({"action": "plan", "payload": {"spec": spec, "capabilities": caps}}, **host))["result"]
        args = {"action": "assess", "payload": {"plan": plan, "observations": envelope(caps)}}
        invalid = deepcopy(args)
        invalid["payload"]["observations"]["unknown"] = "PRIVATE"
        self.assertTrue(json.loads(handler(invalid, **host))["degraded"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: json.loads(handler(args, **host)), range(2)))
        self.assertEqual(sum(not row["degraded"] for row in results), 1)

    def test_forged_state_ledger_replacement_scope_widening_and_capacity_fail_closed(self):
        from omh.plugin_bundle.omh import search_state

        handler = registered(self)
        spec, caps = fixture()
        host = {"session_id": self.id(), "user_task": "Find document"}
        args = {"action": "plan", "payload": {"spec": spec, "capabilities": caps}}
        plan = json.loads(handler(args, **host))["result"]
        ledger = [observation()]
        data = {"plan": plan, "observations": envelope(caps, ledger)}
        first = json.loads(handler({"action": "assess", "payload": data}, **host))["result"]
        data["observations"] = envelope(caps, [], first["state"])
        self.assertTrue(json.loads(handler({"action": "assess", "payload": data}, **host))["degraded"])
        data["observations"] = envelope(caps, ledger, dict(first["state"], revision=0))
        self.assertTrue(json.loads(handler({"action": "assess", "payload": data}, **host))["degraded"])
        data["observations"] = envelope(caps, ledger, first["state"])
        self.assertFalse(json.loads(handler({"action": "assess", "payload": data}, **host))["degraded"])
        scope = deepcopy(spec["scope"])
        scope["grants"][0]["scope_id"] = "wider"
        result = json.loads(handler({"action": "merge", "payload": {"candidate_sets": [], "scope": {
            "schema_version": "search_merge_context/v1", "scope": scope, "capabilities": caps}}}, **host))
        self.assertEqual(result["reason"], "scope_mismatch:/payload/scope")
        with patch.object(search_state, "MAX_REQUESTS_PER_SESSION", 0):
            result = json.loads(handler(args, session_id="capacity-session", user_task="Find"))
        self.assertEqual(result["error"], "host_state_capacity")

    def test_host_context_is_required_and_caller_identity_is_not_authority(self):
        handler = registered(self)
        spec, caps = fixture()
        args = {"action": "plan", "payload": {"spec": spec, "capabilities": caps}}
        for host in ({}, {"session_id": self.id()}, {"user_task": "find"},
                     {"session_id": [], "user_task": "find"},
                     {"session_id": self.id(), "user_task": ""}):
            with self.subTest(host=host):
                result = json.loads(handler(args, **host))
                self.assertEqual(result.get("error"), "host_context_unavailable", result)
                self.assertNotIn("result", result)
        spoofed = dict(args, observation={"host": "hermes", "session_id": self.id()})
        self.assertTrue(json.loads(handler(spoofed))["degraded"])

    def test_last_state_replay_omission_plan_reset_and_cross_session_are_refused(self):
        handler = registered(self)
        spec, caps = fixture()
        spec["queries"].append(dict(spec["queries"][0], query_id="q1", round=1))
        host = {"session_id": self.id(), "user_task": "Find the document"}
        args = {"action": "plan", "payload": {"spec": spec, "capabilities": caps}}
        plan = json.loads(handler(args, **host))["result"]
        data = {"plan": plan, "observations": envelope(caps)}
        first = json.loads(handler({"action": "assess", "payload": data}, **host))["result"]
        self.assertEqual(first["follow_up"], "search_once")
        for bad_host in (host, dict(host, session_id="another-session"), dict(host, user_task="Changed date")):
            refused = json.loads(handler({"action": "assess", "payload": data}, **bad_host))
            self.assertTrue(refused["degraded"], refused)
            self.assertNotIn("result", refused)
        self.assertTrue(json.loads(handler(args, **host))["degraded"])
        replacement = deepcopy(args)
        replacement["payload"]["spec"]["request_id"] = "reset-request"
        self.assertTrue(json.loads(handler(replacement, **host))["degraded"])
        data["observations"]["previous_state"] = first["state"]
        second = json.loads(handler({"action": "assess", "payload": data}, **host))["result"]
        self.assertEqual(second["state"]["revision"], 2)
        self.assertEqual(second["follow_up_query_ids"], [])
        self.assertTrue(json.loads(handler({"action": "assess", "payload": data}, **host))["degraded"])
        other = dict(host, session_id="independent-session")
        other_plan = json.loads(handler(args, **other))["result"]
        self.assertEqual(other_plan, plan)
        self.assertFalse(json.loads(handler({"action": "assess", "payload": {
            "plan": other_plan, "observations": envelope(caps)}}, **other))["degraded"])

    def test_strict_closed_arguments_and_clean_bounded_errors(self):
        handler = registered(self)
        spec, caps = fixture()
        valid = {"action": "plan", "payload": {"spec": spec, "capabilities": caps}}
        cases = [None, [], {}, dict(valid, action=[]), dict(valid, action="search"),
                 dict(valid, session_id="victim"), dict(valid, payload=[]),
                 dict(valid, payload={"spec": spec}), dict(valid, payload=dict(valid["payload"], secret="PRIVATE"))]
        for value in (True, float("nan"), (1, 2), {1: "PRIVATE"}, "PRIVATE" * 10000):
            bad = deepcopy(valid)
            bad["payload"]["spec"]["query"] = value
            cases.append(bad)
        cyclic = deepcopy(valid)
        cyclic["payload"]["spec"]["query"] = cyclic
        cases.append(cyclic)
        for index, args in enumerate(cases):
            with self.subTest(index=index):
                result = json.loads(handler(args, session_id=self.id(), user_task="bounded"))
                self.assertTrue(result["degraded"], result)
                self.assertNotIn("result", result)
                self.assertNotIn("PRIVATE", json.dumps(result))
                self.assertLess(len(json.dumps(result)), 1500)

    def test_merge_and_assess_dispatch_real_apis_without_promoting_document_instructions(self):
        from omh.workflows.search_amplification import assess_search_coverage
        from omh.workflows.search_evidence import merge_search_candidates

        handler = registered(self)
        host = {"session_id": self.id(), "user_task": "Find the exact document"}
        spec, caps = fixture()
        plan = json.loads(handler({"action": "plan", "payload": {"spec": spec, "capabilities": caps}},
                                  **host))["result"]
        scope = dict(schema_version="search_merge_context/v1", scope=spec["scope"], capabilities=caps)
        sets = [dict(schema_version="search_candidate_set/v1", set_id="set", query_id="q0",
                     capability_id="c", candidates=[evidence()["candidate"]])]
        merged = json.loads(handler({"action": "merge", "payload": {"candidate_sets": sets, "scope": scope}}, **host))
        self.assertEqual(merged.get("result"), merge_search_candidates(sets, scope), merged)
        obs = envelope(caps, [observation(evidence=[evidence()])])
        assessed = json.loads(handler({"action": "assess", "payload": {"plan": plan, "observations": obs}}, **host))
        self.assertEqual(assessed.get("result"), assess_search_coverage(plan, obs), assessed)
        self.assertIn("no retrieval", assessed["claim_boundary"])

    def test_registered_plan_calls_real_producer_and_retains_opaque_tokens(self):
        handler = registered(self)
        spec, caps = fixture()
        original = deepcopy(spec)
        result = json.loads(handler({"action": "plan", "payload": {"spec": spec, "capabilities": caps}},
                                    session_id=self.id(), user_task="Find the exact document"))
        self.assertFalse(result["degraded"], result)
        self.assertEqual(result["result"], build_search_plan(spec, caps))
        self.assertEqual(spec, original)
        self.assertEqual(result["plugin_tool"], "omh_search")
        self.assertFalse(result["retrieval_performed"])


if __name__ == "__main__":
    unittest.main()
