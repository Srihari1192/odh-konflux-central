"""Resolve OLM UPDATE_CHANNEL for RHOAI Konflux triggers."""

from __future__ import annotations

import re

from suite.its_trigger_params import rhoai_version_from_app


def _stable_channel_version(version: str) -> str:
    """Normalize version strings to major.minor for OLM stable-* channels."""
    text = (version or "").strip()
    match = re.match(r"^(\d+)[.-](\d+)", text)
    if match:
        return f"{match.group(1)}.{match.group(2)}"
    return text


def resolve_rhoai_update_channel(*, version: str = "", resolved_app: str = "") -> str | None:
    """Map ``--rhoai-version`` or a ``rhoai-v*`` application name to an OLM channel.

    TestOps Jenkins autotrigger-smoke 3.5 EA sets ``UPDATE_CHANNEL=beta`` in ``test-variables.yml``
    and runs ``setup.sh -t operator -u beta`` for RHOAI 3.5 EA (3.5.0-ea.x). Konflux uses
    the same rule: ``3.5`` / ``rhoai-v3-5*`` / ``rhoai-v*-ea-*`` → ``beta`` until GA
    ``stable-3.5`` exists in the FBCF catalog.

    Examples:
        version=3.5 → beta
        resolved_app=rhoai-v3-5-ea-2 → beta
        resolved_app=rhoai-v3-4-foo → stable-3.4
    """
    app = (resolved_app or "").strip()
    if re.search(r"-ea-\d+", app):
        return "beta"

    ver = version.strip()
    if ver == "3.5" or re.match(r"^rhoai-v3-5(?:-|$)", app):
        return "beta"

    if ver:
        return f"stable-{_stable_channel_version(ver)}"

    app_version = _stable_channel_version(rhoai_version_from_app(app))
    if app_version:
        return f"stable-{app_version}"
    if app.startswith("rhoai-v3-"):
        return "stable-3.x"
    return None
