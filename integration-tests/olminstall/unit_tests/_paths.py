"""Shared paths for olminstall unit tests (stable across subpackage layout)."""

from __future__ import annotations

from pathlib import Path

OLMINSTALL_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = OLMINSTALL_ROOT.parent.parent
