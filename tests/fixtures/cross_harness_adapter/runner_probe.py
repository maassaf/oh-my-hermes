#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly:
#      uv run runner_probe.py --scenario passing --output receipt.json
# 3. Or make executable and run:
#      chmod +x runner_probe.py && ./runner_probe.py --scenario passing --output receipt.json
# ─────────────────

from __future__ import annotations

import argparse
from contextlib import ExitStack
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).parents[3]
sys.path.insert(0, str(ROOT / "src"))

from omh.quality.cross_harness_adapter_evidence import CommandEvidence, SourceEvidence
from omh.quality.cross_harness_adapter_model import canonical_digest, parse_adapter_request
from omh.quality.cross_harness_adapters import ExecutionSpec, OutputContract, run_adapter
from omh.quality.cross_harness_benchmark_input import decode_benchmark_bytes
from omh.quality.cross_harness_benchmark_values import json_map


FIXTURES = Path(__file__).parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backend", default="auto")
    arguments = parser.parse_args()
    request_name = {
        "network-denied": "network-denied-request.json",
        "outside-write-denied": "outside-write-denied-request.json",
    }.get(arguments.scenario, "passing-request.json")
    raw = dict(json_map(decode_benchmark_bytes((FIXTURES / request_name).read_bytes())))
    executable = str(Path(sys.executable).resolve())
    argv = (executable, str((FIXTURES / "fake_adapter.py").resolve()), arguments.scenario)
    raw["executable"] = Path(executable).name
    raw["executable_version"] = "fake-adapter 1.0"
    raw["argv_digest"] = canonical_digest(list(argv))
    if arguments.scenario == "flaky":
        raw["repetition"] = 2
    with ExitStack() as resources:
        environment: tuple[tuple[str, str], ...] = ()
        if arguments.scenario == "network-denied":
            listener = resources.enter_context(socket.create_server(("127.0.0.1", 0)))
            environment = (("OMH_NETWORK_PORT", str(listener.getsockname()[1])),)
        temporary = Path(resources.enter_context(TemporaryDirectory(prefix="omh-runner-probe-")))
        source_raw = {"source_id": "fixture-source", "commit": "a" * 40, "license": "MIT", "path_metadata": "tests/fixtures/cross_harness_adapter"}
        command_raw = {"command_id": "runner-probe", "harness": "fake", "argv": ["fake-adapter", arguments.scenario], "cwd_class": "disposable", "source_id": "fixture-source", "source_commit": "a" * 40, "expected_exit": 0, "expected_semantic_result": "pass"}
        source = SourceEvidence("fixture-source", "a" * 40, "MIT", "tests/fixtures/cross_harness_adapter", canonical_digest(source_raw))
        command = CommandEvidence("runner-probe", "fake", ("fake-adapter", arguments.scenario), "disposable", "fixture-source", "a" * 40, 0, "pass", canonical_digest(command_raw), 0, "pass")
        receipt_path = arguments.output.with_name(f"{arguments.output.stem}.receipt.json")
        output = OutputContract(receipt_path, arguments.output, "runner-probe", source, command)
        spec = ExecutionSpec(argv, (FIXTURES,), temporary, arguments.backend, False, environment, (executable, argv[1], "--version"))
        receipt = run_adapter(parse_adapter_request(raw), spec, output)
        clean = not any(temporary.iterdir())
    if receipt.cleanup_verified != clean:
        return 3
    if receipt.status == "unavailable":
        print(receipt_path.read_text(encoding="utf-8").strip())
        return 2
    return 0 if receipt.status == "observed_success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
