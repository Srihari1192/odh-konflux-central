"""Cross-flag validation for ``olm_pipeline.py`` CLI arguments."""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from .cli_parser import CliArgumentParser, _KA_HOST_FROM_ENV
from suite.component_catalog import default_components_smoke_config_path, load_components_smoke_catalog
from suite.component_plan import validate_and_normalize_components_csv
from suite.constants import default_tests_config_path
from suite.errors import AppError
from suite.its_registry import (
    resolve_integration_test_scenario_manifest,
    resolve_integration_test_scenario_run_now_snapshot,
    validate_integration_test_scenario_name,
)
from suite.tests_config import load_tests_catalog
from suite.tests_plan import (
    parse_tests_selection,
    validate_and_normalize_tests_csv_cli,
)

_DURATION_TOKEN_RE = re.compile(r"(?i)(\d+(?:\.\d+)?)([smhd])")


def _normalize_test_timeout(raw: str) -> str:
    """Normalize duration text (e.g. ``10m``, ``1h30m``); return empty for unset."""
    s = (raw or "").strip()
    if not s:
        return ""
    compact = s.replace(" ", "")
    if re.fullmatch(r"\d+(?:\.\d+)?", compact):
        return f"{compact}s"
    pos = 0
    out_parts: list[str] = []
    has_positive = False
    for m in _DURATION_TOKEN_RE.finditer(compact):
        if m.start() != pos:
            raise AppError(
                "--test-timeout must be a duration like 10m, 90s, 1h30m, 1.5h, or plain seconds.",
                2,
            )
        num = m.group(1)
        unit = m.group(2).lower()
        if float(num) > 0:
            has_positive = True
        out_parts.append(f"{num}{unit}")
        pos = m.end()
    if pos != len(compact) or not out_parts:
        raise AppError(
            "--test-timeout must be a duration like 10m, 90s, 1h30m, 1.5h, or plain seconds.",
            2,
        )
    if not has_positive:
        raise AppError("--test-timeout must be greater than zero.", 2)
    return "".join(out_parts)


def parse_cli_args(parser: CliArgumentParser, argv: list[str]) -> argparse.Namespace:
    tests_explicit = any(x == "--tests" or x.startswith("--tests=") for x in argv)
    components_explicit = any(x == "--components" or x.startswith("--components=") for x in argv)
    test_timeout_explicit = any(x == "--test-timeout" or x.startswith("--test-timeout=") for x in argv)
    args = parser.parse_args(argv)

    if args.version and args.product != "rhoai":
        raise AppError("--rhoai-version is supported only with --product rhoai", 2)
    if getattr(args, "install_dependencies", False) and args.product != "existing":
        raise AppError("--install-dependencies is only supported with --product existing", 2)
    if args.ocp_version:
        if not re.fullmatch(r"\d+\.\d+", args.ocp_version.strip()):
            raise AppError("--ocp-version must be MAJOR.MINOR (e.g. 4.20)", 2)
        args.ocp_version = args.ocp_version.strip()
    if args.ka_host == _KA_HOST_FROM_ENV:
        args.ka_host = os.environ.get("KA_HOST", "")
        if not args.ka_host:
            raise AppError(
                "--ka-host with no URL requires KA_HOST in the environment, or pass "
                "--ka-host https://<kubearchive-host> (see README). "
                "Without KubeArchive, only PipelineRuns still on the apiserver are listed.",
                2,
            )
    if args.konflux_ui and not args.konflux_ui.startswith("https://"):
        raise AppError("--konflux-ui must use https://", 2)
    if args.ka_host and not args.ka_host.startswith("https://"):
        raise AppError("--ka-host must use https://", 2)
    if args.konflux_server and not args.konflux_server.startswith("https://"):
        raise AppError("--konflux-server must use https://", 2)

    cfg_arg = (args.tests_config or "").strip()
    cfg_path = Path(cfg_arg).expanduser().resolve() if cfg_arg else default_tests_config_path()
    catalog = load_tests_catalog(cfg_path)
    args.tests_catalog_default_csv = catalog.default_csv
    comp_cfg_path = default_components_smoke_config_path()
    comp_catalog = load_components_smoke_catalog(comp_cfg_path)
    args.components_catalog = comp_catalog
    args.components_catalog_default_csv = comp_catalog.enabled_components_csv

    if tests_explicit or (args.tests or "").strip():
        phases_csv, test_tags, scoped_components, auto_scope_tags = (
            validate_and_normalize_tests_csv_cli(
                args.tests,
                catalog,
                components_catalog=comp_catalog,
            )
        )
        args.tests = phases_csv
        args.test_tags = test_tags
        args.test_slice_scoped_components = scoped_components
        args.test_tags_inferred = auto_scope_tags and bool(test_tags)
    else:
        args.tests = catalog.default_csv
        args.test_tags = ""
        args.test_slice_scoped_components = ()
        args.test_tags_inferred = False
    args.tests_explicit = tests_explicit

    if args.test_tags_inferred and not components_explicit:
        args.components = ",".join(args.test_slice_scoped_components)
        args.components_inferred = True
    else:
        args.components_inferred = False

    selected_tests = parse_tests_selection(args.tests, catalog)
    args.components = validate_and_normalize_components_csv(
        args.components,
        tests_csv=args.tests,
        components_catalog=comp_catalog,
    )
    args.components_explicit = components_explicit
    if args.test_tags:
        selected = {c.strip() for c in args.components.split(",") if c.strip()}
        missing = set(args.test_slice_scoped_components) - selected
        if missing:
            need = ", ".join(sorted(missing))
            raise AppError(
                f"--tests test-slice tokens require component(s): {need} "
                "(pass --components or rely on auto-scope).",
                2,
            )
    if components_explicit and "smoke" not in selected_tests:
        raise AppError("--components is only valid when --tests includes smoke.", 2)
    if getattr(args, "install_dependencies", False) and not (selected_tests & {"smoke", "tier1"}):
        raise AppError("--install-dependencies requires --tests smoke and/or tier1.", 2)
    if getattr(args, "install_dependencies", False):
        if not args.external_kubeconfig and not args.external_kubeconfig_secret:
            raise AppError(
                "--install-dependencies requires --external-kubeconfig or --external-kubeconfig-secret.",
                2,
            )

    args.external_kubeconfig = (args.external_kubeconfig or "").strip()
    args.external_kubeconfig_secret = (args.external_kubeconfig_secret or "").strip()
    if args.external_kubeconfig and args.external_kubeconfig_secret:
        raise AppError(
            "--external-kubeconfig and --external-kubeconfig-secret are mutually exclusive.",
            2,
        )
    if args.external_kubeconfig:
        from k8s.external_kubeconfig import validate_kubeconfig_path

        args.external_kubeconfig_path = validate_kubeconfig_path(args.external_kubeconfig)
    else:
        args.external_kubeconfig_path = None
    if args.external_kubeconfig_secret and not re.fullmatch(
        r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", args.external_kubeconfig_secret
    ):
        raise AppError(
            "--external-kubeconfig-secret must be a valid Kubernetes resource name.",
            2,
        )
    args.test_timeout = _normalize_test_timeout(args.test_timeout)
    args.test_timeout_explicit = test_timeout_explicit
    if args.test_timeout and "smoke" not in selected_tests:
        raise AppError("--test-timeout is only valid when --tests includes smoke.", 2)

    if args.list_pipelines is not None:
        try:
            lp = int(args.list_pipelines)
            if lp <= 0:
                raise ValueError
            args.list_pipelines = lp
        except ValueError as exc:
            raise AppError(f"-l expects a positive integer (got: {args.list_pipelines})", 2) from exc
    else:
        args.list_pipelines = 0

    args.enable_its = (getattr(args, "enable_its", "") or "").strip()
    args.disable_its = (getattr(args, "disable_its", "") or "").strip()
    args.run_now = bool(getattr(args, "run_now", False))
    if args.enable_its and args.disable_its:
        raise AppError("--enable-its and --disable-its are mutually exclusive.", 2)
    if args.run_now and args.disable_its:
        raise AppError("--run-now cannot be used with --disable-its.", 2)
    if args.run_now and not args.enable_its:
        raise AppError("--run-now requires --enable-its NAME.", 2)
    its_admin_on = bool(args.enable_its or args.disable_its)
    if its_admin_on:
        its_name = validate_integration_test_scenario_name(args.enable_its or args.disable_its)
        if args.enable_its:
            olminstall_root = Path(__file__).resolve().parent.parent.parent
            resolve_integration_test_scenario_manifest(olminstall_root, its_name)
            if args.run_now:
                resolve_integration_test_scenario_run_now_snapshot(olminstall_root, its_name)

    list_pipelines_on = bool(args.list_pipelines)
    list_ocp_on = bool(args.list_supported_ocp)
    watch_on = args.watch is not None
    delete_pipelines_on = bool(args.delete_pending_pipelines)
    query_modes = sum(
        [list_pipelines_on, list_ocp_on, watch_on, delete_pipelines_on, its_admin_on]
    )
    if query_modes > 1:
        raise AppError(
            "-l, -w, --delete-pending-pipelines, --list-supported-ocp, "
            "--enable-its, and --disable-its are mutually exclusive (pick one query/maintenance mode).",
            2,
        )

    if (args.external_kubeconfig or args.external_kubeconfig_secret) and args.ocp_version and not list_ocp_on:
        if args.product == "existing":
            raise AppError(
                "--ocp-version with --external-kubeconfig is only for --product rhoai install; "
                "existing runs skip FBC catalog resolution.",
                2,
            )

    def _trigger_options_incompatible_with_query() -> list[str]:
        bad: list[str] = []
        if args.image:
            bad.append("--image")
        if args.version:
            bad.append("--rhoai-version")
        if args.channel:
            bad.append("--channel")
        if args.konflux_repo:
            bad.append("--konflux-repo")
        if args.konflux_branch:
            bad.append("--konflux-branch")
        if args.ocp_version and not list_ocp_on:
            bad.append("--ocp-version")
        if getattr(args, "tests_explicit", False):
            bad.append("--tests")
        if (args.tests_config or "").strip():
            bad.append("--tests-config")
        if getattr(args, "components_explicit", False):
            bad.append("--components")
        if getattr(args, "test_timeout_explicit", False):
            bad.append("--test-timeout")
        if args.external_kubeconfig:
            bad.append("--external-kubeconfig")
        if args.external_kubeconfig_secret:
            bad.append("--external-kubeconfig-secret")
        if getattr(args, "cleanup", False):
            bad.append("--cleanup")
        if getattr(args, "install_dependencies", False):
            bad.append("--install-dependencies")
        if (args.tests_rhoai_version or "").strip():
            bad.append("--tests-rhoai-version")
        if args.slack_channel_id:
            bad.append("--slack-channel-id")
        return bad

    if query_modes and (bad := _trigger_options_incompatible_with_query()):
        if args.enable_its:
            bad = [item for item in bad if item not in ("--konflux-repo", "--konflux-branch")]
        if args.run_now:
            bad = [
                item
                for item in bad
                if item not in ("--image", "--rhoai-version", "--ocp-version", "--channel")
            ]
        if bad:
            joined = ", ".join(bad)
            raise AppError(
                f"Trigger/install options cannot be used with -l, --list-supported-ocp, "
                f"-w, --delete-pending-pipelines, --enable-its, or "
                f"--disable-its: {joined}. "
                "Use only Konflux context flags (e.g. --konflux-namespace, --konflux-app, --ka-host, "
                "--konflux-ui; with --enable-its you may add "
                "--konflux-repo / --konflux-branch) for list/watch/delete/ITS admin; "
                "with --list-supported-ocp you may add --ocp-version to verify it appears in the "
                "supported list.",
                2,
            )

    if getattr(args, "stop_owned_running", False) and not delete_pipelines_on:
        raise AppError("--stop-owned-running requires --delete-pending-pipelines.", 2)
    if getattr(args, "include_unowned_stuck", False) and not delete_pipelines_on:
        raise AppError("--include-unowned-stuck requires --delete-pending-pipelines.", 2)
    if getattr(args, "delete_pending_dry_run", False) and not delete_pipelines_on:
        raise AppError("--delete-pending-dry-run requires --delete-pending-pipelines.", 2)

    if not query_modes:
        if args.cleanup and not args.external_kubeconfig_path and not args.external_kubeconfig_secret:
            raise AppError(
                "--cleanup requires --external-kubeconfig or --external-kubeconfig-secret.",
                2,
            )

    args.watch_mode = watch_on
    return args
