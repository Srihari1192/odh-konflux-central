"""Unit tests for setup-dependencies finalize recovery (no cluster)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from install.dependency_operators import (  # noqa: E402
    ensure_setup_dependency_namespaces_ready,
    finalize_dependency_operators_after_setup_script,
)

class FinalizeDependencyOperatorsTest(unittest.TestCase):
    def test_complete_failure_when_authorino_crd_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            olm_dir = Path(tmp) / "olminstall"
            olm_dir.mkdir()
            (olm_dir / "odh-gitops").mkdir()

            with patch(
                "install.dependency_operators.ensure_setup_dependency_namespaces_ready",
            ):
                with patch(
                    "install.dependency_operators._reconcile_rhcl_after_gitops_with_warning",
                    return_value=False,
                ):
                    with patch(
                        "install.dependency_operators._run_odh_gitops_make",
                        return_value=1,
                    ) as make_run:
                        with patch(
                            "install.dependency_operators._authorino_crd_available",
                            return_value=False,
                        ):
                            with patch(
                                "install.dependency_operators._ensure_authorino_operators_after_setup",
                            ) as authorino:
                                rc = finalize_dependency_operators_after_setup_script(olm_dir, 2)
            self.assertEqual(rc, 1)
            make_run.assert_called_once()
            authorino.assert_not_called()

    def test_partial_failure_runs_authorino_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            olm_dir = Path(tmp) / "olminstall"
            olm_dir.mkdir()
            (olm_dir / "odh-gitops").mkdir()

            with patch(
                "install.dependency_operators.ensure_setup_dependency_namespaces_ready",
            ):
                with patch(
                    "install.dependency_operators._reconcile_rhcl_after_gitops_with_warning",
                    return_value=False,
                ) as reconcile:
                    with patch(
                        "install.dependency_operators._run_odh_gitops_make",
                        return_value=1,
                    ):
                        with patch(
                            "install.dependency_operators._authorino_crd_available",
                            return_value=True,
                        ):
                            with patch(
                                "install.dependency_operators._ensure_authorino_operators_after_setup",
                            ) as authorino:
                                with patch(
                                    "install.dependency_operators.maas_dependency_operators_ready",
                                    return_value=True,
                                ):
                                    rc = finalize_dependency_operators_after_setup_script(olm_dir, 2)
            self.assertEqual(rc, 2)
            self.assertEqual(reconcile.call_count, 2)
            authorino.assert_called_once()

    def test_partial_failure_reconciles_rhcl_before_gitops_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            olm_dir = Path(tmp) / "olminstall"
            olm_dir.mkdir()
            (olm_dir / "odh-gitops").mkdir()
            call_order: list[str] = []

            def _reconcile(_olm: Path) -> bool:
                call_order.append("reconcile")
                return False

            def _make(_olm: Path, *_args: str) -> int:
                call_order.append("make")
                return 1

            with patch(
                "install.dependency_operators.ensure_setup_dependency_namespaces_ready",
            ):
                with patch(
                    "install.dependency_operators._reconcile_rhcl_after_gitops_with_warning",
                    side_effect=_reconcile,
                ):
                    with patch(
                        "install.dependency_operators._run_odh_gitops_make",
                        side_effect=_make,
                    ):
                        with patch(
                            "install.dependency_operators._authorino_crd_available",
                            return_value=False,
                        ):
                            finalize_dependency_operators_after_setup_script(olm_dir, 2)
            self.assertEqual(call_order, ["reconcile", "make", "reconcile"])

class EnsureSetupDependencyNamespacesTest(unittest.TestCase):
    @patch("install.dependency_operators.time.sleep")
    @patch("install.dependency_operators.unblock_terminating_namespace")
    @patch(
        "install.dependency_operators._namespace_phase",
        side_effect=["Terminating", "Active", "Active"],
    )
    def test_waits_until_terminating_namespace_cleared(
        self,
        _phase,
        unblock,
        _sleep,
    ) -> None:
        ensure_setup_dependency_namespaces_ready(
            ("openshift-keda",),
            timeout_sec=30,
        )
        unblock.assert_called_once_with("openshift-keda")

if __name__ == "__main__":
    raise SystemExit(unittest.main())
