"""Delegation graphs the view, panel and snapshot tests draw: the owner's mock-up and its variants."""

from __future__ import annotations


def root(model: str = "claude-opus-5-5", effort: str = "high") -> dict:
    return {"id": "root", "kind": "root", "title": "main", "model": model, "effort": effort, "state": "root",
            "status": "", "order": 0, "group": ""}


def lane(node_id: str, title: str, category: str, model: str, effort: str, state: str, *, elapsed=None,
         order: int = 0, group: str = "", waits: int = 0, status: str = "") -> dict:
    return {"id": node_id, "kind": "lane", "title": title, "category": category, "model": model, "effort": effort,
            "state": state, "status": status or state, "elapsed_seconds": elapsed, "order": order, "group": group,
            "waits_on": waits}


def graph(nodes: list[dict], edges: list[tuple[str, str, str]], *, name: str = "delegation",
          source: str = "native") -> dict:
    return {"schema_version": "delegation_graph/v1", "status": "active", "scope": "session", "source": source,
            "name": name, "hidden_nodes": 0, "nodes": nodes,
            "edges": [{"from": upper, "to": lower, "kind": kind} for upper, lower, kind in edges]}


def with_tokens(payload: dict, counts: dict[str, object]) -> dict:
    """``payload`` with each named node's ``output_tokens`` set, as the graph reader sets a native lane's."""
    for node in payload["nodes"]:
        if node["id"] in counts:
            node["output_tokens"] = counts[node["id"]]
    return payload


def owner_mock() -> dict:
    """The owner's mock-up: three lanes from main, and a verification that depends on all three."""
    return graph(
        [
            root(),
            lane("a", "Refresh HUD docs", "writing", "claude-opus-5-5", "medium", "running", elapsed=130, order=1),
            lane("b", "Polish HUD CLI", "quick", "glm-5.3-flash", "low", "done", elapsed=62, order=2),
            lane("c", "Update HUD tests", "deep", "claude-opus-5-5", "high", "running", elapsed=128, order=3),
            lane("v", "Integration verify", "inherit", "claude-opus-5-5", "", "waiting", order=4, waits=3),
        ],
        [("root", "a", "dispatch"), ("root", "b", "dispatch"), ("root", "c", "dispatch"),
         ("a", "v", "depends_on"), ("b", "v", "depends_on"), ("c", "v", "depends_on")],
        name="ulw-work hud-graph",
    )


def parallel(count: int) -> dict:
    lanes = [lane(f"l{index}", f"Lane {index} migrate", "quick", "glm-5.3-flash", "low", "running",
                  elapsed=60 + index, order=index + 1) for index in range(count)]
    return graph([root(), *lanes], [("root", item["id"], "dispatch") for item in lanes])


def nested() -> dict:
    """main -> an orchestrator -> a sub-orchestrator -> two leaves, beside a lane with a Korean title."""
    return graph(
        [
            root(),
            lane("o", "Port the auth module", "architect", "claude-opus-5-5", "xhigh", "running", elapsed=400, order=1),
            lane("k", "인증 모듈 문서 정리", "writing", "claude-opus-5-5", "medium", "running", elapsed=355, order=2),
            lane("o1", "Port token store", "inherit", "claude-opus-5-5", "xhigh", "done", elapsed=131, order=3),
            lane("o2", "Sub-orchestrate refresh", "inherit", "claude-opus-5-5", "xhigh", "running", elapsed=182,
                 order=4),
            lane("o21", "Refresh tokens", "?", "?", "", "dispatched", order=5),
            lane("o22", "Rotate keys", "?", "?", "", "dispatched", order=6),
        ],
        [("root", "o", "dispatch"), ("root", "k", "dispatch"), ("o", "o1", "dispatch"), ("o", "o2", "dispatch"),
         ("o2", "o21", "dispatch"), ("o2", "o22", "dispatch")],
    )


def export_run(states: dict[str, str] | None = None, *, staged: bool = False) -> dict:
    """A dag-ulw style run: Export API, then the button, the docs and the CLI flag on it, then a verification on all
    three. `staged` puts each layer in its own group (`stage N`, as a team record does), which the tree draws as a
    row of its own: ten rows then, seven without."""
    states = {"api": "done", "ui": "running", "docs": "waiting", "cli": "waiting", "verify": "waiting", **(states or {})}
    stage = (lambda number: f"stage {number}") if staged else (lambda number: "")
    nodes = [
        root(),
        lane("api", "Export API", "quick", "glm-5.3-flash", "low", states["api"], elapsed=12, order=1, group=stage(1)),
        lane("ui", "Export button", "quick", "glm-5.3", "low", states["ui"], elapsed=9, order=2, group=stage(2)),
        lane("docs", "Export docs", "writing", "claude-opus-5-5", "medium", states["docs"], order=3, group=stage(2)),
        lane("cli", "Export CLI flag", "quick", "glm-5.3-flash", "low", states["cli"], order=4, group=stage(2)),
        lane("verify", "Verify export", "deep", "claude-opus-5-5", "high", states["verify"], order=5, group=stage(3),
             waits=3),
    ]
    edges = [("root", "api", "dispatch"), ("api", "ui", "depends_on"), ("api", "docs", "depends_on"),
             ("api", "cli", "depends_on"), ("ui", "verify", "depends_on"), ("docs", "verify", "depends_on"),
             ("cli", "verify", "depends_on")]
    return graph(nodes, edges, name="export feature")


def dag_export_run() -> dict:
    """A dag run record's graph (`d:<run>:<node>` ids, status words), five nodes over three frontiers: an API,
    three nodes on it, a verification of all three. The boxes' fixture; `export_run` is the tree's.

    One node in each state a box colours differently: accepted, running, handed out, failed and waiting.
    """
    def node(key: str, title: str, category: str, model: str, effort: str, state: str, status: str, order: int,
             **extra) -> dict:
        return {**lane(f"d:r1:{key}", title, category, model, effort, state, order=order, status=status,
                       group=f"layer {extra.pop('layer')}"), **extra}

    return graph(
        [
            root(),
            node("api", "Export API", "quick", "glm-5.3-flash", "low", "done", "accepted", 1, layer=1,
                 elapsed_seconds=12, session_tail="3f0a9c1d"),
            node("button", "Export button", "unspecified-high", "glm-5.3", "low", "running", "running", 2, layer=2,
                 elapsed_seconds=21, session_tail="7c41e9a2"),
            node("docs", "Export docs", "writing", "?", "", "dispatched", "handed out", 3, layer=2),
            node("cli", "Export CLI flag", "quick", "glm-5.3-flash", "low", "failed", "failed", 4, layer=2,
                 elapsed_seconds=8, session_tail="b84d2e07"),
            node("verify", "Verify export", "unspecified-high", "?", "", "waiting", "waiting", 5, layer=3, waits_on=3),
        ],
        [("root", "d:r1:api", "dispatch"),
         *(("d:r1:api", f"d:r1:{key}", "depends_on") for key in ("button", "docs", "cli")),
         *((f"d:r1:{key}", "d:r1:verify", "depends_on") for key in ("button", "docs", "cli"))],
        name="export feature",
    )


def dag_export_button_repair_run() -> dict:
    """The export run after the button failed: the repair added for it, on the API, and the verification that still
    depends on the button, which the repair runs before."""
    run = dag_export_run()
    for node in run["nodes"][1:]:
        node.update(state="done", status="accepted", elapsed_seconds=node.get("elapsed_seconds") or 3, waits_on=0)
    by_id = {node["id"]: node for node in run["nodes"]}
    by_id["d:r1:button"].update(state="failed", status="blocked")
    by_id["d:r1:verify"].update(state="waiting", status="held")
    run["nodes"].append({
        **lane("d:r1:button-fix", "Export button, with the key", "unspecified-high", "glm-5.3", "low", "running",
               status="running", order=6, group="layer 2"), "elapsed_seconds": 14, "session_tail": "e5a1c093",
        "cycle": 1,  # a repair node's repair cycle, as `delegation_graph._dag_nodes` copies it from the run record
    })
    run["edges"] += [{"from": "d:r1:api", "to": "d:r1:button-fix", "kind": "depends_on"},
                     {"from": "d:r1:button", "to": "d:r1:button-fix", "kind": "repair"}]
    return run


def dag_export_repair_run() -> dict:
    """The export run later: all five accepted, an extend node that failed, and the repair added for it."""
    run = dag_export_run()
    for node in run["nodes"][1:]:
        node.update(state="done", status="accepted", elapsed_seconds=node.get("elapsed_seconds") or 9, waits_on=0)
    run["nodes"] += [
        {**lane("d:r1:csv", "CSV export", "quick", "glm-5.3-flash", "low", "failed", status="failed", order=6,
                group="extend 1"), "elapsed_seconds": 6, "session_tail": "51be07c4"},
        {**lane("d:r1:csv-fix", "CSV export, with the header row", "quick", "glm-5.3-flash", "low", "running",
                status="running", order=7, group="repair 1"), "elapsed_seconds": 18, "session_tail": "9d2e6f30",
         "cycle": 1},
    ]
    run["edges"] += [{"from": "d:r1:verify", "to": "d:r1:csv", "kind": "extend"},
                     {"from": "d:r1:csv", "to": "d:r1:csv-fix", "kind": "repair"}]
    return run
