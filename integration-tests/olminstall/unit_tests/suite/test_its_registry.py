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
_RH_NIGHTLY_REL = "integration-tests/olminstall/tekton/its/its-rhoai-e2e-rh-nightly-pm-ocp420.yaml"
_RH_NIGHTLY_SHORT = "tekton/its/its-rhoai-e2e-rh-nightly-pm-ocp420.yaml"


def test_validate_integration_test_scenario_name_ok() -> None:
    assert validate_integration_test_scenario_name("rhoai-e2e-eaas-ocp421") == (
        "rhoai-e2e-eaas-ocp421"
    )


def test_validate_integration_test_scenario_name_rejects_empty() -> None:
    with pytest.raises(AppError, match="non-empty"):
        validate_integration_test_scenario_name("  ")


def test_looks_like_its_manifest_path() -> None:
    assert looks_like_its_manifest_path(_RH_NIGHTLY_REL)
    assert looks_like_its_manifest_path(_RH_NIGHTLY_SHORT)
    assert not looks_like_its_manifest_path("rhoai-e2e-rh-nightly-pm-ocp420")


def test_resolve_eaas_manifest() -> None:
    path = resolve_integration_test_scenario_manifest(_ROOT, "rhoai-e2e-eaas-ocp421")
    assert path.name == "its-rhoai-e2e-eaas-ocp421.yaml"
    assert integration_test_scenario_application(path) == "rhoai-fbc-fragment-ocp-421"


def test_resolve_rh_nightly_manifest() -> None:
    path = resolve_integration_test_scenario_manifest(
        _ROOT,
        "rhoai-e2e-rh-nightly-pm-ocp420",
    )
    assert path.name == "its-rhoai-e2e-rh-nightly-pm-ocp420.yaml"
    assert integration_test_scenario_application(path) == "rhoai-fbc-fragment-ocp-420"


def test_resolve_manifest_path_repo_relative(monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = _ROOT.resolve().parent.parent
    monkeypatch.chdir(repo_root)
    path = resolve_integration_test_scenario_manifest_path(_ROOT, _RH_NIGHTLY_REL)
    assert path.name == "its-rhoai-e2e-rh-nightly-pm-ocp420.yaml"


def test_resolve_manifest_path_explicit_wrong_cwd_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(_ROOT)
    with pytest.raises(AppError, match="ITS manifest not found"):
        resolve_integration_test_scenario_manifest_path(
            _ROOT,
            "./integration-tests/olminstall/tekton/its/its-rhoai-e2e-rh-nightly-pm-ocp420.yaml",
        )


def test_resolve_manifest_path_olminstall_relative() -> None:
    path = resolve_integration_test_scenario_manifest_path(_ROOT, _RH_NIGHTLY_SHORT)
    assert path.name == "its-rhoai-e2e-rh-nightly-pm-ocp420.yaml"


def test_resolve_ref_from_path_returns_metadata_name() -> None:
    manifest, name = resolve_integration_test_scenario_ref(_ROOT, _RH_NIGHTLY_SHORT)
    assert manifest.name == "its-rhoai-e2e-rh-nightly-pm-ocp420.yaml"
    assert name == "rhoai-e2e-rh-nightly-pm-ocp420"


def test_resolve_manifest_path_rejects_missing_file() -> None:
    with pytest.raises(AppError, match="ITS manifest not found"):
        resolve_integration_test_scenario_manifest_path(
            _ROOT,
            "integration-tests/olminstall/tekton/its/does-not-exist.yaml",
        )


def test_rh_nightly_default_konflux_app() -> None:
    assert (
        integration_test_scenario_default_konflux_app("rhoai-e2e-rh-nightly-pm-ocp420")
        == "rhoai-fbc-fragment-ocp-420"
    )
    assert integration_test_scenario_default_konflux_app("rhoai-e2e-eaas-ocp421") == (
        "rhoai-fbc-fragment-ocp-421"
    )


def test_resolve_run_its_snapshot_rh_nightly() -> None:
    path = resolve_integration_test_scenario_run_its_snapshot(
        _ROOT,
        "rhoai-e2e-rh-nightly-pm-ocp420",
    )
    assert path is not None
    assert path.name == "test-snapshot-rh-nightly.yaml"


def test_resolve_run_its_snapshot_unsupported_returns_none() -> None:
    assert (
        resolve_integration_test_scenario_run_its_snapshot(_ROOT, "rhoai-e2e-eaas-ocp421")
        is None
    )


def test_its_manifest_param_reads_product() -> None:
    path = _ROOT / "tekton" / "its" / "its-rhoai-e2e-rh-nightly-pm-ocp420.yaml"
    from suite.its_registry import its_manifest_param

    assert its_manifest_param(path, "PRODUCT") == "rhoai"


def test_resolve_manifest_path_absolute_under_repo() -> None:
    abs_path = _ROOT / "tekton" / "its" / "its-rhoai-e2e-rh-nightly-pm-ocp420.yaml"
    path = resolve_integration_test_scenario_manifest_path(_ROOT, str(abs_path))
    assert path == abs_path.resolve()


def test_resolve_manifest_path_cwd_relative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _ROOT / "tekton" / "its" / "its-rhoai-e2e-rh-nightly-pm-ocp420.yaml"
    copy = tmp_path / "my-its.yaml"
    copy.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    path = resolve_integration_test_scenario_manifest_path(_ROOT, "my-its.yaml")
    assert path == copy.resolve()


def test_resolve_manifest_path_absolute_outside_repo(tmp_path: Path) -> None:
    source = _ROOT / "tekton" / "its" / "its-rhoai-e2e-rh-nightly-pm-ocp420.yaml"
    copy = tmp_path / "its-copy.yaml"
    copy.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    path = resolve_integration_test_scenario_manifest_path(_ROOT, str(copy))
    assert path == copy.resolve()


def test_resolve_manifest_path_absolute_missing_fails_fast() -> None:
    with pytest.raises(AppError, match="ITS manifest not found"):
        resolve_integration_test_scenario_manifest_path(_ROOT, "/no/such/its-manifest.yaml")


def test_resolve_manifest_path_rejects_escape() -> None:
    with pytest.raises(AppError, match="stay under repository root"):
        resolve_integration_test_scenario_manifest_path(
            _ROOT,
            "tekton/its/../../../../../etc/passwd",
        )


def test_resolve_unknown_manifest() -> None:
    with pytest.raises(AppError, match="No in-tree ITS manifest"):
        resolve_integration_test_scenario_manifest(_ROOT, "does-not-exist")


def test_list_manifests_includes_playpen_its() -> None:
    names = list_integration_test_scenario_manifests(_ROOT)
    assert "rhoai-e2e-eaas-ocp421" in names
    assert "rhoai-e2e-rh-nightly-pm-ocp420" in names


def test_eaas_pipelinerun_wrapper_prefix() -> None:
    path = _ROOT / "tekton" / "pipelines" / "olminstall-pipelinerun-eaas.yaml"
    text = path.read_text(encoding="utf-8")
    assert "generateName: e2e-its-eaas-bvt-smoke-" in text
