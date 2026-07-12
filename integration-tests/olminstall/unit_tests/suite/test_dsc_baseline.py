"""Tests for DSC baseline snapshot and drift detection."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from suite.dsc_baseline import (
    _extract_management_states,
    check_dsc_drift,
    filter_drifts_for_component,
    finalize_component_dsc_hygiene,
    load_dsc_baseline,
    read_dsc_drift_marker,
    reconcile_baseline_dsc_before_component,
    restore_dsc_from_baseline,
    wait_for_baseline_spec,
    wait_for_ready_reconcile,
    write_dsc_drift_marker,
    check_baseline_managed_ready_stale,
)

class ExtractManagementStatesTest(unittest.TestCase):
    def test_extracts_states(self) -> None:
        spec = {
            "dashboard": {"managementState": "Managed"},
            "workbenches": {"managementState": "Removed"},
            "kserve": {"managementState": "Managed", "modelsAsService": {"managementState": "Managed"}},
        }
        states = _extract_management_states(spec)
        self.assertEqual(states["dashboard"], "Managed")
        self.assertEqual(states["workbenches"], "Removed")
        self.assertEqual(states["kserve"], "Managed")

    def test_empty_spec(self) -> None:
        self.assertEqual(_extract_management_states({}), {})

class LoadBaselineTest(unittest.TestCase):
    def test_no_baseline_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_dsc_baseline(Path(tmp)))

    def test_loads_saved_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = {"dashboard": {"managementState": "Managed"}}
            (root / ".dsc-baseline.json").write_text(
                json.dumps(spec), encoding="utf-8"
            )
            loaded = load_dsc_baseline(root)
            self.assertEqual(loaded, spec)

class CheckDscDriftTest(unittest.TestCase):
    """Offline drift detection against a saved baseline (no cluster)."""

    def test_no_baseline_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(check_dsc_drift(Path(tmp)), [])

    def test_detects_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = {
                "dashboard": {"managementState": "Managed"},
                "workbenches": {"managementState": "Managed"},
            }
            (root / ".dsc-baseline.json").write_text(
                json.dumps(baseline), encoding="utf-8"
            )
            current = {
                "dashboard": {"managementState": "Removed"},
                "workbenches": {"managementState": "Managed"},
            }
            import unittest.mock as mock

            with mock.patch(
                "suite.dsc_baseline._oc_get_dsc_components_spec", return_value=current
            ):
                drifts = check_dsc_drift(root)
            self.assertEqual(len(drifts), 1)
            self.assertIn("dashboard", drifts[0])
            self.assertIn("Managed", drifts[0])
            self.assertIn("Removed", drifts[0])

    def test_no_drift_when_states_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = {
                "dashboard": {"managementState": "Managed"},
                "workbenches": {"managementState": "Removed"},
            }
            (root / ".dsc-baseline.json").write_text(
                json.dumps(spec), encoding="utf-8"
            )
            import unittest.mock as mock

            with mock.patch(
                "suite.dsc_baseline._oc_get_dsc_components_spec", return_value=spec
            ):
                self.assertEqual(check_dsc_drift(root), [])

class DriftMarkerTest(unittest.TestCase):
    def test_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_dsc_drift_marker(root, "workbenches", ["dashboard: Managed\u2192Removed"])
            drifts = read_dsc_drift_marker(root, "workbenches")
            self.assertEqual(drifts, ["dashboard: Managed\u2192Removed"])

    def test_no_marker_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(read_dsc_drift_marker(Path(tmp), "workbenches"), [])

class RestoreFromBaselineTest(unittest.TestCase):
    def test_no_baseline_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(restore_dsc_from_baseline(Path(tmp)))

    def test_restore_calls_oc_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = {"dashboard": {"managementState": "Managed"}}
            (root / ".dsc-baseline.json").write_text(
                json.dumps(baseline), encoding="utf-8"
            )
            import subprocess
            import unittest.mock as mock

            fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            with mock.patch("install.dsc_install.run_oc", return_value=fake_result) as mock_oc:
                result = restore_dsc_from_baseline(root)
            self.assertTrue(result)
            call_args = mock_oc.call_args
            args_list = call_args[0][0] if call_args[0] else call_args[1].get("args", [])
            self.assertIn("patch", args_list)
            self.assertIn("datasciencecluster", args_list)

class FilterDriftsForComponentTest(unittest.TestCase):
    def test_filters_to_managed_keys(self) -> None:
        import unittest.mock as mock

        drifts = [
            "dashboard: Managed\u2192Removed",
            "workbenches: Managed\u2192Removed",
        ]
        with mock.patch(
            "suite.dsc_baseline._managed_dsc_keys_for_component",
            return_value={"dashboard"},
        ):
            out = filter_drifts_for_component("dashboard_cypress", drifts)
        self.assertEqual(out, ["dashboard: Managed\u2192Removed"])

    def test_empty_when_no_managed_overlap(self) -> None:
        import unittest.mock as mock

        with mock.patch(
            "suite.dsc_baseline._managed_dsc_keys_for_component",
            return_value={"ogx"},
        ):
            out = filter_drifts_for_component(
                "ai_safety_evalhub",
                ["dashboard: Managed\u2192Removed"],
            )
        self.assertEqual(out, [])

class WaitForBaselineSpecTest(unittest.TestCase):
    def test_no_baseline_returns_true(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(wait_for_baseline_spec(Path(tmp), timeout_sec=1))

    def test_returns_true_when_spec_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = {"dashboard": {"managementState": "Managed"}}
            (root / ".dsc-baseline.json").write_text(
                json.dumps(spec), encoding="utf-8"
            )
            import unittest.mock as mock

            with mock.patch(
                "suite.dsc_baseline.check_dsc_drift", return_value=[]
            ):
                self.assertTrue(wait_for_baseline_spec(root, timeout_sec=1))

class FinalizeComponentDscHygieneTest(unittest.TestCase):
    def test_no_drift_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            import unittest.mock as mock

            with (
                mock.patch("suite.dsc_baseline.check_dsc_drift", return_value=[]),
                mock.patch(
                    "suite.dsc_baseline.check_baseline_managed_ready_stale",
                    return_value=set(),
                ),
            ):
                out = finalize_component_dsc_hygiene("workbenches", Path(tmp))
            self.assertEqual(out, [])

    def test_unattributed_drift_restores_without_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            import unittest.mock as mock

            drifts = ["dashboard: Managed\u2192Removed"]
            with (
                mock.patch("suite.dsc_baseline.check_dsc_drift", return_value=drifts),
                mock.patch(
                    "suite.dsc_baseline.check_baseline_managed_ready_stale",
                    return_value=set(),
                ),
                mock.patch(
                    "suite.dsc_baseline.filter_drifts_for_component", return_value=[]
                ),
                mock.patch(
                    "suite.dsc_baseline.restore_dsc_from_baseline", return_value=True
                ) as mock_restore,
                mock.patch(
                    "suite.dsc_baseline.wait_for_baseline_spec", return_value=True
                ),
                mock.patch(
                    "suite.dsc_baseline.wait_for_ready_reconcile", return_value=True
                ) as mock_ready,
            ):
                out = finalize_component_dsc_hygiene("ai_safety_evalhub", root)
            self.assertEqual(out, [])
            mock_restore.assert_called_once()
            mock_ready.assert_called_once_with({"dashboard"})
            self.assertEqual(read_dsc_drift_marker(root, "ai_safety_evalhub"), [])

    def test_attributed_drift_writes_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            import unittest.mock as mock

            drifts = ["dashboard: Managed\u2192Removed"]
            with (
                mock.patch("suite.dsc_baseline.check_dsc_drift", return_value=drifts),
                mock.patch(
                    "suite.dsc_baseline.check_baseline_managed_ready_stale",
                    return_value=set(),
                ),
                mock.patch(
                    "suite.dsc_baseline.filter_drifts_for_component", return_value=drifts
                ),
                mock.patch(
                    "suite.dsc_baseline.restore_dsc_from_baseline", return_value=True
                ),
                mock.patch(
                    "suite.dsc_baseline.wait_for_baseline_spec", return_value=True
                ),
                mock.patch(
                    "suite.dsc_baseline.wait_for_ready_reconcile", return_value=True
                ),
            ):
                out = finalize_component_dsc_hygiene("dashboard_cypress", root)
            self.assertEqual(out, drifts)
            self.assertEqual(read_dsc_drift_marker(root, "dashboard_cypress"), drifts)

    def test_stale_ready_reconciles_without_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            import unittest.mock as mock

            with (
                mock.patch("suite.dsc_baseline.check_dsc_drift", return_value=[]),
                mock.patch(
                    "suite.dsc_baseline.check_baseline_managed_ready_stale",
                    return_value={"dashboard", "mlflowoperator"},
                ),
                mock.patch(
                    "suite.dsc_baseline.restore_dsc_from_baseline", return_value=True
                ) as mock_restore,
                mock.patch(
                    "suite.dsc_baseline.wait_for_ready_reconcile", return_value=True
                ) as mock_ready,
            ):
                out = finalize_component_dsc_hygiene("maas_billing", root)
            self.assertEqual(out, [])
            mock_restore.assert_called_once()
            mock_ready.assert_called_once_with({"dashboard", "mlflowoperator"})

class ReconcileBeforeComponentTest(unittest.TestCase):
    def test_reconcile_when_stale_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            import unittest.mock as mock

            with (
                mock.patch("suite.dsc_baseline.check_dsc_drift", return_value=[]),
                mock.patch(
                    "suite.dsc_baseline.check_baseline_managed_ready_stale",
                    return_value={"mlflowoperator"},
                ),
                mock.patch(
                    "suite.dsc_baseline.restore_dsc_from_baseline", return_value=True
                ) as mock_restore,
                mock.patch(
                    "suite.dsc_baseline.wait_for_ready_reconcile", return_value=True
                ) as mock_ready,
            ):
                reconcile_baseline_dsc_before_component("mlflow", root)
            mock_restore.assert_called_once()
            mock_ready.assert_called_once_with({"mlflowoperator"})

