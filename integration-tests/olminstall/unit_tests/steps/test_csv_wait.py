"""Unit tests for operator CSV wait helpers."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from install import install_and_verify as iav

class WaitForSucceededCsvVersionTest(unittest.TestCase):
    def test_returns_version_when_already_succeeded(self) -> None:
        with (
            patch("install.approve_transitive_installplans.approve_pending_installplans", return_value=0),
            patch.object(iav, "_subscription_target_csv", return_value=None),
            patch.object(iav, "pick_succeeded_csv_version", return_value="3.5.0-ea.2"),
        ):
            ver = iav.wait_for_succeeded_csv_version("redhat-ods-operator", "rhods-operator", timeout_sec=30)
        self.assertEqual(ver, "3.5.0-ea.2")

    def test_polls_until_succeeded(self) -> None:
        phases = [None, None, "3.5.0-ea.2"]
        with (
            patch("install.approve_transitive_installplans.approve_pending_installplans", return_value=0),
            patch.object(iav, "_subscription_target_csv", return_value=None),
            patch.object(iav, "pick_succeeded_csv_version", side_effect=phases),
            patch.object(iav, "_operator_csv_phase", side_effect=[("rhods-operator.3.5.0-ea.2", "Installing"), ("rhods-operator.3.5.0-ea.2", "InstallReady")]),
            patch.object(iav.time, "monotonic", side_effect=[0, 1, 2, 3]),
            patch.object(iav.time, "sleep"),
        ):
            ver = iav.wait_for_succeeded_csv_version("redhat-ods-operator", "rhods-operator", timeout_sec=60, poll_sec=1)
        self.assertEqual(ver, "3.5.0-ea.2")

    def test_fails_fast_on_failed_phase(self) -> None:
        with (
            patch("install.approve_transitive_installplans.approve_pending_installplans", return_value=0),
            patch.object(iav, "_subscription_target_csv", return_value=None),
            patch.object(iav, "pick_succeeded_csv_version", return_value=None),
            patch.object(iav, "_operator_csv_phase", return_value=("rhods-operator.3.5.0-ea.2", "Failed")),
            patch.object(iav.time, "monotonic", return_value=0),
        ):
            ver = iav.wait_for_succeeded_csv_version("redhat-ods-operator", "rhods-operator", timeout_sec=60)
        self.assertIsNone(ver)

