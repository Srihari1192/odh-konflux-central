"""Resolve in-tree IntegrationTestScenario manifests by metadata.name or repo path."""

from __future__ import annotations

import re
from pathlib import Path

from suite.errors import AppError

_K8S_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")

# metadata.name -> config snapshot YAML for ``--run-its NAME`` offline FBC fallback
_ITS_RUN_ITS_SNAPSHOT_BY_NAME: dict[str, str] = {
    "odh-olminstall-testops-rh-nightly": "config/test-snapshot-rh-nightly.yaml",
}

# metadata.name -> Konflux Application when ``--konflux-app`` differs from DEFAULT_APP
_ITS_DEFAULT_KONFLUX_APP_BY_NAME: dict[str, str] = {
    "odh-olminstall-testops-rh-nightly": "rhoai-fbc-fragment-ocp-420",
}


def validate_integration_test_scenario_name(name: str) -> str:
    text = (name or "").strip()
    if not text:
        raise AppError("IntegrationTestScenario name must be non-empty.", 2)
    if not _K8S_NAME_RE.fullmatch(text):
        raise AppError(
            f"Invalid IntegrationTestScenario name {text!r}; use a valid Kubernetes resource name.",
            2,
        )
    return text


def looks_like_its_manifest_path(ref: str) -> bool:
    """True when *ref* is a manifest path, not a bare metadata.name."""
    text = (ref or "").strip()
    return bool(text) and ("/" in text or text.endswith((".yaml", ".yml")))


def konflux_repo_root(olminstall_root: Path) -> Path:
    """Repository root (parent of ``integration-tests/``)."""
    return olminstall_root.resolve().parent.parent


def integration_test_scenario_name_from_manifest(manifest_path: Path) -> str:
    """Return ``metadata.name`` from an IntegrationTestScenario manifest."""
    doc = _load_manifest_doc(manifest_path)
    if doc.get("kind") != "IntegrationTestScenario":
        raise AppError(
            f"ITS manifest {manifest_path} kind must be IntegrationTestScenario "
            f"(got {doc.get('kind')!r}).",
            2,
        )
    meta = doc.get("metadata")
    if not isinstance(meta, dict):
        raise AppError(f"ITS manifest {manifest_path} is missing metadata.", 2)
    name = str(meta.get("name", "")).strip()
    if not name:
        raise AppError(f"ITS manifest {manifest_path} is missing metadata.name.", 2)
    return validate_integration_test_scenario_name(name)


def _resolve_manifest_candidate(candidate: Path, *, repo_root: Path, ref: str) -> Path | None:
    """Return *candidate* when it resolves under *repo_root* and exists."""
    resolved = candidate.expanduser().resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError as exc:
        raise AppError(
            f"ITS manifest path must stay under repository root: {ref!r}",
            2,
        ) from exc
    return resolved if resolved.is_file() else None


def _its_manifest_path_candidates(olminstall_root: Path, ref: str) -> list[Path]:
    """Build manifest path candidates: absolute, then olminstall-relative, then repo-relative."""
    text = (ref or "").strip()
    raw = Path(text).expanduser()
    if raw.is_absolute():
        return [raw]
    repo_root = konflux_repo_root(olminstall_root)
    return [olminstall_root / text, repo_root / text]


def resolve_integration_test_scenario_manifest_path(olminstall_root: Path, ref: str) -> Path:
    """Resolve *ref* to an on-disk ITS manifest.

    Accepted forms:
    - ``metadata.name`` (indexed under ``tekton/its/``)
    - path relative to the olminstall tree (e.g. ``tekton/its/foo.yaml``)
    - path relative to the repository root (e.g. ``integration-tests/olminstall/...``)
    - absolute path when it resolves under the repository root
    """
    text = (ref or "").strip()
    if not text:
        raise AppError("IntegrationTestScenario reference must be non-empty.", 2)
    if looks_like_its_manifest_path(text):
        repo_root = konflux_repo_root(olminstall_root)
        for candidate in _its_manifest_path_candidates(olminstall_root, text):
            if path := _resolve_manifest_candidate(candidate, repo_root=repo_root, ref=text):
                return path
        raise AppError(
            f"ITS manifest not found for path {text!r} "
            f"(tried olminstall-relative, repo-relative, and absolute paths under {repo_root}).",
            2,
        )
    return resolve_integration_test_scenario_manifest(olminstall_root, text)


def resolve_integration_test_scenario_ref(olminstall_root: Path, ref: str) -> tuple[Path, str]:
    """Return (manifest path, metadata.name) for an ITS name or manifest path."""
    manifest = resolve_integration_test_scenario_manifest_path(olminstall_root, ref)
    return manifest, integration_test_scenario_name_from_manifest(manifest)


def _its_dir(olminstall_root: Path) -> Path:
    return olminstall_root / "tekton" / "its"


def _load_manifest_doc(path: Path) -> dict:
    try:
        import yaml
    except ImportError as exc:
        raise AppError("PyYAML is required to read ITS manifests.", 1) from exc
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AppError(f"Cannot read ITS manifest {path}: {exc}", 1) from exc
    except yaml.YAMLError as exc:
        raise AppError(f"Invalid YAML in ITS manifest {path}: {exc}", 1) from exc
    if not isinstance(doc, dict):
        raise AppError(f"ITS manifest {path} is empty or not a mapping.", 1)
    return doc


def integration_test_scenario_default_konflux_app(name: str) -> str:
    """Return the Konflux Application for ``--enable-its NAME`` when not testops-playpen."""
    validated = validate_integration_test_scenario_name(name)
    return _ITS_DEFAULT_KONFLUX_APP_BY_NAME.get(validated, "")


def integration_test_scenario_application(manifest_path: Path) -> str:
    doc = _load_manifest_doc(manifest_path)
    spec = doc.get("spec")
    if not isinstance(spec, dict):
        return ""
    return str(spec.get("application", "")).strip()


def its_manifest_param(manifest_path: Path, param_name: str) -> str:
    """Read one ``spec.params`` value from an ITS manifest file."""
    doc = _load_manifest_doc(manifest_path)
    spec = doc.get("spec")
    if not isinstance(spec, dict):
        return ""
    params = spec.get("params")
    if not isinstance(params, list):
        return ""
    for item in params:
        if isinstance(item, dict) and item.get("name") == param_name:
            return str(item.get("value", "")).strip()
    return ""


def list_integration_test_scenario_manifests(olminstall_root: Path) -> dict[str, Path]:
    """Map metadata.name -> manifest path for all YAML files under tekton/its/."""
    out: dict[str, Path] = {}
    its_dir = _its_dir(olminstall_root)
    if not its_dir.is_dir():
        return out
    for path in sorted(its_dir.glob("*.yaml")):
        doc = _load_manifest_doc(path)
        if doc.get("kind") != "IntegrationTestScenario":
            continue
        meta = doc.get("metadata")
        if not isinstance(meta, dict):
            continue
        name = str(meta.get("name", "")).strip()
        if name:
            out[name] = path
    return out


def resolve_integration_test_scenario_manifest(olminstall_root: Path, name: str) -> Path:
    """Return manifest path for a known ITS name; raise AppError when missing."""
    validated = validate_integration_test_scenario_name(name)
    its_dir = _its_dir(olminstall_root)
    indexed = list_integration_test_scenario_manifests(olminstall_root)
    path = indexed.get(validated)
    if path is not None:
        return path
    known = ", ".join(sorted(indexed)) or "(none)"
    raise AppError(
        f"No in-tree ITS manifest for {validated!r} under {its_dir}. Known names: {known}",
        2,
    )


def format_known_integration_test_scenario_names(olminstall_root: Path) -> str:
    return ", ".join(sorted(list_integration_test_scenario_manifests(olminstall_root)))


def resolve_integration_test_scenario_run_its_snapshot(
    olminstall_root: Path, name: str
) -> Path | None:
    """Return Snapshot manifest for ``--run-its`` offline FBC fallback, or None."""
    validated = validate_integration_test_scenario_name(name)
    rel = _ITS_RUN_ITS_SNAPSHOT_BY_NAME.get(validated)
    if not rel:
        return None
    path = olminstall_root / rel
    if not path.is_file():
        raise AppError(f"--run-its snapshot file missing: {path}", 1)
    return path
