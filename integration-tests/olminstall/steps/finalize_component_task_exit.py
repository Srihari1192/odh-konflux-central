#!/usr/bin/env python3
"""Rewrite component-test.exit and return Tekton-friendly exit after shell test steps.

Used by golang/playwright component images that cannot import olminstall during the run step.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from _bootstrap import ensure_olminstall_path

ensure_olminstall_path()

from suite.component_task_exit import component_from_plan, resolve_component_exit_codes
from suite.dsc_baseline import finalize_component_dsc_hygiene
from steps.tekton_util import require_env


def _component_test_output_published() -> bool:
    """True when summarize (or write-konflux-task-summary backfill) wrote TEST_OUTPUT."""
    raw = os.environ.get("TEST_OUTPUT_PATH", "").strip()
    if not raw or "$(" in raw:
        return False
    path = Path(raw)
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return False
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(obj, dict) and str(obj.get("result", "")).strip()


def main() -> int:
    artifacts_dir = Path(require_env("ARTIFACTS_DIR"))
    component_id = require_env("COMPONENT_ID")
    plan_path = Path(require_env("COMPONENT_TEST_PLAN_JSON"))
    exit_path = artifacts_dir / "component-test.exit"
    raw_ec = 1  # missing marker => treat as failure until proven otherwise
    if exit_path.is_file():
        try:
            raw_ec = int(exit_path.read_text(encoding="ascii").strip())
        except ValueError:
            raw_ec = 1

    comp = component_from_plan(plan_path, component_id)
    if comp is None:
        print(f"WARN: {component_id} missing from plan; keeping run exit {raw_ec}", file=sys.stderr)
        return raw_ec

    strict_ec, tekton_ec = resolve_component_exit_codes(
        comp,
        raw_ec=raw_ec,
        artifacts_dir=artifacts_dir,
    )
    drifts = finalize_component_dsc_hygiene(component_id, artifacts_dir)
    if drifts:
        print(
            f"DSC drift attributed to {component_id}: {'; '.join(drifts)} \u2014 failing task",
            flush=True,
        )
        strict_ec = max(strict_ec, 1)
        tekton_ec = 1
    exit_path.write_text(str(strict_ec), encoding="ascii")
    if _component_test_output_published():
        if tekton_ec != 0:
            print(
                f"Component {component_id}: recorded exit {strict_ec} in component-test.exit; "
                "Tekton finalize exit 0 so Konflux UI shows TEST_OUTPUT results",
                flush=True,
            )
        return 0
    return tekton_ec


if __name__ == "__main__":
    raise SystemExit(main())
