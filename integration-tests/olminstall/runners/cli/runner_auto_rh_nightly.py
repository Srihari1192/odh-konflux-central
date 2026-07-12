"""Rh-nightly catalog sync: poll Konflux and create Snapshot when OCP-matched digest changes."""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from k8s.cluster_ocp_version import cluster_ocp_minor_from_kubeconfig
from k8s.external_credentials import (
    refresh_working_kubeconfig_from_credentials,
    update_external_kubeconfig_secret,
)
from k8s.external_kubeconfig import wait_for_external_cluster_idle
from k8s.oc_util import filter_warning_lines, parse_json_output, run_cmd
from runners.report.pipelinerun_metadata import (
    build_manual_snapshot_trigger_labels,
    build_trigger_annotations,
)
from suite.constants import (
    ANNOTATION_TRIGGER_TYPE,
    DEFAULT_UPSTREAM_KONFLUX_GIT,
    OLMINSTALL_RH_NIGHTLY_ITS_NAME,
    RHOAI_FBCF_IMAGE_REF_PATTERN,
    TRIGGER_TYPE_RH_NIGHTLY_AUTO,
)
from suite.errors import AppError
from suite.its_registry import resolve_integration_test_scenario_manifest
from suite.rhoai_fbc_ocp import rhoai_fbc_name_from_ocp_minor
from suite.rh_nightly_auto_trigger import (
    build_auto_trigger_snapshot_yaml,
    decide_auto_trigger,
    load_last_triggered_state,
    record_auto_trigger_success,
    save_last_triggered_state,
)
from .runner_mixin_its import RunnerItsAdminMixin
from .runner_support import format_olm_pipeline_watch_cli, spin_while


class RunnerAutoRhNightlyMixin:
    def _materialize_external_kubeconfig_from_secret(self, secret_name: str) -> tuple[Path, Path]:
        proc = run_cmd(
            [
                "oc",
                "get",
                "secret",
                secret_name,
                "-n",
                self.args.namespace,
                "-o",
                "jsonpath={.data.kubeconfig}",
            ],
            capture=True,
            check=False,
        )
        if proc.returncode != 0 or not (proc.stdout or "").strip():
            raise AppError(f"Cannot read kubeconfig from secret {secret_name!r} in {self.args.namespace}")
        import base64

        raw = base64.b64decode(proc.stdout.strip())
        tmp = Path(tempfile.mkdtemp(prefix="olminstall-auto-rh-nightly-"))
        path = tmp / "kubeconfig"
        path.write_bytes(raw)
        path.chmod(0o600)

        try:
            if refresh_working_kubeconfig_from_credentials(
                namespace=self.args.namespace,
                cluster_source=secret_name,
                bootstrap_path=path,
                work_path=path,
            ):
                print(f"Refreshed external kubeconfig from htpasswd credentials for {secret_name!r}")
                update_external_kubeconfig_secret(
                    namespace=self.args.namespace,
                    secret_name=secret_name,
                    kubeconfig_path=str(path),
                )
        except AppError as exc:
            raise AppError(f"Could not refresh kubeconfig for {secret_name!r}: {exc}") from exc
        return path, tmp

    @staticmethod
    def _cleanup_materialized_kubeconfig(tmp_dir: Path) -> None:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    def _component_last_promoted_image(self, component_name: str) -> str:
        proc = run_cmd(
            [
                "oc",
                "get",
                "component",
                component_name,
                "-n",
                self.args.namespace,
                "-o",
                "jsonpath={.status.lastPromotedImage}",
            ],
            capture=True,
            check=False,
            timeout=60,
        )
        if proc.returncode == 0:
            img = (proc.stdout or "").strip()
            if img and re.search(RHOAI_FBCF_IMAGE_REF_PATTERN, img):
                return img
        return ""

    def _latest_v35_release_snapshot_ts(self) -> str:
        """Audit metadata only; auto-trigger uses OCP-matched catalog digest, not v3-5 snapshot cadence."""
        best = ""
        for app in ("rhoai-v3-5-ea-2", "rhoai-v3-5"):
            proc = run_cmd(
                [
                    "oc",
                    "get",
                    "snapshots",
                    "-n",
                    self.args.namespace,
                    "-l",
                    f"appstudio.openshift.io/application={app}",
                    "--sort-by=.metadata.creationTimestamp",
                    "-o",
                    "jsonpath={.items[-1].metadata.creationTimestamp}",
                ],
                capture=True,
                check=False,
                timeout=120,
            )
            if proc.returncode == 0:
                ts = (proc.stdout or "").strip()
                if ts > best:
                    best = ts
        return best

    def _resolve_auto_trigger_fbc_image(self, fbc_component: str) -> tuple[str, dict[str, Any] | None]:
        promoted = self._component_last_promoted_image(fbc_component)
        if promoted:
            return promoted, None
        ts, img, meta = self.latest_named_component_image(
            self.args.namespace,
            "rhoai-v3-5-ea-2",
            fbc_component,
            RHOAI_FBCF_IMAGE_REF_PATTERN,
        )
        if img:
            return img, meta
        for app in ("rhoai-v3-5", fbc_component):
            ts2, img2, meta2 = self.latest_named_component_image(
                self.args.namespace,
                app,
                fbc_component,
                RHOAI_FBCF_IMAGE_REF_PATTERN,
            )
            if img2 and (not img or ts2 > ts):
                img, meta = img2, meta2
        return img or "", meta

    def _apply_snapshot_labels(self, snapshot_name: str, labels: dict[str, str]) -> None:
        if not labels:
            return
        cmd = ["oc", "label", "snapshot", snapshot_name, "-n", self.args.namespace, "--overwrite"]
        for key, val in labels.items():
            cmd.append(f"{key}={val}")
        proc = run_cmd(cmd, capture=True, check=False)
        if proc.returncode != 0:
            detail = filter_warning_lines(f"{proc.stdout or ''}\n{proc.stderr or ''}").strip()
            raise AppError(f"Failed to label snapshot/{snapshot_name}: {detail or proc.returncode}")

    def _apply_snapshot_annotations(self, snapshot_name: str, annotations: dict[str, str]) -> None:
        if not annotations:
            return
        cmd = ["oc", "annotate", "snapshot", snapshot_name, "-n", self.args.namespace, "--overwrite"]
        for key, val in annotations.items():
            cmd.append(f"{key}={val}")
        proc = run_cmd(cmd, capture=True, check=False)
        if proc.returncode != 0:
            detail = filter_warning_lines(f"{proc.stdout or ''}\n{proc.stderr or ''}").strip()
            raise AppError(f"Failed to annotate snapshot/{snapshot_name}: {detail or proc.returncode}")

    def sync_rh_nightly_catalog(self, *, skip_its_apply: bool = False, wait_for_run: bool = True) -> int:
        self._apply_konflux_git_inference_from_clone_or_env()
        manifest = resolve_integration_test_scenario_manifest(self.script_dir, OLMINSTALL_RH_NIGHTLY_ITS_NAME)
        cluster_secret = RunnerItsAdminMixin._its_manifest_param(manifest, "CLUSTER_SOURCE")
        if not cluster_secret:
            raise AppError(f"{OLMINSTALL_RH_NIGHTLY_ITS_NAME} manifest missing CLUSTER_SOURCE param", 1)

        kubeconfig, kubeconfig_tmp = self._materialize_external_kubeconfig_from_secret(cluster_secret)
        try:
            ocp_minor = cluster_ocp_minor_from_kubeconfig(kubeconfig)
            if not ocp_minor:
                raise AppError(f"Could not detect OCP minor from secret {cluster_secret!r}")

            fbc_component = rhoai_fbc_name_from_ocp_minor(ocp_minor)
            cluster_id = self._cluster_label_for_external_secret(cluster_secret)
            fbc_image, fbc_meta = self._resolve_auto_trigger_fbc_image(fbc_component)
            v35_ts = self._latest_v35_release_snapshot_ts()

            min_rhoai = RunnerItsAdminMixin._its_manifest_param(manifest, "MIN_RHOAI_VERSION") or "3.5"
            state = load_last_triggered_state()
            decision = decide_auto_trigger(
                cluster_id=cluster_id,
                fbc_component=fbc_component,
                fbc_image=fbc_image,
                min_rhoai=min_rhoai,
                state=state,
            )
            print(
                f"rh-nightly catalog sync cluster={cluster_id} ocp={ocp_minor} component={fbc_component} "
                f"v35_snapshot_ts={v35_ts or 'n/a'}"
            )
            if decision.action != "trigger":
                print(f"rh-nightly catalog sync skipped: {decision.reason}")
                return 0

            wait_for_external_cluster_idle(
                namespace=self.args.namespace,
                cluster_source=cluster_secret,
                cluster_id=cluster_id,
                force=bool(getattr(self.args, "force_cluster_run", False)),
            )

            if not skip_its_apply:
                self._apply_integration_test_scenario(
                    OLMINSTALL_RH_NIGHTLY_ITS_NAME,
                    param_overrides={
                        "OCP_VERSION": ocp_minor,
                        "RHOAI_FBC_NAME": fbc_component,
                    },
                )

            git_url = self.args.konflux_repo or DEFAULT_UPSTREAM_KONFLUX_GIT
            git_rev = self.args.konflux_branch or "main"
            snap_yaml = build_auto_trigger_snapshot_yaml(
                application=self.args.app,
                fbc_component=fbc_component,
                fbc_image=fbc_image,
                git_url=git_url,
                git_revision=git_rev,
            )
            with spin_while(f"Creating rh-nightly catalog Snapshot on {self.args.app}"):
                proc = run_cmd(
                    ["oc", "create", "-n", self.args.namespace, "-f", "-", "-o", "jsonpath={.metadata.name}"],
                    capture=True,
                    check=True,
                    input_text=snap_yaml,
                )
                snapshot_name = (proc.stdout or "").strip()
            if not snapshot_name:
                raise AppError("rh-nightly catalog sync Snapshot create returned empty name")

            labels = build_manual_snapshot_trigger_labels(
                application=self.args.app,
                run_owner=self.run_owner,
                product="rhoai",
                target_type="external",
                cluster=cluster_id,
                fbcf_image=fbc_image,
                scripts_git_url=git_url,
                scripts_git_revision=git_rev,
                upstream_git_url=DEFAULT_UPSTREAM_KONFLUX_GIT,
                fbc_snapshot_meta=fbc_meta,
                local_git_repo=self.script_dir.parent.parent,
            )
            annotations = build_trigger_annotations(product="rhoai", tests="bvt,smoke", cluster=cluster_id)
            annotations[ANNOTATION_TRIGGER_TYPE] = TRIGGER_TYPE_RH_NIGHTLY_AUTO
            annotations["olminstall.run-owner"] = self.run_owner
            self._apply_snapshot_labels(snapshot_name, labels)
            self._apply_snapshot_annotations(snapshot_name, annotations)

            print(f"rh-nightly catalog sync Snapshot: {snapshot_name}")
            print(f"  image: {fbc_image}")
            link = f"{self.konflux_ui}/ns/{self.args.namespace}/applications/{self.args.app}/activity/pipelineruns"
            print(f"  Konflux UI: {link}")

            self.snapshot_name = snapshot_name
            snap_rec = parse_json_output(
                ["oc", "get", "snapshot", snapshot_name, "-n", self.args.namespace, "-o", "json"]
            )
            self._trigger_snapshot_spec = snap_rec.get("spec") if isinstance(snap_rec, dict) else None
            self._trigger_snapshot_created_ts = (
                (snap_rec.get("metadata") or {}).get("creationTimestamp", "") if isinstance(snap_rec, dict) else ""
            )
            if wait_for_run:
                self.wait_for_pipelinerun()

            record_auto_trigger_success(
                cluster_id=cluster_id,
                fbc_component=fbc_component,
                fbc_image=fbc_image,
                snapshot_name=snapshot_name,
                state=state,
                v35_snapshot_ts=v35_ts,
            )
            save_last_triggered_state(state)

            if wait_for_run:
                watch_hint = format_olm_pipeline_watch_cli(
                    olminstall_dir=self.script_dir,
                    namespace=self.args.namespace,
                    app=self.args.app,
                    pipelinerun=self.pr or "",
                )
                print(f"Watch with:\n  {watch_hint}")
            return 0
        finally:
            self._cleanup_materialized_kubeconfig(kubeconfig_tmp)
