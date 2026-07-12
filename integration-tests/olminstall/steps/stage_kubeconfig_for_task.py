#!/usr/bin/env python3
"""Stage target-cluster kubeconfig at /credentials/kubeconfig for install/test tasks.

Env:
    CLUSTER_SOURCE     -- EAAS, or tenant Secret with key kubeconfig (external cluster)
    EAAS_KUBECONFIG_REL -- path under /credentials from EaaS get-kubeconfig step
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from _bootstrap import ensure_olminstall_path

ensure_olminstall_path()

from steps.external_kubeconfig_mount import copy_external_kubeconfig_mount  # noqa: E402
from steps.prepare_diagnostics_kubeconfig import _fetch_external_kubeconfig, _namespace  # noqa: E402
from suite.its_trigger_params import external_kubeconfig_secret_name  # noqa: E402

_CREDENTIALS = Path("/credentials")
_KUBECONFIG = _CREDENTIALS / "kubeconfig"


def _write_secure(path: Path, data: bytes) -> None:
    with open(path, "wb", opener=lambda p, flags: os.open(p, flags, 0o600)) as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(data)


def main() -> int:
    external = external_kubeconfig_secret_name(os.environ.get("CLUSTER_SOURCE", ""))
    eaas_rel = os.environ.get("EAAS_KUBECONFIG_REL", "").strip()
    _CREDENTIALS.mkdir(parents=True, exist_ok=True)

    if external:
        if copy_external_kubeconfig_mount(_KUBECONFIG):
            return 0
        ns = _namespace()
        if not ns:
            print("ERROR: namespace required to read external kubeconfig secret", file=sys.stderr)
            return 1
        try:
            content = _fetch_external_kubeconfig(external, ns)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"ERROR: could not load external kubeconfig from secret/{external}: {exc}", file=sys.stderr)
            return 1
        _write_secure(_KUBECONFIG, content.encode("utf-8"))
        print(f"External kubeconfig staged at {_KUBECONFIG}")
        return 0

    if eaas_rel:
        src = _CREDENTIALS / eaas_rel
        if not src.is_file():
            print(f"ERROR: EaaS kubeconfig missing at {src}", file=sys.stderr)
            return 1
        if src.resolve() != _KUBECONFIG.resolve():
            _write_secure(_KUBECONFIG, src.read_bytes())
        print(f"EaaS kubeconfig staged at {_KUBECONFIG} (from {eaas_rel})")
        return 0

    print("ERROR: set CLUSTER_SOURCE (tenant Secret name) or EAAS_KUBECONFIG_REL", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
