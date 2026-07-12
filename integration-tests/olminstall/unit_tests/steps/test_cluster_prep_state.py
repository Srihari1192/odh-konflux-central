"""Unit tests for cluster prep markers shared across Tekton tasks."""

from __future__ import annotations

from pathlib import Path

from steps.cluster_prep_state import (
    cluster_prep_already_done,
    dep_operators_already_done,
    mark_cluster_prep_done,
    mark_dep_operators_done,
)

def test_dep_operators_marker_skips_duplicate_rhcl(tmp_path, monkeypatch) -> None:
    payload = tmp_path / "tests-payload" / "results"
    monkeypatch.setenv("TESTS_SHARED", str(tmp_path))
    monkeypatch.setenv("PIPELINE_RUN_NAME", "pr-test-1")
    mark_dep_operators_done()
    assert dep_operators_already_done()
    assert not cluster_prep_already_done()

    mark_cluster_prep_done(payload)
    assert cluster_prep_already_done(payload)

def test_stale_marker_from_other_pipelinerun_ignored(tmp_path, monkeypatch) -> None:
    payload = tmp_path / "tests-payload" / "results"
    payload.mkdir(parents=True)
    monkeypatch.setenv("TESTS_SHARED", str(tmp_path))
    monkeypatch.setenv("PIPELINE_RUN_NAME", "pr-old")
    mark_dep_operators_done()
    monkeypatch.setenv("PIPELINE_RUN_NAME", "pr-new")
    assert not dep_operators_already_done()

def test_artifacts_dir_preferred_over_tests_shared(tmp_path, monkeypatch) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    monkeypatch.setenv("ARTIFACTS_DIR", str(artifacts))
    monkeypatch.setenv("TESTS_SHARED", str(tmp_path / "other"))
    mark_dep_operators_done()
    assert (artifacts / ".dep-operators-done").is_file()
