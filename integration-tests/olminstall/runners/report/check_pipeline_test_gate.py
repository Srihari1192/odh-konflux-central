#!/usr/bin/env python3
"""Fail test-finalize when requested gates have FAILURE or missing TEST_OUTPUT."""

from __future__ import annotations

import json
import os
import sys

from _bootstrap import ensure_olminstall_path

ensure_olminstall_path()

from runners.report.pipeline_test_outputs import collect_bvt_smoke_outputs
from runners.report.pipelinerun_summary import list_pipeline_test_outputs, namespace_from_env, pipeline_run_name_from_env
from steps.check_test_output_gate import check_test_output_json
from steps.tekton_incluster import list_taskruns_in_cluster


def _gates_from_env() -> set[str]:
    raw = os.environ.get("TEST_GATES", "").strip()
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def _workspace_gate_output_paths() -> tuple[str, str]:
    return (
        os.environ.get("SMOKE_TEST_OUTPUT_PATH", "").strip(),
        os.environ.get("BVT_TEST_OUTPUT_PATH", "").strip(),
    )


def _collect_gate_outputs(taskruns: list[dict[str, object]]) -> dict[str, str]:
    smoke_path, bvt_path = _workspace_gate_output_paths()
    return collect_bvt_smoke_outputs(
        taskruns,
        list_from_taskruns=list_pipeline_test_outputs,
        smoke_path=smoke_path,
        bvt_path=bvt_path,
    )


def main() -> int:
    gates = _gates_from_env()
    if not gates.intersection({"bvt", "smoke"}):
        print("No bvt/smoke gates in TEST_GATES; skipping pipeline test gate")
        return 0

    pr_name = pipeline_run_name_from_env(required=True)
    ns = namespace_from_env(required=True)
    list_errors: list[str] = []
    taskruns = list_taskruns_in_cluster(pr_name, ns, error_out=list_errors)
    if not taskruns and list_errors:
        print(f"WARN: {list_errors[0]}", file=sys.stderr)
    by_gate = _collect_gate_outputs(taskruns)
    failures: list[str] = []
    for gate in ("bvt", "smoke"):
        if gate not in gates:
            continue
        raw = by_gate.get(gate, "")
        if not raw:
            failures.append(f"{gate}: TEST_OUTPUT missing")
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            failures.append(f"{gate}: TEST_OUTPUT unreadable ({exc})")
            continue
        if not isinstance(data, dict):
            failures.append(f"{gate}: TEST_OUTPUT is not a JSON object")
            continue
        ec, msg = check_test_output_json(
            data,
            gate_label=gate.upper(),
            allow_all_skipped_note_prefix="bvt:" if gate == "bvt" else "",
            strict=(gate == "bvt"),
        )
        if ec != 0:
            failures.append(msg)

    if failures:
        for line in failures:
            print(f"ERROR: {line}", file=sys.stderr)
        return 1

    print("Pipeline test gates passed (bvt/smoke TEST_OUTPUT acceptable)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
