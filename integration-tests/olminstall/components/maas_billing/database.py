"""MaaS database secret setup (models-as-a-service parity)."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from install.dsc_install import oc_run
from steps.tekton_util import git_clone

from components.maas_billing.common import (
    _MAAS_APPS_NS,
    _MAAS_DB_SECRET,
    _MODELS_AS_SERVICE_DEST,
    _MODELS_AS_SERVICE_REPO,
    _kubectl_shim_dir,
    _secret_exists,
)

_MAAS_INFRA_NS = "odh-ai-gateway-infra"


def _maas_infra_namespace() -> str:
    return os.environ.get("MAAS_INFRA_NAMESPACE", _MAAS_INFRA_NS).strip() or _MAAS_INFRA_NS


def _read_secret_data_key(namespace: str, secret_name: str, key: str) -> str | None:
    r = oc_run(
        ["get", "secret", secret_name, "-n", namespace, "-o", "json"],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if r.returncode != 0:
        return None
    try:
        body = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return None
    encoded = (body.get("data") or {}).get(key)
    if not encoded:
        return None
    return base64.b64decode(encoded).decode("utf-8", errors="replace").strip() or None


def _maas_postgres_service() -> str:
    return os.environ.get("MAAS_POSTGRES_SERVICE", "postgres").strip() or "postgres"


def _postgres_host_for_apps_namespace(infra_ns: str | None = None) -> str:
    ns = (infra_ns or _maas_infra_namespace()).strip()
    service = _maas_postgres_service()
    return f"{service}.{ns}.svc.cluster.local"


def _rewrite_db_connection_url_for_apps_namespace(
    connection_url: str,
    *,
    infra_ns: str | None = None,
) -> str:
    """maas-api runs in apps ns; Postgres from setup-database.sh is in the infra ns."""
    infra = (infra_ns or _maas_infra_namespace()).strip()
    service = _maas_postgres_service()
    target_host = _postgres_host_for_apps_namespace(infra)
    parsed = urlparse(connection_url)
    if not parsed.hostname:
        return connection_url
    if parsed.hostname == target_host:
        return connection_url
    ns_svc_host = f"{service}.{infra}.svc"
    if parsed.hostname not in (service, ns_svc_host):
        return connection_url
    port = parsed.port or 5432
    auth = ""
    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth += f":{parsed.password}"
        auth += "@"
    netloc = f"{auth}{target_host}:{port}"
    return urlunparse(
        (
            parsed.scheme,
            netloc,
            parsed.path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


def _repair_apps_maas_db_connection_url_if_needed() -> bool:
    """Update apps maas-db-config when it still points at infra-local postgres hostname."""
    current = _read_secret_data_key(_MAAS_APPS_NS, _MAAS_DB_SECRET, "DB_CONNECTION_URL")
    if not current:
        return False
    repaired = _rewrite_db_connection_url_for_apps_namespace(current)
    if repaired == current:
        return False
    print(
        f"Repairing {_MAAS_APPS_NS}/{_MAAS_DB_SECRET} DB host for cross-namespace maas-api "
        f"({urlparse(current).hostname} -> {urlparse(repaired).hostname})",
        flush=True,
    )
    _create_maas_db_config_secret(_MAAS_APPS_NS, repaired)
    return True


def _restart_maas_api_after_db_config() -> None:
    """Roll maas-api so it picks up maas-db-config in redhat-ods-applications."""
    r = oc_run(
        ["get", "deployment", "maas-api", "-n", _MAAS_APPS_NS],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if r.returncode != 0:
        return
    from components.maas_billing.auth import (
        _rollout_restart_deployment,
        _wait_maas_api_deployment_ready,
    )

    rollout_timeout = int(os.environ.get("MAAS_API_ROLLOUT_TIMEOUT_SEC", "300"))
    ready_timeout = int(os.environ.get("MAAS_API_READY_TIMEOUT_SEC", "600"))
    print(
        f"Rolling out maas-api in {_MAAS_APPS_NS} after {_MAAS_DB_SECRET} update...",
        flush=True,
    )
    _rollout_restart_deployment(
        _MAAS_APPS_NS,
        "maas-api",
        timeout_sec=rollout_timeout,
    )
    _wait_maas_api_deployment_ready(timeout_sec=ready_timeout)


def _promote_maas_db_secret_to_apps_namespace() -> bool:
    """Copy maas-db-config into redhat-ods-applications when setup-database.sh left it in infra."""
    if _secret_exists(_MAAS_APPS_NS, _MAAS_DB_SECRET):
        return True
    infra_ns = _maas_infra_namespace()
    if not _namespace_exists(infra_ns) or not _secret_exists(infra_ns, _MAAS_DB_SECRET):
        return False
    connection_url = _read_secret_data_key(infra_ns, _MAAS_DB_SECRET, "DB_CONNECTION_URL")
    if not connection_url:
        return False
    connection_url = _rewrite_db_connection_url_for_apps_namespace(
        connection_url,
        infra_ns=infra_ns,
    )
    print(
        f"Promoting {_MAAS_DB_SECRET} from {infra_ns} to {_MAAS_APPS_NS} "
        "(setup-database.sh deploys Postgres in the infra namespace)",
        flush=True,
    )
    _create_maas_db_config_secret(_MAAS_APPS_NS, connection_url)
    return _secret_exists(_MAAS_APPS_NS, _MAAS_DB_SECRET)


def _create_maas_db_config_secret(namespace: str, connection_url: str) -> None:
    created = oc_run(
        [
            "create",
            "secret",
            "generic",
            _MAAS_DB_SECRET,
            "--from-file=DB_CONNECTION_URL=/dev/stdin",
            "-n",
            namespace,
            "--dry-run=client",
            "-o",
            "yaml",
        ],
        stdin_text=connection_url,
        check=True,
        capture_output=True,
        timeout=60,
    )
    apply = oc_run(
        ["apply", "-f", "-"],
        stdin_text=created.stdout or "",
        check=False,
        capture_output=True,
        timeout=60,
    )
    if apply.returncode != 0:
        err = (apply.stderr or apply.stdout or "").strip()
        raise RuntimeError(f"Could not apply {_MAAS_DB_SECRET}: {err or 'unknown error'}")
    oc_run(
        ["label", "secret", _MAAS_DB_SECRET, "-n", namespace, "app=maas-api", "--overwrite"],
        check=False,
        capture_output=True,
        timeout=30,
    )


def _clone_models_as_a_service() -> Path:
    dest = _MODELS_AS_SERVICE_DEST
    if dest.exists():
        shutil.rmtree(dest)
    rev = os.environ.get("MODELS_AS_SERVICE_REPO_REVISION", "").strip() or "main"
    print(f"Cloning models-as-a-service for MaaS DB setup ({_MODELS_AS_SERVICE_REPO} @ {rev})...", flush=True)
    git_clone(_MODELS_AS_SERVICE_REPO, rev, dest, tls_workaround=True)
    return dest


def _namespace_exists(name: str) -> bool:
    r = oc_run(
        ["get", "namespace", name],
        check=False,
        capture_output=True,
        timeout=30,
    )
    return r.returncode == 0


def ensure_maas_database() -> None:
    """Ensure maas-db-config exists in redhat-ods-applications (models-as-a-service parity)."""
    if not _namespace_exists(_MAAS_APPS_NS):
        from install.dependency_operators import product_install_path

        if product_install_path():
            print(
                f"NOTE: deferring {_MAAS_DB_SECRET} until {_MAAS_APPS_NS} exists "
                "(post install-rhoai; prepare-components will retry)",
                flush=True,
            )
            return
        raise RuntimeError(
            f"namespace {_MAAS_APPS_NS} not found; cannot ensure {_MAAS_DB_SECRET}"
        )

    if _secret_exists(_MAAS_APPS_NS, _MAAS_DB_SECRET):
        if _repair_apps_maas_db_connection_url_if_needed():
            print(
                f"✓ MaaS database secret {_MAAS_APPS_NS}/{_MAAS_DB_SECRET} repaired for apps-namespace maas-api",
                flush=True,
            )
            _restart_maas_api_after_db_config()
        else:
            print(f"✓ MaaS database secret {_MAAS_APPS_NS}/{_MAAS_DB_SECRET} exists", flush=True)
        return

    external_url = os.environ.get("MAAS_DB_CONNECTION_URL", "").strip()
    if external_url:
        print(f"Creating {_MAAS_DB_SECRET} from MAAS_DB_CONNECTION_URL...", flush=True)
        _create_maas_db_config_secret(_MAAS_APPS_NS, external_url)
        print(f"✓ MaaS database secret {_MAAS_APPS_NS}/{_MAAS_DB_SECRET} created", flush=True)
        _restart_maas_api_after_db_config()
        return

    repo = _clone_models_as_a_service()
    script = repo / "scripts" / "setup-database.sh"
    if not script.is_file():
        raise FileNotFoundError(f"Missing MaaS setup script: {script}")

    env = os.environ.copy()
    env["MAAS_CONTROLLER_NAMESPACE"] = _MAAS_APPS_NS
    env["DB_SSLMODE"] = "disable"
    env["PATH"] = f"{_kubectl_shim_dir()}:{env.get('PATH', '')}"
    print(
        f"Running setup-database.sh (MAAS_CONTROLLER_NAMESPACE={_MAAS_APPS_NS}, DB_SSLMODE=disable)...",
        flush=True,
    )
    try:
        proc = subprocess.run(
            ["bash", str(script)],
            cwd=repo,
            env=env,
            check=False,
            timeout=600,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("setup-database.sh timed out after 600s") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"setup-database.sh failed (exit {proc.returncode})")
    if not _secret_exists(_MAAS_APPS_NS, _MAAS_DB_SECRET):
        if not _promote_maas_db_secret_to_apps_namespace():
            raise RuntimeError(
                f"{_MAAS_DB_SECRET} still missing in {_MAAS_APPS_NS} after setup-database.sh"
            )
    print(f"✓ MaaS database ready ({_MAAS_APPS_NS}/{_MAAS_DB_SECRET})", flush=True)
    _restart_maas_api_after_db_config()
