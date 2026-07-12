"""Unit tests for PipelineRun generateName prefix builder."""

from __future__ import annotations

import unittest

from suite.pipelinerun_naming import (
    build_diagnostic_artifact_log_name,
    build_olminstall_generate_prefix,
    cluster_segment_for_name,
    compact_version_for_name,
    diagnostic_version_segment,
    gates_segment_for_name,
)

class TestPipelinerunNaming(unittest.TestCase):
    def test_compact_version_short(self) -> None:
        self.assertEqual(compact_version_for_name("3.5"), "3.5")
        self.assertEqual(compact_version_for_name("rhoai-v3-5-ea-2"), "3.5ea2")

    def test_compact_version_placeholder(self) -> None:
        self.assertEqual(compact_version_for_name("unspecified (default)"), "")
        self.assertEqual(compact_version_for_name("latest (default)"), "")
        self.assertEqual(compact_version_for_name("n/a"), "")

    def test_gates_segment(self) -> None:
        self.assertEqual(gates_segment_for_name("bvt,smoke"), "bvt-smoke")
        self.assertEqual(gates_segment_for_name("bvt,smoke,tier1"), "bvt-smoke-tier1")

    def test_full_rhoai_eaas(self) -> None:
        prefix = build_olminstall_generate_prefix(
            product="rhoai",
            version="rhoai-v3-5-ea-2",
            cluster_source="EAAS",
            target_type="eaas",
            tests_csv="bvt,smoke",
            run_owner="nmanos@redhat.com",
        )
        self.assertEqual(prefix, "olminstall-cli-nmanos-rhoai-3.5ea2-eaas-bvt-smoke-")

    def test_existing_omits_product(self) -> None:
        prefix = build_olminstall_generate_prefix(
            product="existing",
            tests_csv="bvt,smoke",
            run_owner="nmanos",
        )
        self.assertEqual(prefix, "olminstall-cli-nmanos-bvt-smoke-")

    def test_odh_without_version(self) -> None:
        prefix = build_olminstall_generate_prefix(
            product="odh",
            version="n/a",
            cluster_source="EAAS",
            target_type="eaas",
            tests_csv="bvt,smoke",
            run_owner="jdoe",
        )
        self.assertEqual(prefix, "olminstall-cli-jdoe-odh-eaas-bvt-smoke-")

    def test_external_cluster_label(self) -> None:
        prefix = build_olminstall_generate_prefix(
            product="rhoai",
            version="3.5",
            cluster_source="my-kubeconfig-secret",
            cluster_label="ods-qe-psi-09",
            target_type="external",
            tests_csv="bvt",
            run_owner="alice",
        )
        self.assertEqual(prefix, "olminstall-cli-alice-rhoai-3.5-ods-qe-psi-09-bvt-")

    def test_existing_external_cluster_label(self) -> None:
        prefix = build_olminstall_generate_prefix(
            product="existing",
            cluster_source="olminstall-kubeconfig-nmanos",
            cluster_label="nmanos-konflux1",
            target_type="external",
            tests_csv="smoke",
            run_owner="nmanos",
        )
        self.assertEqual(prefix, "olminstall-cli-nmanos-nmanos-konflux1-smoke-")

    def test_cluster_segment_skips_auto_kubeconfig_secret_name(self) -> None:
        self.assertEqual(
            cluster_segment_for_name(
                cluster_source="olminstall-kubeconfig-nmanos",
                cluster_label="",
                target_type="external",
            ),
            "",
        )
        self.assertEqual(
            cluster_segment_for_name(
                cluster_source="nmanos-konflux1",
                cluster_label="",
                target_type="external",
            ),
            "nmanos-konflux1",
        )

    def test_missing_version_and_cluster(self) -> None:
        prefix = build_olminstall_generate_prefix(
            product="rhoai",
            version="unspecified (default)",
            tests_csv="bvt",
            run_owner="bob",
        )
        self.assertEqual(prefix, "olminstall-cli-bob-rhoai-bvt-")

    def test_rhoai_version_and_cluster_keeps_descriptive_name(self) -> None:
        prefix = build_olminstall_generate_prefix(
            product="rhoai",
            version="3.5",
            cluster_label="nmanos-konflux",
            cluster_source="olminstall-kubeconfig-nmanos-konflux-nmanos",
            target_type="external",
            tests_csv="bvt,smoke",
            run_owner="nmanos",
        )
        self.assertEqual(
            prefix,
            "olminstall-cli-nmanos-rhoai-3.5-nmanos-konflux-bvt-smoke-",
        )

    def test_overflow_drops_version_before_cluster(self) -> None:
        prefix = build_olminstall_generate_prefix(
            product="rhoai",
            version="rhoai-v3-5-ea-2",
            cluster_label="rh-nightly-pm",
            target_type="external",
            tests_csv="bvt,smoke,tier1",
            run_owner="nmanos",
        )
        self.assertIn("rh-nightly-pm", prefix)
        self.assertIn("rhoai", prefix)
        self.assertNotIn("3.5ea2", prefix)
        self.assertNotEqual(prefix, "olminstall-cli-nmanos-bvt-smoke-tier1-")

    def test_diagnostic_artifact_log_name_existing_cluster(self) -> None:
        name = build_diagnostic_artifact_log_name(
            since_time="2026-06-24T11:25:10Z",
            installed_product="rhoai",
            operator_version="2.4.1",
            cluster_label="ods-qe-psi-07",
            pipeline_product="existing",
        )
        self.assertEqual(name, "rhoai-2.4.1-ods-qe-psi-07-diagnostic-2026-06-24T112510Z.log")

    def test_diagnostic_artifact_log_name_trigger_version(self) -> None:
        name = build_diagnostic_artifact_log_name(
            since_time="2026-06-24T11:25:10Z",
            installed_product="rhoai",
            operator_version="rhoai-v3-5-ea-2",
            cluster_label="ods-qe-psi-07",
            pipeline_product="existing",
        )
        self.assertEqual(name, "rhoai-3.5ea2-ods-qe-psi-07-diagnostic-2026-06-24T112510Z.log")

    def test_diagnostic_version_segment_semver(self) -> None:
        self.assertEqual(diagnostic_version_segment("2.4.1"), "2.4.1")
        self.assertEqual(diagnostic_version_segment("rhoai-v3-5-ea-2"), "3.5ea2")

