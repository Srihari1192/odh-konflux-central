"""Unit tests for --run-its FBC image resolution (no cluster)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runners.cli.cli import make_parser, parse_cli_args
from runners.cli.runner import OLMInstallRunner

_ROOT = Path(__file__).resolve().parents[2]
_RH_NIGHTLY_SNAP = _ROOT / "config" / "test-snapshot-rh-nightly.yaml"
_RH_NIGHTLY_ITS = _ROOT / "tekton" / "its" / "its-olminstall-testops-rh-nightly.yaml"
_ODH_ITS = _ROOT / "tekton" / "its" / "its-olminstall-open-data-hub-tenant.yaml"
_PINNED_420 = (
    "quay.io/rhoai/rhoai-fbc-fragment@sha256:"
    "d9f54f26a526be21e0806a5c36b7d929b5861cffa68bcca57825fb878ecb40a2"
)
_LATEST_420 = (
    "quay.io/rhoai/rhoai-fbc-fragment@sha256:"
    "feedfacefeedfacefeedfacefeedfacefeedfacefeedfacefeedfacefeedface"
)


class RunItsManifestDefaultsTest(unittest.TestCase):
    def _runner(self) -> OLMInstallRunner:
        parser = make_parser()
        args = parse_cli_args(
            parser,
            ["--run-its", "odh-olminstall-testops-rh-nightly"],
        )
        runner = OLMInstallRunner(args)
        runner.snapshot_file = _RH_NIGHTLY_SNAP
        return runner

    def test_run_its_stores_pin_but_does_not_set_image(self) -> None:
        runner = self._runner()
        runner._apply_run_its_manifest_defaults(_RH_NIGHTLY_ITS)
        self.assertEqual(runner.args.product, "rhoai")
        self.assertEqual(runner.args.ocp_version, "4.20")
        self.assertEqual(runner.resolved_rhoai_fbc_name, "rhoai-fbc-fragment-ocp-420")
        self.assertEqual(runner._run_its_pinned_fbcf_image, _PINNED_420)
        self.assertEqual(runner.image, "")

    def test_run_its_keeps_manifest_cluster_source_without_cli_override(self) -> None:
        runner = self._runner()
        runner._apply_run_its_manifest_defaults(_RH_NIGHTLY_ITS)
        self.assertEqual(runner.external_kubeconfig_secret, "olminstall-kubeconfig-rh-nightly-pm")

    def test_run_its_cli_external_kubeconfig_overrides_manifest_cluster_source(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".kubeconfig", delete=False) as tf:
            kubeconfig = tf.name
        runner = self._runner()
        runner.args.external_kubeconfig = kubeconfig
        runner.args.external_kubeconfig_path = Path(kubeconfig)
        runner._apply_run_its_manifest_defaults(_RH_NIGHTLY_ITS)
        self.assertEqual(runner.external_kubeconfig_secret, "")


class RunItsUpdateChannelTest(unittest.TestCase):
    def test_run_its_patch_uses_staged_manifest_update_channel(self) -> None:
        parser = make_parser()
        args = parse_cli_args(
            parser,
            ["--run-its", "tekton/its/its-olminstall-open-data-hub-tenant.yaml"],
        )
        runner = OLMInstallRunner(args)
        runner._stage_its_manifest_tmp(_ODH_ITS, push_context=False)
        runner._patch_its_cli_override_params(runner.its_apply_tmp, odh_overrides=True)
        from k8s.oc_util import run_cmd

        proc = run_cmd(
            [
                "yq",
                "e",
                '(.spec.params[] | select(.name == "UPDATE_CHANNEL") | .value) // ""',
                runner.its_apply_tmp,
            ],
            capture=True,
            check=True,
        )
        self.assertEqual(proc.stdout.strip().strip('"'), "odh-stable")


class ResolveRhoaiFbcLatestForComponentTest(unittest.TestCase):
    def _runner(self) -> OLMInstallRunner:
        parser = make_parser()
        args = parse_cli_args(parser, ["--product", "rhoai"])
        runner = OLMInstallRunner(args)
        runner.resolved_rhoai_fbc_name = "rhoai-fbc-fragment-ocp-420"
        return runner

    def test_picks_highest_version_app_with_newest_snapshot(self) -> None:
        runner = self._runner()

        def fake_latest(
            namespace: str,
            app: str,
            component_name: str,
            image_pattern: str,
        ) -> tuple[str, str, dict | None]:
            del namespace, component_name, image_pattern
            images = {
                "rhoai-v3-4-foo": ("2026-07-01T00:00:00Z", "quay.io/rhoai/rhoai-fbc-fragment@sha256:3400"),
                "rhoai-v3-5-ea-2": ("2026-07-08T00:00:00Z", _LATEST_420),
            }
            ts, img = images.get(app, ("", ""))
            return ts, img, None

        with patch.object(runner, "get_applications", return_value=["rhoai-v3-4-foo", "rhoai-v3-5-ea-2"]):
            with patch.object(runner, "latest_named_component_image", side_effect=fake_latest):
                runner._resolve_rhoai_fbc_latest_for_component("rhoai-fbc-fragment-ocp-420")
        self.assertEqual(runner.image, _LATEST_420)
        self.assertEqual(runner.resolved_app, "rhoai-v3-5-ea-2")

    def test_falls_back_to_snapshot_pin_when_konflux_empty(self) -> None:
        runner = self._runner()
        runner.snapshot_file = _RH_NIGHTLY_SNAP
        runner._run_its_pinned_fbcf_image = _PINNED_420
        with patch.object(runner, "get_applications", return_value=[]):
            runner._resolve_rhoai_fbc_latest_for_component("rhoai-fbc-fragment-ocp-420")
        runner._apply_pinned_fbcf_fallback(reason="test")
        self.assertEqual(runner.image, _PINNED_420)


class RhoaiAppVersionKeyTest(unittest.TestCase):
    def test_parses_major_minor(self) -> None:
        self.assertEqual(
            OLMInstallRunner._rhoai_app_version_key("rhoai-v3-5-ea-2"),
            (3, 5),
        )

    def test_ignores_non_numeric_segments(self) -> None:
        self.assertEqual(
            OLMInstallRunner._rhoai_app_version_key("rhoai-v3-4-foo"),
            (3, 4),
        )
