"""Shared JUnit helpers for component test runners (pytest, golang, cypress)."""

from __future__ import annotations

from suite.cluster_api_health import is_definitive_infra_error


def prereq_junit_outcome(reason: str) -> str:
    """JUnit testcase outcome for prereq blocks: hard infra failures vs soft skips."""
    return "failure" if is_definitive_infra_error(reason) else "skip"
