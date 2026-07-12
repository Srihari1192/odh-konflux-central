#!/usr/bin/env python3
"""Legacy Tekton script-path entry for BVT/component pytest.

Delegates to ``runners.run_bvt_pytest`` so timeout budget and env naming stay in one place.
Prefer ``python3 -m runners.run_bvt_pytest`` (task-bvt-health-checks).
"""

from __future__ import annotations

from runners.run_bvt_pytest import main

if __name__ == "__main__":
    raise SystemExit(main())
