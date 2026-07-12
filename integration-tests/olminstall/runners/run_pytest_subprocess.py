"""Subprocess pytest runner for component smoke and BVT health checks.

Prefer importing from this module; ``runners.run_bvt_pytest`` remains for Tekton
``python3 -m runners.run_bvt_pytest`` and backward-compatible imports.
"""

from __future__ import annotations

from runners.run_bvt_pytest import (  # noqa: F401
    main,
    run_health_suite,
    run_single,
)

__all__ = ["main", "run_health_suite", "run_single"]
