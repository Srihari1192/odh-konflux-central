"""Ensure integration-tests/olminstall is on sys.path for Tekton step scripts.

Tekton steps invoke modules via ``python -m steps.<module>`` or ``python -m runners.<module>``
from the olminstall root (see ``tekton/pipelines/olminstall-pipeline.yaml``).
"""

from __future__ import annotations

import sys
from pathlib import Path

_OLMINSTALL_ROOT = Path(__file__).resolve().parent


def ensure_olminstall_path() -> Path:
    root = str(_OLMINSTALL_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    return _OLMINSTALL_ROOT
