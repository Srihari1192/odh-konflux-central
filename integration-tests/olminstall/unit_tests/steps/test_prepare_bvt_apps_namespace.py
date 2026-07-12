#!/usr/bin/env python3
"""Unit tests for BVT application-namespace pod reconciliation."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from steps.prepare_bvt_apps_namespace import (  # noqa: E402
    _migration_version_from_job_name,
    reconcile_stuck_mlflow_migration_pods_for_bvt,
)

class PrepareBvtAppsNamespaceTest(unittest.TestCase):
    def test_migration_version_from_job_name(self) -> None:
        self.assertEqual(_migration_version_from_job_name("mlflow-mg-3120-g1"), "3.12.0")

    def test_skips_when_mlflow_not_available(self) -> None:
        with patch("steps.prepare_bvt_apps_namespace._mlflow_deployment_available", return_value=False):
            with patch("steps.prepare_bvt_apps_namespace.oc_run") as oc_run:
                reconcile_stuck_mlflow_migration_pods_for_bvt()
                oc_run.assert_not_called()

    def test_deletes_stuck_migration_pods_jobs_and_patches_status(self) -> None:
        pods = {
            "items": [
                {"metadata": {"name": "mlflow-mg-3120-g1-b6sld"}, "status": {"phase": "Pending"}},
                {"metadata": {"name": "mlflow-abc"}, "status": {"phase": "Running"}},
            ]
        }
        status_version_calls = {"n": 0}

        def _oc_run(args, **kwargs):
            cmd = list(args)
            if cmd[:3] == ["get", "deploy", "mlflow"]:
                return type("R", (), {"returncode": 0, "stdout": "True", "stderr": ""})()
            if cmd[:3] == ["get", "mlflow", "mlflow"]:
                status_version_calls["n"] += 1
                version = "" if status_version_calls["n"] < 4 else "3.12.0"
                return type("R", (), {"returncode": 0, "stdout": version, "stderr": ""})()
            if cmd[:3] == ["get", "pods", "-n"]:
                return type("R", (), {"returncode": 0, "stdout": json.dumps(pods), "stderr": ""})()
            if cmd[:3] == ["get", "jobs", "-n"]:
                return type(
                    "R",
                    (),
                    {"returncode": 0, "stdout": "mlflow-mg-3120-g1\nother-job\n", "stderr": ""},
                )()
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with patch("steps.prepare_bvt_apps_namespace.time.sleep"):
            with patch("steps.prepare_bvt_apps_namespace.oc_run", side_effect=_oc_run) as oc_run:
                reconcile_stuck_mlflow_migration_pods_for_bvt()
                delete_cmds = [list(c.args[0]) for c in oc_run.call_args_list if c.args[0][0] == "delete"]
                patch_cmds = [list(c.args[0]) for c in oc_run.call_args_list if c.args[0][0] == "patch"]
                self.assertIn(
                    ["delete", "pod", "mlflow-mg-3120-g1-b6sld", "-n", "redhat-ods-applications", "--ignore-not-found"],
                    delete_cmds,
                )
                self.assertIn(
                    ["delete", "job", "mlflow-mg-3120-g1", "-n", "redhat-ods-applications", "--ignore-not-found"],
                    delete_cmds,
                )
                self.assertTrue(any(cmd[1:4] == ["mlflow", "mlflow", "--type=merge"] for cmd in patch_cmds))

if __name__ == "__main__":
    raise SystemExit(unittest.main())
