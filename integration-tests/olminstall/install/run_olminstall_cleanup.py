"""Tekton step: run data-hub/olminstall ``cleanup.sh -t operator`` on the target cluster."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from _bootstrap import ensure_olminstall_path

ensure_olminstall_path()

from suite.errors import AppError
from k8s.external_kubeconfig import verify_external_cluster_login

_CLEANUP_SCRIPT = "cleanup.sh"


def _require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise AppError(
            f"cleanup-external requires {name!r} on PATH (olminstall cleanup.sh uses it).",
            2,
        )


def run_cleanup_operator(*, olminstall_dir: Path, kubeconfig: str | Path) -> None:
    """Run ``cleanup.sh -t operator`` from a cloned olminstall tree."""
    script = olminstall_dir.resolve() / _CLEANUP_SCRIPT
    if not script.is_file():
        raise AppError(f"olminstall repo missing {_CLEANUP_SCRIPT}: {script}", 2)
    _invoke_cleanup(script, kubeconfig=Path(kubeconfig))


def _invoke_cleanup(script: Path, *, kubeconfig: Path) -> None:
    olm_dir = script.parent.resolve()
    cmd = ["bash", script.name, "-t", "operator"]
    env = os.environ.copy()
    env["KUBECONFIG"] = str(kubeconfig)
    print(f"INFO (cwd={olm_dir}) {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, cwd=olm_dir, env=env, check=False)
    if proc.returncode != 0:
        raise AppError(f"olminstall cleanup.sh failed (exit {proc.returncode})", proc.returncode)


def main() -> int:
    """Tekton step entrypoint: ``KUBECONFIG`` + ``OLMINSTALL_DIR`` required."""
    kubeconfig = os.environ.get("KUBECONFIG", "").strip()
    olm_dir = os.environ.get("OLMINSTALL_DIR", "").strip()
    if not kubeconfig:
        print("KUBECONFIG is required", file=sys.stderr)
        return 1
    if not olm_dir:
        print("OLMINSTALL_DIR is required", file=sys.stderr)
        return 1
    for tool in ("oc", "bash", "jq"):
        _require_tool(tool)
    try:
        who = verify_external_cluster_login(Path(kubeconfig))
        print(f"INFO Running olminstall cleanup on cluster as {who} (KUBECONFIG={kubeconfig})", flush=True)
        run_cleanup_operator(olminstall_dir=Path(olm_dir), kubeconfig=kubeconfig)
    except AppError as exc:
        print(exc, file=sys.stderr)
        return exc.code if exc.code else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
