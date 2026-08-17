#!/usr/bin/env python3
"""
Tekton step: read TESTS / COMPONENTS params + YAML catalogs, write RUN_* result files.

Invoked from parse-pipeline-tests after SCRIPTS_REPO is cloned to REPO_ROOT (e.g. /tmp/repo).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from _bootstrap import ensure_olminstall_path

ensure_olminstall_path()

from suite.component_catalog import (
    load_components_smoke_catalog,
    merged_setup_dependencies_args,
    resolve_shift_left_env_secret,
)
from suite.component_smoke_results import component_smoke_result_name
from suite.constants import DEFAULT_SETUP_DEPENDENCIES_ARGS
from suite.component_plan import parse_components_selection, resolve_components_csv
from install.dsc_install import components_need_models_as_service
from suite.errors import AppError
from suite.tests_config import compute_pipeline_result_flags, load_tests_catalog
from suite.its_trigger_params import CLUSTER_SOURCE_EAAS
from suite.tests_plan import parse_tests_selection, validate_and_normalize_tests_csv

_DISTRIBUTED_WORKLOADS_COMPONENTS = frozenset({"trainer", "distributed_workloads"})

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

# Extra Tekton results (not driven by olminstall-tests-config.yaml phase entries).
EXTRA_RESULT_KEYS = (
    "RUN_OPENDATAHUB_TESTS",
    "RUN_MINIMAL_DEPS",
    "RUN_INSTALL_DEP_OPERATORS",
    "RUN_COMPONENT_TESTS",
    "RUN_COMPONENT_CLUSTER_PREP_IN_DEP_OPERATORS",
    "RUN_BVT_PLACEHOLDER_ONLY",
    "RUN_DISTRIBUTED_WORKLOADS_TESTS",
)


def _snapshot_only_no_cluster(*, product: str, cluster_source: str) -> bool:
    """True when PRODUCT=existing with no external kubeconfig (placeholder BVT / no smoke)."""
    prod = (product or "").strip().lower()
    source = (cluster_source or "").strip()
    if prod not in ("", "existing"):
        return False
    return source in ("", CLUSTER_SOURCE_EAAS)

DEFAULT_SMOKE_AWS_SECRET = "unused-smoke-aws-secret"


def _write_component_smoke_results(
    *,
    catalog_component_ids: tuple[str, ...],
    selected_ids: frozenset[str],
    run_component_tests: bool,
    results_base: Path,
) -> int:
    """Write RUN_SMOKE_<id> true/false for each catalog component."""
    for cid in catalog_component_ids:
        key = component_smoke_result_name(cid)
        path_var = f"{key}_PATH"
        p = os.environ.get(path_var, "").strip()
        if not p:
            continue
        result_path = Path(p).resolve()
        if not result_path.is_relative_to(results_base):
            print(
                f"ERROR: {path_var}={p!r} resolves outside allowed results directory {results_base}",
                file=sys.stderr,
            )
            return 1
        selected = run_component_tests and cid in selected_ids
        try:
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text("true" if selected else "false", encoding="utf-8")
        except (FileNotFoundError, PermissionError, OSError) as exc:
            print(f"ERROR: could not write result file {path_var}={p!r}: {exc}", file=sys.stderr)
            return 1
    return 0


def main() -> int:
    tests_raw = os.environ.get("TEST_GATES", os.environ.get("TESTS", "")).strip()
    components_raw = os.environ.get("COMPONENTS", "").strip()
    repo_root = os.environ.get("REPO_ROOT", "").strip()
    if not repo_root:
        print("REPO_ROOT is required (clone destination of SCRIPTS_REPO).", file=sys.stderr)
        return 1
    root = Path(repo_root)
    cfg = root / "integration-tests" / "olminstall" / "config" / "olminstall-tests-config.yaml"
    comp_cfg = root / "integration-tests" / "olminstall" / "config" / "olminstall-components-smoke.yaml"
    smoke_aws_secret = DEFAULT_SMOKE_AWS_SECRET

    try:
        catalog = load_tests_catalog(cfg)
        csv = validate_and_normalize_tests_csv(tests_raw if tests_raw else None, catalog)
        selected = parse_tests_selection(csv, catalog)
        flags = compute_pipeline_result_flags(selected, catalog)

        components_csv = ""
        setup_deps_args = ""
        needs_smoke_maas_deps = False
        product = os.environ.get("PRODUCT", "existing").strip().lower()
        cluster_source = os.environ.get("CLUSTER_SOURCE", "").strip()
        snapshot_only = _snapshot_only_no_cluster(product=product, cluster_source=cluster_source)
        installs_product = product not in ("", "existing")
        install_dependencies = os.environ.get("INSTALL_DEPENDENCIES", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        comp_catalog = load_components_smoke_catalog(comp_cfg)
        selected_component_ids: frozenset[str] = frozenset()

        if selected & {"smoke", "tier1"}:
            components_csv = resolve_components_csv(
                components_raw if components_raw else None,
                tests_catalog=catalog,
                tests_selected=selected,
                components_catalog=comp_catalog,
            )
            selected_component_ids = parse_components_selection(components_csv, comp_catalog)
            needs_smoke_maas_deps = components_need_models_as_service(selected_component_ids)
            if installs_product:
                merged = merged_setup_dependencies_args(selected_component_ids, comp_catalog)
                setup_deps_args = merged if merged else DEFAULT_SETUP_DEPENDENCIES_ARGS
            elif install_dependencies:
                merged = merged_setup_dependencies_args(selected_component_ids, comp_catalog)
                setup_deps_args = merged if merged else (
                    DEFAULT_SETUP_DEPENDENCIES_ARGS if needs_smoke_maas_deps else ""
                )
            elif needs_smoke_maas_deps and (selected & {"smoke", "tier1"}):
                merged = merged_setup_dependencies_args(selected_component_ids, comp_catalog)
                setup_deps_args = merged if merged else DEFAULT_SETUP_DEPENDENCIES_ARGS
        elif installs_product:
            setup_deps_args = DEFAULT_SETUP_DEPENDENCIES_ARGS

        run_component_tests = flags.get("RUN_SMOKE", False) or flags.get("RUN_TIER1", False)
        if snapshot_only:
            if run_component_tests:
                print(
                    "INFO snapshot-only (PRODUCT=existing, no CLUSTER_SOURCE): "
                    "smoke/tier1 disabled — pass --external-kubeconfig for component tests",
                    flush=True,
                )
            run_component_tests = False
            flags["RUN_SMOKE"] = False
            flags["RUN_TIER1"] = False
            setup_deps_args = ""
            components_csv = ""
            selected_component_ids = frozenset()
            needs_smoke_maas_deps = False

        run_dep_operators = installs_product or (
            run_component_tests and (install_dependencies or needs_smoke_maas_deps)
        )
        flags["RUN_MINIMAL_DEPS"] = run_dep_operators
        flags["RUN_INSTALL_DEP_OPERATORS"] = run_dep_operators

        flags["RUN_COMPONENT_CLUSTER_PREP_IN_DEP_OPERATORS"] = (
            run_component_tests
            and (install_dependencies or needs_smoke_maas_deps)
            and not installs_product
        )
        flags["RUN_COMPONENT_TESTS"] = run_component_tests
        flags["RUN_OPENDATAHUB_TESTS"] = flags.get("RUN_BVT", False) or run_component_tests
        flags["RUN_BVT_PLACEHOLDER_ONLY"] = snapshot_only and bool(flags.get("RUN_BVT", False))
        flags["RUN_DISTRIBUTED_WORKLOADS_TESTS"] = run_component_tests and bool(
            selected_component_ids & _DISTRIBUTED_WORKLOADS_COMPONENTS
        )

        smoke_aws_secret = resolve_shift_left_env_secret(
            comp_catalog,
            selected_ids=selected_component_ids,
            explicit="",
        ) or DEFAULT_SMOKE_AWS_SECRET

    except AppError as exc:
        print(
            f"ERROR: tests config or selection failed (fix YAML/CSV or paths): {exc}",
            file=sys.stderr,
        )
        return 1
    except FileNotFoundError as exc:
        print(
            f"ERROR: file not found — verify REPO_ROOT={repo_root!r} and that the repo contains "
            f"integration-tests/olminstall/config/*.yaml: {exc}",
            file=sys.stderr,
        )
        return 1
    except PermissionError as exc:
        print(
            f"ERROR: permission denied reading config under REPO_ROOT={repo_root!r}: {exc}",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        if yaml is not None and isinstance(exc, yaml.YAMLError):
            print(
                f"ERROR: invalid YAML in tests config ({cfg}): {exc}. Fix indentation/quoting in the file.",
                file=sys.stderr,
            )
            return 1
        raise

    print(
        f"TEST_GATES={csv!r} COMPONENTS={components_csv!r} selection={sorted(selected)} -> {flags}",
        flush=True,
    )
    results_base = Path(os.environ.get("RESULTS_DIR", "/tekton/results")).resolve()
    all_keys = set(flags) | set(EXTRA_RESULT_KEYS)
    for key in sorted(all_keys):
        val = flags.get(key, False)
        path_var = f"{key}_PATH"
        p = os.environ.get(path_var, "").strip()
        if not p:
            print(f"Missing env {path_var} for result {key}", file=sys.stderr)
            return 1
        result_path = Path(p).resolve()
        if not result_path.is_relative_to(results_base):
            print(
                f"ERROR: {path_var}={p!r} resolves outside allowed results directory {results_base}",
                file=sys.stderr,
            )
            return 1
        try:
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text("true" if val else "false", encoding="utf-8")
        except (FileNotFoundError, PermissionError, OSError) as exc:
            print(
                f"ERROR: could not write result file {path_var}={p!r}: {exc}",
                file=sys.stderr,
            )
            return 1

    if _write_component_smoke_results(
        catalog_component_ids=(),
        selected_ids=selected_component_ids,
        run_component_tests=bool(flags.get("RUN_COMPONENT_TESTS")),
        results_base=results_base,
    ):
        return 1

    components_path = os.environ.get("COMPONENTS_CSV_PATH", "").strip()
    workspace_csv = os.environ.get("COMPONENTS_CSV_WORKSPACE", "").strip()
    if not workspace_csv:
        print("Missing env COMPONENTS_CSV_WORKSPACE for parse run-config", file=sys.stderr)
        return 1
    ws_path = Path(workspace_csv)
    ws_path.parent.mkdir(parents=True, exist_ok=True)
    ws_path.write_text(components_csv, encoding="utf-8")
    if components_path:
        print(
            "INFO: COMPONENTS_CSV written to workspace only; emit-parse-artifacts publishes Tekton result",
            flush=True,
        )

    setup_deps_path = os.environ.get("SETUP_DEPENDENCIES_ARGS_PATH", "").strip()
    workspace_setup = os.environ.get("SETUP_DEPENDENCIES_ARGS_WORKSPACE", "").strip()
    if not workspace_setup:
        print("Missing env SETUP_DEPENDENCIES_ARGS_WORKSPACE for parse run-config", file=sys.stderr)
        return 1
    ws_path = Path(workspace_setup)
    ws_path.parent.mkdir(parents=True, exist_ok=True)
    ws_path.write_text(setup_deps_args, encoding="utf-8")
    if setup_deps_path:
        print(
            "INFO: SETUP_DEPENDENCIES_ARGS written to workspace only; emit-parse-artifacts publishes Tekton result",
            flush=True,
        )

    smoke_aws_path = os.environ.get("SMOKE_AWS_SECRET_PATH", "").strip()
    workspace_smoke_aws = os.environ.get("SMOKE_AWS_SECRET_WORKSPACE", "").strip()
    if flags.get("RUN_COMPONENT_TESTS") and not workspace_smoke_aws:
        print("Missing env SMOKE_AWS_SECRET_WORKSPACE for parse run-config", file=sys.stderr)
        return 1
    if workspace_smoke_aws:
        ws_path = Path(workspace_smoke_aws)
        ws_path.parent.mkdir(parents=True, exist_ok=True)
        ws_path.write_text(smoke_aws_secret, encoding="utf-8")
    if smoke_aws_path:
        print(
            "INFO: SMOKE_AWS_SECRET written to workspace only; emit-parse-artifacts publishes Tekton result",
            flush=True,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
