"""Fail publish-results when requested BVT/smoke/install gates never executed.

Uses in-cluster PipelineRun TaskRun state so hollow green runs fail at
publish-results instead of reporting PASSED.

Intentional e2e skips (``CONFORMA_GATE=skip`` from wait-for-conforma, e.g. catalog
line below ``MIN_RHOAI_VERSION`` or conforma fail/timeout) are not hollow green:
install/smoke are skipped via Tekton ``when`` and the PipelineRun should succeed
with WARNING from wait-for-conforma.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from runners.report.junit_suite_report import GATE_NOT_RUN_SUMMARY, is_gate_summary_placeholder
from steps.pipeline_task_state import require_pipeline_tasks_ran
from steps.tekton_incluster import (
    list_taskruns_in_cluster,
    namespace_from_env,
    pipeline_run_name_from_env,
    result_map,
    task_name,
)
from suite.conforma_gate import CONFORMA_GATE_SKIP


def _requested_gates() -> set[str]:
    raw = (os.environ.get("TEST_GATES") or "").strip()
    if not raw:
        return set()
    return {g.strip().lower() for g in raw.split(",") if g.strip()}


def _install_tasks_for_product(product: str) -> tuple[str, ...]:
    p = (product or "").strip().lower()
    if p == "existing":
        return ()
    if p == "rhoai":
        return ("install-dep-operators", "install-rhoai")
    if p == "odh":
        return ("install-dep-operators", "install-odh")
    return ("install-dep-operators", "install-rhoai", "install-odh")


def read_gate_result(path: str) -> str:
    p = Path(path)
    if not p.is_file():
        return ""
    return p.read_text(encoding="utf-8").strip()


def _gate_result_ok(gate_val: str) -> bool:
    return bool(gate_val) and not is_gate_summary_placeholder(gate_val) and gate_val.strip() != GATE_NOT_RUN_SUMMARY


def _normalize_conforma_gate(raw: str | None) -> str:
    return (raw or "").strip().lower()


def _conforma_gate_from_taskrun(*, pipeline_run: str, namespace: str) -> str:
    """Read wait-for-conforma CONFORMA_GATE when the pipeline param was not wired."""
    if not pipeline_run or not namespace:
        return ""
    for tr in list_taskruns_in_cluster(pipeline_run, namespace):
        if task_name(tr) == "wait-for-conforma":
            return _normalize_conforma_gate(result_map(tr).get("CONFORMA_GATE"))
    return ""


def intentional_conforma_e2e_skip(
    *,
    conforma_gate: str | None = None,
    pipeline_run: str = "",
    namespace: str = "",
) -> bool:
    """True when wait-for-conforma skipped e2e (min-RHOAI / conforma fail / timeout)."""
    gate = _normalize_conforma_gate(
        conforma_gate if conforma_gate is not None else os.environ.get("CONFORMA_GATE")
    )
    if gate:
        return gate == CONFORMA_GATE_SKIP
    pr = (pipeline_run or pipeline_run_name_from_env()).strip()
    ns = (namespace or namespace_from_env()).strip()
    return _conforma_gate_from_taskrun(pipeline_run=pr, namespace=ns) == CONFORMA_GATE_SKIP


def collect_hollow_green_failures(
    *,
    test_gates: str | None = None,
    product: str | None = None,
    gate_values: dict[str, str] | None = None,
    conforma_gate: str | None = None,
) -> list[str]:
    """Return human-readable failure lines when requested gates did not run."""
    gates = _requested_gates() if test_gates is None else {
        g.strip().lower() for g in (test_gates or "").split(",") if g.strip()
    }
    if not gates:
        return []

    prod = (product if product is not None else os.environ.get("PRODUCT") or "").strip().lower()
    pr_name = pipeline_run_name_from_env().strip()
    ns = namespace_from_env().strip()
    has_cluster = bool(pr_name and ns)

    if intentional_conforma_e2e_skip(
        conforma_gate=conforma_gate,
        pipeline_run=pr_name,
        namespace=ns,
    ):
        return []

    failures: list[str] = []
    install_tasks = _install_tasks_for_product(prod)
    if has_cluster:
        failures.extend(
            require_pipeline_tasks_ran(
                install_tasks,
                pipeline_run=pr_name,
                namespace=ns,
                allow_failed=True,
            )
        )
    else:
        for task in install_tasks:
            failures.append(f"{task}: cannot verify execution (no PipelineRun context)")

    for gate in sorted(gates):
        gate_key = f"{gate.upper()}_GATE"
        gate_val = ""
        if gate_values:
            gate_val = (gate_values.get(gate_key) or "").strip()
        if not gate_val:
            gate_path = os.environ.get(f"{gate.upper()}_GATE_PATH", "")
            if gate_path:
                gate_val = read_gate_result(gate_path)

        task_ok = False
        if has_cluster:
            task_errors = require_pipeline_tasks_ran(
                (f"run-{gate}-tests",),
                pipeline_run=pr_name,
                namespace=ns,
                allow_failed=True,
            )
            task_ok = not task_errors
        gate_ok = _gate_result_ok(gate_val)

        if task_ok or gate_ok:
            continue
        if gate_val and not gate_ok:
            failures.append(f"{gate} gate result is placeholder: {gate_val!r}")
        elif has_cluster:
            failures.append(
                f"{gate}: run-{gate}-tests did not execute and no gate result published"
            )
        else:
            failures.append(
                f"{gate}: no PipelineRun context and gate result missing or placeholder"
            )

    return failures


def main() -> int:
    if intentional_conforma_e2e_skip():
        print("CONFORMA_GATE=skip - e2e intentionally not run; skipping hollow-green check")
        return 0
    failures = collect_hollow_green_failures()
    if failures:
        print("Requested gates did not run:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print("All requested gates executed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
