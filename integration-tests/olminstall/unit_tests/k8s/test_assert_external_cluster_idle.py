"""Tests for external-cluster single-flight guards."""

from __future__ import annotations

import unittest
from unittest import mock

from k8s.external_kubeconfig import (
    assert_external_cluster_idle,
    list_active_pipelineruns_for_external_cluster,
    wait_for_external_cluster_idle,
)
from suite.errors import AppError


class AssertExternalClusterIdleTest(unittest.TestCase):
    def test_skips_eaas(self) -> None:
        wait_for_external_cluster_idle(
            namespace="rhoai-tenant",
            cluster_source="EAAS",
        )

    def test_force_skips_busy(self) -> None:
        with mock.patch(
            "k8s.external_kubeconfig.list_active_pipelineruns_for_external_cluster",
            return_value=["pr-other"],
        ):
            wait_for_external_cluster_idle(
                namespace="rhoai-tenant",
                cluster_source="olminstall-kubeconfig-rh-nightly-pm",
                force=True,
            )

    def test_raises_when_busy_immediate(self) -> None:
        with mock.patch(
            "k8s.external_kubeconfig.list_active_pipelineruns_for_external_cluster",
            return_value=["pr-other"],
        ):
            with self.assertRaises(AppError) as ctx:
                assert_external_cluster_idle(
                    namespace="rhoai-tenant",
                    cluster_source="olminstall-kubeconfig-rh-nightly-pm",
                )
        self.assertIn("busy", str(ctx.exception))
        self.assertIn("--force-cluster-run", str(ctx.exception))

    def test_waits_until_idle(self) -> None:
        with mock.patch(
            "k8s.external_kubeconfig.list_active_pipelineruns_for_external_cluster",
            side_effect=[["pr-other"], []],
        ):
            with mock.patch("k8s.external_kubeconfig.time.sleep"):
                wait_for_external_cluster_idle(
                    namespace="rhoai-tenant",
                    cluster_source="olminstall-kubeconfig-rh-nightly-pm",
                    timeout_sec=120,
                    poll_interval_sec=5,
                )

    def test_list_active_matches_cluster_id(self) -> None:
        payload = {
            "items": [
                {
                    "metadata": {
                        "name": "olminstall-a",
                        "labels": {"olminstall.cluster": "ods-qe-psi-07"},
                    },
                    "spec": {"params": [{"name": "CLUSTER_SOURCE", "value": "sec-a"}]},
                    "status": {},
                },
                {
                    "metadata": {"name": "olminstall-b"},
                    "spec": {"params": [{"name": "CLUSTER_SOURCE", "value": "sec-b"}]},
                    "status": {"completionTime": "2026-06-28T12:00:00Z"},
                },
            ]
        }
        with mock.patch(
            "k8s.external_kubeconfig._list_olminstall_pipelinerun_items",
            return_value=payload["items"],
        ):
            with mock.patch(
                "k8s.external_kubeconfig.resolve_cluster_id_for_external_cluster",
                return_value="ods-qe-psi-07",
            ):
                active = list_active_pipelineruns_for_external_cluster(
                    namespace="rhoai-tenant",
                    cluster_source="olminstall-kubeconfig-ods-qe-psi-07-nmanos",
                    cluster_id="ods-qe-psi-07",
                )
        self.assertEqual(active, ["olminstall-a"])
