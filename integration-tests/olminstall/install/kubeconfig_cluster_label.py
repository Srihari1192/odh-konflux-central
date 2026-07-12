#!/usr/bin/env python3
"""Derive a short cluster label from a kubeconfig (API URL, context, or cluster name)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

# Jenkins common.getClusterNameFromUrl parity (vars/common.groovy).
_OPENSHIFT_CLUSTER_URL_RE = re.compile(r"https://(?:[^.]+)?(?:\.apps|api)\.(.*?)\.")


def cluster_name_from_url(cluster_url: str = "") -> str:
    """Extract cluster id from an OpenShift console or API HTTPS URL."""
    url = (cluster_url or "").strip()
    if not url:
        return ""
    match = _OPENSHIFT_CLUSTER_URL_RE.search(url)
    if match:
        return match.group(1)[:63]
    return ""


def cluster_label_from_kubeconfig(kubeconfig: Path | str) -> str:
    """Return a short cluster label from kubeconfig; empty string if unavailable."""
    path = Path(kubeconfig).expanduser().resolve()
    if not path.is_file():
        return ""
    env = {**os.environ, "KUBECONFIG": str(path)}
    for jsonpath in (
        "{.clusters[0].cluster.server}",
        "{.clusters[0].name}",
        "{.contexts[0].name}",
    ):
        proc = subprocess.run(
            ["oc", "config", "view", "--minify", "-o", f"jsonpath={jsonpath}"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        if proc.returncode != 0:
            continue
        raw = (proc.stdout or "").strip()
        if jsonpath.endswith(".server}"):
            label = cluster_name_from_url(raw)
        else:
            label = _sanitize_cluster_label(raw)
        if label:
            return label
    return ""


def _extract_from_api_cluster_token(token: str) -> str:
    """``api-ods-qe-psi-09-osp-…`` or ``api.ods-qe-psi-09.…`` → ``ods-qe-psi-09``."""
    token = token.split(":")[0].strip()
    if token.startswith("api-"):
        rest = token[4:]
        for marker in (
            "-osp-",
            "-p1.",
            "-p2.",
            "-p3.",
            "-hjvn.",
            "-p1-openshiftapps-",
            "-p2-openshiftapps-",
            "-p3-openshiftapps-",
        ):
            if marker in rest:
                return rest.split(marker)[0][:63]
        if "-rh-ods-" in rest:
            return rest.split("-rh-ods-")[0][:63]
    if token.startswith("api."):
        host = token[4:]
        if host.startswith("ods-qe-") or ".osp." in host:
            # api.ods-qe-psi-09.osp.rh-ods.com
            return host.split(".")[0][:63]
    return ""


def _sanitize_cluster_label(raw: str) -> str:
    """Drop hostnames/URLs; keep a short human-readable cluster id."""
    name = raw.strip()
    if not name:
        return ""
    if "://" in name:
        from_url = cluster_name_from_url(name)
        if from_url:
            return from_url
        return ""
    # oc login context: default/api-CLUSTER:6443/user → use api-CLUSTER segment
    if "/" in name:
        parts = list(reversed(name.split("/")))
        for part in parts:
            extracted = _extract_from_api_cluster_token(part)
            if extracted:
                return extracted
        for part in parts:
            if part and part not in ("default",) and ":" not in part and len(part) <= 63:
                return part[:63]
    extracted = _extract_from_api_cluster_token(name)
    if extracted:
        return extracted
    if name.startswith("api.") and name.count(".") >= 2:
        return name.split(".")[1][:63]
    if re.match(r"^[\w.-]+\.[\w.-]+\.", name):
        return name.split(".")[0][:63]
    return name[:63]


def main() -> int:
    kubeconfig = os.environ.get("KUBECONFIG", "").strip()
    out_path = os.environ.get("CLUSTER_NAME_PATH", "").strip()
    if not kubeconfig:
        print("KUBECONFIG is required", file=sys.stderr)
        return 1
    label = cluster_label_from_kubeconfig(kubeconfig)
    if not label:
        print("WARN: could not resolve cluster label from kubeconfig", file=sys.stderr)
        return 0
    print(f"External cluster: {label}")
    if out_path:
        Path(out_path).write_text(label, encoding="ascii")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
