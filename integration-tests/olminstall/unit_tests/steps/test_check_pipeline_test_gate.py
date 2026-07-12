"""Tests for test-finalize pipeline gate enforcement."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runners.report import check_pipeline_test_gate as gate_mod

class CheckPipelineTestGateTest(unittest.TestCase):
    def test_smoke_from_workspace_when_only_bvt_in_taskruns(self) -> None:
        bvt_json = json.dumps(
            {
                "result": "SUCCESS",
                "successes": 5,
                "failures": 0,
                "skipped": 0,
                "note": "bvt: 100% pass rate (5 passed, 0 failed, 0 skipped)",
            }
        )
        smoke_json = json.dumps(
            {
                "result": "WARNING",
                "successes": 2,
                "failures": 1,
                "skipped": 0,
                "note": "smoke: 67% pass rate (2 passed, 1 failed, 0 skipped)",
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            smoke_path = Path(tmp) / "smoke.json"
            smoke_path.write_text(smoke_json, encoding="utf-8")
            env = {
                "TEST_GATES": "bvt,smoke",
                "PIPELINE_RUN_NAME": "pr-1",
                "NAMESPACE": "rhoai-tenant",
                "SMOKE_TEST_OUTPUT_PATH": str(smoke_path),
            }
            with patch.dict(os.environ, env, clear=False):
                with patch.object(gate_mod, "list_taskruns_in_cluster", return_value=[{"metadata": {"name": "tr"}}]):
                    with patch.object(
                        gate_mod,
                        "list_pipeline_test_outputs",
                        return_value=[("bvt", bvt_json)],
                    ):
                        self.assertEqual(gate_mod.main(), 0)

if __name__ == "__main__":
    raise SystemExit(unittest.main())
