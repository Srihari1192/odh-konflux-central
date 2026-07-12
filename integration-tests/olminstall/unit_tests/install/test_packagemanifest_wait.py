"""Unit tests for OLM PackageManifest wait helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from install import install_and_verify as iav

_PM_JSON = {
    "status": {
        "catalogSource": "rhoai-catalog-dev",
        "channels": [
            {"name": "beta", "currentCSV": "rhods-operator.3.5.0-ea.2"},
            {"name": "stable", "currentCSV": "rhods-operator.2.25.7"},
        ],
    }
}

class PackagemanifestWaitTest(unittest.TestCase):
    def test_packagemanifest_channel_csv_matches_catalog_and_channel(self) -> None:
        proc = patch.object(
            iav,
            "_packagemanifest_doc_for_catalog",
            return_value=_PM_JSON,
        )
        with proc:
            csv = iav.packagemanifest_channel_csv("rhods-operator", "rhoai-catalog-dev", "beta")
        self.assertEqual(csv, "rhods-operator.3.5.0-ea.2")

    def test_packagemanifest_channel_csv_wrong_catalog(self) -> None:
        proc = patch.object(
            iav,
            "_packagemanifest_doc_for_catalog",
            return_value=None,
        )
        with proc:
            csv = iav.packagemanifest_channel_csv("rhods-operator", "other-catalog", "beta")
        self.assertIsNone(csv)

    def test_packagemanifest_doc_for_catalog_prefers_label_selector(self) -> None:
        listed = {
            "items": [
                {
                    "metadata": {"name": "rhods-operator"},
                    "status": {"catalogSource": "rhoai-catalog-dev", "channels": []},
                }
            ]
        }
        with patch.object(
            iav,
            "oc_run",
            return_value=type("R", (), {"returncode": 0, "stdout": json.dumps(listed)})(),
        ):
            doc = iav._packagemanifest_doc_for_catalog("rhods-operator", "rhoai-catalog-dev")
        self.assertEqual((doc or {}).get("status", {}).get("catalogSource"), "rhoai-catalog-dev")

    def test_wait_packagemanifest_ready_succeeds_when_csv_appears(self) -> None:
        with (
            patch.object(iav, "packagemanifest_channel_csv", side_effect=[None, "rhods-operator.3.5.0-ea.2"]),
            patch.object(iav.time, "time", side_effect=[100, 100, 101, 101]),
            patch.object(iav.time, "sleep"),
        ):
            csv = iav.wait_packagemanifest_ready("rhods-operator", "rhoai-catalog-dev", "beta", 200)
        self.assertEqual(csv, "rhods-operator.3.5.0-ea.2")

    def test_patch_oc_wait_csv_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            utils = Path(tmp) / "utils"
            utils.mkdir()
            oc_wait = utils / "oc_wait.sh"
            oc_wait.write_text(
                "oc_wait_for_ip() {\n  for i in {1..10}; do\n    echo wait\n  done\n}\n"
                "oc_wait_for_csv() {\n  for i in {1..60}; do\n    echo csv\n  done\n}\n",
                encoding="utf-8",
            )
            iav.patch_oc_wait_csv_timeout(Path(tmp), retries=120)
            text = oc_wait.read_text(encoding="utf-8")
            self.assertIn("for i in {1..120};", text)
            self.assertIn("for i in {1..10};", text)

    def test_patch_oc_wait_install_plan_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            utils = Path(tmp) / "utils"
            utils.mkdir()
            oc_wait = utils / "oc_wait.sh"
            oc_wait.write_text(
                "oc_wait_for_ip() {\n  for i in {1..10}; do\n    echo wait\n  done\n"
                "oc_wait_for_csv() {\n  for i in {1..10}; do\n    echo csv\n  done\n",
                encoding="utf-8",
            )
            iav.patch_oc_wait_install_plan_timeout(Path(tmp), retries=90)
            text = oc_wait.read_text(encoding="utf-8")
            self.assertIn("for i in {1..90};", text)
            self.assertIn("oc_wait_for_csv() {\n  for i in {1..10};", text)

    def test_patch_manifest_install_plan_automatic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "install-rhods-operator.yaml"
            manifest.write_text(
                "spec:\n  installPlanApproval: Manual\n  channel: beta\n",
                encoding="utf-8",
            )
            iav.patch_manifest_install_plan_automatic(manifest)
            self.assertIn("installPlanApproval: Automatic", manifest.read_text(encoding="utf-8"))

    def test_patch_manifest_channel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "install-rhods-operator.yaml"
            manifest.write_text(
                "spec:\n  channel: fast\n  name: rhods-operator\n",
                encoding="utf-8",
            )
            iav.patch_manifest_channel(manifest, "beta")
            self.assertIn("channel: beta", manifest.read_text(encoding="utf-8"))
            self.assertNotIn("channel: fast", manifest.read_text(encoding="utf-8"))

    def test_patch_manifest_starting_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "install-rhods-operator.yaml"
            manifest.write_text(
                'spec:\n  channel: beta\n  startingCSV: ""\n',
                encoding="utf-8",
            )
            iav.patch_manifest_starting_csv(manifest, "rhods-operator.3.5.0-ea.2")
            self.assertIn('startingCSV: "rhods-operator.3.5.0-ea.2"', manifest.read_text(encoding="utf-8"))

    def test_subscription_bundle_unpack_in_progress(self) -> None:
        sub = {"status": {"conditions": [{"type": "BundleUnpacking", "status": "True"}]}}
        with patch.object(
            iav,
            "oc_run",
            return_value=type("R", (), {"returncode": 0, "stdout": json.dumps(sub)})(),
        ):
            self.assertTrue(iav.subscription_bundle_unpack_in_progress("rhods-operator", "redhat-ods-operator"))

    def test_wait_subscription_bundle_unpacked_when_not_unpacking(self) -> None:
        sub = {"status": {"conditions": [{"type": "CatalogSourcesUnhealthy", "status": "False"}]}}
        with patch.object(
            iav,
            "oc_run",
            return_value=type("R", (), {"returncode": 0, "stdout": json.dumps(sub)})(),
        ):
            self.assertTrue(iav.wait_subscription_bundle_unpacked("rhods-operator", "redhat-ods-operator", 100.0))

class IdmsMirrorTest(unittest.TestCase):
    def test_idms_has_rhoai_mirror(self) -> None:
        self.assertTrue(
            iav.idms_has_rhoai_mirror(
                {"imageDigestMirrors": [{"source": iav.RHOAI_IDMS_SOURCE, "mirrors": [iav.RHOAI_IDMS_MIRROR]}]}
            )
        )
        self.assertFalse(iav.idms_has_rhoai_mirror({"imageDigestMirrors": []}))

    def test_ensure_rhoai_idms_mirror_skips_when_present(self) -> None:
        listed = {
            "items": [
                {
                    "spec": {
                        "imageDigestMirrors": [
                            {"source": iav.RHOAI_IDMS_SOURCE, "mirrors": [iav.RHOAI_IDMS_MIRROR]}
                        ]
                    }
                }
            ]
        }
        with patch.object(iav, "oc_run", return_value=type("R", (), {"returncode": 0, "stdout": json.dumps(listed)})()):
            iav.ensure_rhoai_idms_mirror()

    def test_ensure_rhoai_idms_mirror_patches_cluster(self) -> None:
        calls: list[list[str]] = []

        def fake_oc(args: list[str], **kwargs: object) -> object:
            calls.append(args)
            if args[:3] == ["get", "imagedigestmirrorset", "-o"]:
                return type("R", (), {"returncode": 0, "stdout": json.dumps({"items": []})})()
            if args[:3] == ["get", "imagedigestmirrorset", "cluster"]:
                return type(
                    "R",
                    (),
                    {"returncode": 0, "stdout": json.dumps({"spec": {"imageDigestMirrors": []}})},
                )()
            return type("R", (), {"returncode": 0, "stdout": ""})()

        with patch.object(iav, "oc_run", side_effect=fake_oc):
            iav.ensure_rhoai_idms_mirror()
        self.assertTrue(any(c[:2] == ["patch", "imagedigestmirrorset"] for c in calls))

