"""Tests for external-cluster trigger guards in runner_support."""

from __future__ import annotations

import unittest

from runners.cli.runner_support import (
    pipelinerun_external_cluster_id,
    refuse_owned_external_trigger_message,
)


class RefuseOwnedExternalTriggerTest(unittest.TestCase):
    def test_refuses_same_cluster_without_force(self) -> None:
        msg = refuse_owned_external_trigger_message(
            owned_name="olminstall-psi-23-a",
            owned_cluster_id="ods-qe-psi-23",
            target_cluster_id="ods-qe-psi-23",
            watch_cli="python3 olm_pipeline.py -w olminstall-psi-23-a",
            force=False,
        )
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertIn("Refusing", msg)
        self.assertIn("--force-cluster-run", msg)

    def test_allows_different_cluster(self) -> None:
        self.assertIsNone(
            refuse_owned_external_trigger_message(
                owned_name="olminstall-psi-07-a",
                owned_cluster_id="ods-qe-psi-07",
                target_cluster_id="ods-qe-psi-23",
                watch_cli="watch",
                force=False,
            )
        )

    def test_force_overrides_refusal(self) -> None:
        self.assertIsNone(
            refuse_owned_external_trigger_message(
                owned_name="olminstall-psi-23-a",
                owned_cluster_id="ods-qe-psi-23",
                target_cluster_id="ods-qe-psi-23",
                watch_cli="watch",
                force=True,
            )
        )


class PipelinerunExternalClusterIdTest(unittest.TestCase):
    def test_prefers_cluster_label(self) -> None:
        item = {"metadata": {"labels": {"olminstall.cluster": "ods-qe-psi-23"}}}
        self.assertEqual(
            pipelinerun_external_cluster_id(item, namespace="rhoai-tenant"),
            "ods-qe-psi-23",
        )


if __name__ == "__main__":
    unittest.main()
