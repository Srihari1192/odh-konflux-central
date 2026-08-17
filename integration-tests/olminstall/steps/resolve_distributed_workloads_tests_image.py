#!/usr/bin/env python3
"""Resolve distributed-workloads-tests image (Jenkins :latest parity)."""

from __future__ import annotations

import os

from _bootstrap import ensure_olminstall_path

ensure_olminstall_path()

from steps.tekton_util import require_env, write_result

_DEFAULT_REPO = "quay.io/opendatahub/distributed-workloads-tests"


def main() -> int:
    result_path = require_env("RESULT_PATH")
    repo = os.environ.get("DISTRIBUTED_WORKLOADS_TESTS_REPO", "").strip() or _DEFAULT_REPO
    # Jenkins components/distributed-workloads/main.yaml pins :latest (no CSV→tag mapping).
    resolved = f"{repo}:latest"
    write_result(result_path, resolved)
    print(f"Using distributed-workloads-tests image: {resolved} (Jenkins :latest parity)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
