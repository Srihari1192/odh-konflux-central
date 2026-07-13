"""Persist gateway auth stack readiness across olminstall install and Cypress steps."""

from __future__ import annotations

import os
from pathlib import Path

_MARKER_NAME = ".gateway-auth-stack-incomplete"


def _marker_base_dirs() -> list[Path]:
    """Directories that may carry cross-step state (setup-dependencies vs Cypress)."""
    bases: list[Path] = []
    tests_shared = os.environ.get("TESTS_SHARED", "").strip()
    if tests_shared:
        from steps.tests_payload import resolve_tests_payload_root

        payload = resolve_tests_payload_root(Path(tests_shared))
        bases.extend((payload / "results", payload))
    artifacts = os.environ.get("ARTIFACTS_DIR", "").strip()
    if artifacts:
        bases.append(Path(artifacts))
    if not bases:
        bases.append(Path("/artifacts"))
    seen: set[str] = set()
    ordered: list[Path] = []
    for base in bases:
        key = str(base)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(base)
    return ordered


def gateway_stack_marker_paths() -> list[Path]:
    return [base / _MARKER_NAME for base in _marker_base_dirs()]


def write_gateway_stack_incomplete_marker() -> None:
    wrote = False
    for path in gateway_stack_marker_paths():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("rhcl post-install retry failed\n", encoding="utf-8")
            wrote = True
        except OSError as exc:
            print(
                f"WARN: could not write gateway stack marker at {path} ({exc})",
                flush=True,
            )
    if not wrote:
        print("WARN: gateway stack incomplete marker not written to any base dir", flush=True)


def clear_gateway_stack_incomplete_marker() -> None:
    for path in gateway_stack_marker_paths():
        if path.is_file():
            path.unlink()


def gateway_stack_incomplete() -> bool:
    return any(path.is_file() for path in gateway_stack_marker_paths())
