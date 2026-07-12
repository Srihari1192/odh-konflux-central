"""Tests for dashboard route verify (Jenkins verifyDashboardRoute parity)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from components.dashboard_cypress.verify_route import (
    verify_dashboard_route_for_prepare,
    wait_for_dashboard_route,
)

class DashboardVerifyRouteTest(unittest.TestCase):
    @patch("components.dashboard_cypress.verify_route.oc_run")
    def test_wait_all_cluster_deployments_parallel(self, oc_run_mock: object) -> None:
        from components.dashboard_cypress.verify_route import wait_all_cluster_deployments_available

        def _oc_side_effect(cmd: list[str], **_kwargs: object) -> object:
            if cmd[:3] == ["get", "deployments", "-A"]:
                return type("R", (), {
                    "returncode": 0,
                    "stdout": json.dumps({"items": [
                        {"metadata": {"namespace": "ns-a", "name": "dep-1"}},
                        {"metadata": {"namespace": "ns-b", "name": "dep-2"}},
                    ]}),
                    "stderr": "",
                })()
            if "dep-1" in cmd:
                return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            return type("R", (), {"returncode": 1, "stdout": "", "stderr": "timeout"})()

        oc_run_mock.side_effect = _oc_side_effect
        self.assertFalse(wait_all_cluster_deployments_available(timeout_sec=30))
        self.assertEqual(oc_run_mock.call_count, 3)

    @patch("components.dashboard_cypress.verify_route._repair_gateway_stack_for_verify")
    @patch("components.dashboard_cypress.verify_route.verify_dashboard_reachable", return_value=True)
    @patch("components.dashboard_cypress.verify_route._dashboard_ready_status", return_value="True")
    @patch("components.dashboard_cypress.verify_route.wait_all_cluster_deployments_available", return_value=True)
    @patch(
        "components.dashboard_cypress.verify_route.resolve_odh_dashboard_base_url",
        return_value="https://rh-ai.apps.example.com",
    )
    def test_wait_runs_gateway_repair_for_rhoai_install(self, *_mocks: object) -> None:
        with patch.dict(os.environ, {"PRODUCT": "rhoai"}, clear=False):
            self.assertEqual(
                wait_for_dashboard_route(timeout_sec=60, poll_sec=0, deployment_wait_sec=1),
                "https://rh-ai.apps.example.com",
            )

    @patch("components.dashboard_cypress.verify_route._repair_gateway_stack_for_verify")
    @patch("components.dashboard_cypress.verify_route.verify_dashboard_reachable", return_value=True)
    @patch("components.dashboard_cypress.verify_route._dashboard_ready_status", return_value="True")
    @patch("components.dashboard_cypress.verify_route.wait_all_cluster_deployments_available", return_value=True)
    @patch(
        "components.dashboard_cypress.verify_route.resolve_odh_dashboard_base_url",
        return_value="https://rh-ai.apps.example.com",
    )
    def test_wait_for_dashboard_route_success(self, *_mocks: object) -> None:
        self.assertEqual(
            wait_for_dashboard_route(timeout_sec=60, poll_sec=0, deployment_wait_sec=1),
            "https://rh-ai.apps.example.com",
        )

    @patch("components.dashboard_cypress.verify_route._repair_gateway_stack_for_verify")
    @patch("components.dashboard_cypress.verify_route.verify_dashboard_reachable", return_value=True)
    @patch("components.dashboard_cypress.verify_route._dashboard_ready_status", return_value="False")
    @patch("components.dashboard_cypress.verify_route.wait_all_cluster_deployments_available", return_value=False)
    @patch(
        "components.dashboard_cypress.verify_route.resolve_odh_dashboard_base_url",
        return_value="https://rh-ai.apps.example.com",
    )
    def test_wait_accepts_reachable_gateway_when_dashboard_not_ready(self, *_mocks: object) -> None:
        self.assertEqual(
            wait_for_dashboard_route(timeout_sec=60, poll_sec=0, deployment_wait_sec=1),
            "https://rh-ai.apps.example.com",
        )

    @patch.dict(os.environ, {"PRODUCT": "existing"}, clear=False)
    @patch("components.dashboard_cypress.verify_route._repair_gateway_stack_for_verify")
    @patch("components.dashboard_cypress.verify_route.verify_dashboard_reachable", return_value=True)
    @patch("components.dashboard_cypress.verify_route._dashboard_ready_status", return_value="True")
    @patch("components.dashboard_cypress.verify_route.wait_all_cluster_deployments_available", return_value=True)
    @patch(
        "components.dashboard_cypress.verify_route.resolve_odh_dashboard_base_url",
        return_value="https://rh-ai.apps.example.com",
    )
    def test_wait_runs_gateway_repair_for_existing_cluster(self, repair_mock: object, *_mocks: object) -> None:
        self.assertEqual(
            wait_for_dashboard_route(timeout_sec=60, poll_sec=0, deployment_wait_sec=1),
            "https://rh-ai.apps.example.com",
        )
        self.assertTrue(repair_mock.called)

    @patch.dict(os.environ, {"PRODUCT": "existing"}, clear=False)
    @patch("components.dashboard_cypress.verify_route._repair_gateway_stack_for_verify")
    @patch("components.dashboard_cypress.verify_route.verify_dashboard_reachable", return_value=False)
    @patch("components.dashboard_cypress.verify_route._dashboard_ready_status", return_value="True")
    @patch("components.dashboard_cypress.verify_route.wait_all_cluster_deployments_available", return_value=True)
    @patch(
        "components.dashboard_cypress.verify_route.resolve_odh_dashboard_base_url",
        return_value="https://rh-ai.apps.example.com",
    )
    @patch("components.dashboard_cypress.verify_route.time.sleep")
    def test_wait_times_out_existing_when_gateway_unreachable(self, _sleep: object, *_mocks: object) -> None:
        with self.assertRaises(RuntimeError):
            wait_for_dashboard_route(timeout_sec=1, poll_sec=0, deployment_wait_sec=0)

    @patch("components.dashboard_cypress.verify_route._repair_gateway_stack_for_verify")
    @patch("components.dashboard_cypress.verify_route.verify_dashboard_reachable", return_value=False)
    @patch("components.dashboard_cypress.verify_route._dashboard_ready_status", return_value="False")
    @patch("components.dashboard_cypress.verify_route.wait_all_cluster_deployments_available", return_value=True)
    @patch("components.dashboard_cypress.verify_route.resolve_odh_dashboard_base_url", return_value="")
    @patch("components.dashboard_cypress.verify_route.time.sleep")
    def test_wait_for_dashboard_route_times_out(self, _sleep: object, *_mocks: object) -> None:
        with self.assertRaises(RuntimeError):
            wait_for_dashboard_route(timeout_sec=1, poll_sec=0, deployment_wait_sec=0)

    @patch("components.dashboard_cypress.verify_route.wait_for_dashboard_route")
    def test_verify_writes_config(self, wait_mock: object) -> None:
        wait_mock.return_value = "https://dash.example.com"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            url = verify_dashboard_route_for_prepare(artifacts_dir=root)
            self.assertEqual(url, "https://dash.example.com")
            cfg = root / "dashboard-cypress-config.yml"
            self.assertTrue(cfg.is_file())
            self.assertIn("ODH_DASHBOARD_URL: https://dash.example.com", cfg.read_text(encoding="utf-8"))

    @patch.dict(os.environ, {"PRODUCT": "existing"}, clear=False)
    @patch("components.dashboard_cypress.verify_route.verify_dashboard_reachable", return_value=False)
    def test_dashboard_cypress_accessible_for_smoke_requires_curl(self, *_mocks: object) -> None:
        from components.dashboard_cypress.verify_route import dashboard_cypress_accessible_for_smoke

        self.assertFalse(dashboard_cypress_accessible_for_smoke(url="https://rh-ai.example.com"))

    @patch.dict(os.environ, {"PRODUCT": "existing"}, clear=False)
    @patch("components.dashboard_cypress.verify_route.verify_dashboard_reachable", return_value=True)
    def test_dashboard_cypress_accessible_for_smoke_when_curl_ok(self, *_mocks: object) -> None:
        from components.dashboard_cypress.verify_route import dashboard_cypress_accessible_for_smoke

        self.assertTrue(dashboard_cypress_accessible_for_smoke(url="https://rh-ai.example.com"))

    @patch.dict(os.environ, {"PRODUCT": "existing"}, clear=False)
    def test_dashboard_cypress_accessible_for_smoke_requires_url(self) -> None:
        from components.dashboard_cypress.verify_route import dashboard_cypress_accessible_for_smoke

        self.assertFalse(dashboard_cypress_accessible_for_smoke(url=""))

