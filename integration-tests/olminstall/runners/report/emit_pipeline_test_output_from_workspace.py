#!/usr/bin/env python3
"""Write pipeline-level TEST_OUTPUT from workspace sidecars (always succeeds).

Used by the ``publish-results`` ``emit-workspace-test-output`` step so pipeline
TEST_OUTPUT is available from gate sidecars before the in-cluster propagate step.

Reads ``.olminstall-smoke-test-output.json`` / ``.olminstall-bvt-test-output.json``
written by ``test-finalize``; does not call the in-cluster TaskRun API.

Env:
    RESULT_PATH -- Tekton TEST_OUTPUT result file (required)
    TEST_GATES -- comma-separated gate ids (bvt, smoke, …)
    SMOKE_TEST_OUTPUT_PATH, BVT_TEST_OUTPUT_PATH -- workspace sidecar paths
"""
from __future__ import annotations

import json
import os
import sys

from _bootstrap import ensure_olminstall_path

ensure_olminstall_path()

from runners.report.pipeline_test_outputs import (
    build_finalize_test_output_from_taskruns,
    konflux_failure_test_output_json,
    publish_results_test_output_json,
)
from runners.report.junit_suite_report import read_gate_sidecar
from steps.tekton_util import write_result


def main() -> int:
    result_path = os.environ.get("RESULT_PATH", "").strip()
    if not result_path or "$(" in result_path:
        print("RESULT_PATH is required", file=sys.stderr)
        return 0

    smoke_path = os.environ.get("SMOKE_TEST_OUTPUT_PATH", "").strip()
    bvt_path = os.environ.get("BVT_TEST_OUTPUT_PATH", "").strip()
    test_gates = os.environ.get("TEST_GATES", "").strip()

    payload = build_finalize_test_output_from_taskruns(
        [],
        test_gates=test_gates,
        smoke_path=smoke_path,
        bvt_path=bvt_path,
    )
    if payload is not None:
        text = publish_results_test_output_json(
            payload,
            test_gates=test_gates,
            bvt_raw=read_gate_sidecar(bvt_path),
            smoke_raw=read_gate_sidecar(smoke_path),
        )
    else:
        pr_name = os.environ.get("PIPELINE_RUN_NAME", "").strip() or "unknown"
        text = konflux_failure_test_output_json(
            note=f"PipelineRun {pr_name}: no workspace gate TEST_OUTPUT sidecars",
        )

    write_result(result_path, text)
    print(f"Wrote pipeline TEST_OUTPUT from workspace ({len(text)} chars) to {result_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
