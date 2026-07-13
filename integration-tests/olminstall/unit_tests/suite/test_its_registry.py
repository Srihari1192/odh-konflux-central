"""Tests for ITS manifest registry (no cluster)."""

from __future__ import annotations

from pathlib import Path

import pytest

from suite.errors import AppError
from suite.its_registry import (
    integration_test_scenario_application,
    integration_test_scenario_default_konflux_app,
    list_integration_test_scenario_manifests,
    looks_like_its_manifest_path,
    resolve_integration_test_scenario_manifest,
    resolve_integration_test_scenario_manifest_path,
    resolve_integration_test_scenario_ref,
    resolve_integration_test_scenario_run_its_snapshot,
    validate_integration_test_scenario_name,
)

_ROOT = Path(__file__).resolve().parents[2]
_RH_NIGHTLY_REL = "integration-tests/olminstall/tekton/its/its-olminstall-testops-rh-nightly.yaml"
_RH_NIGHTLY_SHORT = "tekton/its/its-olminstall-testops-rh-nightly.yaml"


def test_validate_integration_test_scenario_name_ok() -> None:
    assert validate_integration_test_scenario_name("odh-olminstall-testops-eaas") == (
        "odh-olminstall-testops-eaas"
    )


def test_validate_integration_test_scenario_name_rejects_empty() -> None:
    with pytest.raises(AppError, match="non-empty"):
        validate_integration_test_scenario_name("  ")


def test_looks_like_its_manifest_path() -> None:
    assert looks_like_its_manifest_path(_RH_NIGHTLY_REL)
    assert looks_like_its_manifest_path(_RH_NIGHTLY_SHORT)
    assert not looks_like_its_manifest_path("odh-olminstall-testops-rh-nightly")


def test_resolve_eaas_manifest() -> None:
    path = resolve_integration_test_scenario_manifest(_ROOT, "odh-olminstall-testops-eaas")
    assert path.name == "its-olminstall-testops-eaas.yaml"
    assert integration_test_scenario_application(path) == "testops-playpen"


def test_resolve_rh_nightly_manifest() -> None:
    path = resolve_integration_test_scenario_manifest(
        _ROOT,
        "odh-olminstall-testops-rh-nightly",
    )
    assert path.name == "its-olminstall-testops-rh-nightly.yaml"
    assert integration_test_scenario_application(path) == "rhoai-fbc-fragment-ocp-420"


def test_resolve_manifest_path_repo_relative() -> None:
    path = resolve_integration_test_scenario_manifest_path(_ROOT, _RH_NIGHTLY_REL)
    assert path.name == "its-olminstall-testops-rh-nightly.yaml"


def test_resolve_manifest_path_olminstall_relative() -> None:
    path = resolve_integration_test_scenario_manifest_path(_ROOT, _RH_NIGHTLY_SHORT)
    assert path.name == "its-olminstall-testops-rh-nightly.yaml"


def test_resolve_ref_from_path_returns_metadata_name() -> None:
    manifest, name = resolve_integration_test_scenario_ref(_ROOT, _RH_NIGHTLY_SHORT)
    assert manifest.name == "its-olminstall-testops-rh-nightly.yaml"
    assert name == "odh-olminstall-testops-rh-nightly"


def test_resolve_manifest_path_rejects_missing_file() -> None:
    with pytest.raises(AppError, match="ITS manifest not found"):
        resolve_integration_test_scenario_manifest_path(
            _ROOT,
            "integration-tests/olminstall/tekton/its/does-not-exist.yaml",
        )


def test_rh_nightly_default_konflux_app() -> None:
    assert (
        integration_test_scenario_default_konflux_app("odh-olminstall-testops-rh-nightly")
        == "rhoai-fbc-fragment-ocp-420"
    )
    assert integration_test_scenario_default_konflux_app("odh-olminstall-testops-eaas") == ""


def test_resolve_run_its_snapshot_rh_nightly() -> None:
    path = resolve_integration_test_scenario_run_its_snapshot(
        _ROOT,
        "odh-olminstall-testops-rh-nightly",
    )
    assert path is not None
    assert path.name == "test-snapshot-rh-nightly.yaml"


def test_resolve_run_its_snapshot_unsupported_returns_none() -> None:
    assert (
        resolve_integration_test_scenario_run_its_snapshot(_ROOT, "odh-olminstall-testops-eaas")
        is None
    )


def test_its_manifest_param_reads_product() -> None:
    path = _ROOT / "tekton" / "its" / "its-olminstall-testops-rh-nightly.yaml"
    from suite.its_registry import its_manifest_param

    assert its_manifest_param(path, "PRODUCT") == "rhoai"


def test_resolve_manifest_path_absolute_under_repo() -> None:
    abs_path = _ROOT / "tekton" / "its" / "its-olminstall-testops-rh-nightly.yaml"
    path = resolve_integration_test_scenario_manifest_path(_ROOT, str(abs_path))
    assert path == abs_path.resolve()


def test_resolve_manifest_path_absolute_outside_repo_rejected() -> None:
    with pytest.raises(AppError, match="stay under repository root"):
        resolve_integration_test_scenario_manifest_path(_ROOT, "/etc/passwd")


def test_resolve_manifest_path_rejects_escape() -> None:
    with pytest.raises(AppError, match="stay under repository root"):
        resolve_integration_test_scenario_manifest_path(
            _ROOT,
            "integration-tests/olminstall/tekton/its/../../../../../etc/passwd",
        )


def test_resolve_unknown_manifest() -> None:
    with pytest.raises(AppError, match="No in-tree ITS manifest"):
        resolve_integration_test_scenario_manifest(_ROOT, "does-not-exist")


def test_list_manifests_includes_playpen_its() -> None:
    names = list_integration_test_scenario_manifests(_ROOT)
    assert "odh-olminstall-testops-eaas" in names
    assert "odh-olminstall-testops-rh-nightly" in names


def test_eaas_pipelinerun_wrapper_prefix() -> None:
    path = _ROOT / "tekton" / "pipelines" / "olminstall-pipelinerun-eaas.yaml"
    text = path.read_text(encoding="utf-8")
    assert "generateName: olminstall-its-eaas-bvt-smoke-" in text
