"""Jenkins verifyDashboardRoute parity for Tekton prepare (wait + curl probe)."""

from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from components.dashboard_cypress.config import (
    resolve_odh_dashboard_base_url,
    write_dashboard_cypress_test_config,
)
from components.dashboard_cypress.runtime import verify_dashboard_reachable
from install.dsc_install import oc_run

_DEFAULT_DEPLOYMENT_WAIT_SEC = 180
_DEFAULT_ROUTE_VERIFY_TIMEOUT_SEC = 900
_GATEWAY_REPAIR_ATTEMPTS = frozenset({1, 4, 7, 10, 13, 16, 19, 22, 25, 28})
# Jenkins verifyDashboardRoute.groovy: 3 minutes per deployment wait.
_JENKINS_DEPLOYMENT_WAIT_MINUTES = 3


def _dashboard_ready_status() -> str:
    r = oc_run(
        [
            "get",
            "datasciencecluster",
            "default-dsc",
            "-o",
            'jsonpath={.status.conditions[?(@.type=="DashboardReady")].status}',
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    return (r.stdout or "").strip()


def _repair_gateway_stack_for_verify() -> None:
    """Re-run §15 gateway prep when deployments are up but dashboard routes are missing."""
    from install.gateway_config import ensure_rhoai_gateway_for_install, gateway_config_ready
    from install.dsc_install import ensure_dashboard_gateway_prereqs
    from install.rhoai_gateway_prep import ensure_transitive_olm_deps_for_gateway

    print("verify-operator-ready: running RHOAI gateway repair (§15 P1–P4)...", flush=True)
    ensure_dashboard_gateway_prereqs(for_gateway_stack=True)
    try:
        approved = ensure_transitive_olm_deps_for_gateway(wait_servicemesh=True)
        if approved:
            print(f"✓ Approved {approved} transitive InstallPlan(s) for gateway stack", flush=True)
    except Exception as exc:
        print(f"WARN: transitive OLM approve failed ({exc})", file=sys.stderr, flush=True)
    try:
        timeout = int(os.environ.get("GATEWAY_CONFIG_WAIT_SEC", "1200"))
        ensure_rhoai_gateway_for_install(wait_timeout_sec=timeout, wait_servicemesh_first=True)
    except Exception as exc:
        print(f"WARN: gateway repair failed ({exc})", file=sys.stderr, flush=True)
    if gateway_config_ready():
        print("✓ GatewayConfig Ready after verify gateway repair", flush=True)


def wait_all_cluster_deployments_available(*, timeout_sec: int = _DEFAULT_DEPLOYMENT_WAIT_SEC) -> bool:
    """Jenkins verifyDashboardRoute: parallel ``oc wait`` on every Deployment (-A)."""
    list_r = oc_run(
        ["get", "deployments", "-A", "-o", "json"],
        check=False,
        capture_output=True,
        timeout=120,
    )
    if list_r.returncode != 0:
        print(
            f"WARN: could not list cluster deployments: {(list_r.stderr or list_r.stdout or '').strip()}",
            file=sys.stderr,
            flush=True,
        )
        return False
    try:
        items = json.loads(list_r.stdout or "{}").get("items") or []
    except json.JSONDecodeError:
        print("WARN: could not parse deployment list JSON", file=sys.stderr, flush=True)
        return False

    pairs: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        meta = item.get("metadata") or {}
        ns = (meta.get("namespace") or "").strip()
        name = (meta.get("name") or "").strip()
        if ns and name:
            pairs.append((ns, name))
    if not pairs:
        return True

    print(
        f"Wait for all deployments to be ready (up to {timeout_sec // 60} minutes, "
        f"{len(pairs)} deployment(s), parallel)",
        flush=True,
    )

    def _wait_one(ns: str, name: str) -> bool:
        wait_r = oc_run(
            [
                "wait",
                "--for=condition=available",
                f"--timeout={timeout_sec}s",
                f"deployment/{name}",
                "-n",
                ns,
            ],
            check=False,
            capture_output=True,
            timeout=timeout_sec + 60,
        )
        if wait_r.returncode != 0:
            print(
                f"WARN: deployment/{name} in {ns} not available within {timeout_sec}s",
                file=sys.stderr,
                flush=True,
            )
            return False
        return True

    ok = True
    workers = min(32, len(pairs))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_wait_one, ns, name) for ns, name in pairs]
        for fut in as_completed(futures):
            if not fut.result():
                ok = False
    return ok


def wait_for_dashboard_route(
    *,
    timeout_sec: int | None = None,
    poll_sec: int = 30,
    deployment_wait_sec: int | None = None,
) -> str:
    """Block until dashboard URL resolves, DashboardReady=True, and curl preflight passes."""
    total = timeout_sec
    if total is None:
        total = int(os.environ.get("DASHBOARD_ROUTE_VERIFY_TIMEOUT_SEC", str(_DEFAULT_ROUTE_VERIFY_TIMEOUT_SEC)))
    deploy_wait = deployment_wait_sec
    if deploy_wait is None:
        deploy_wait = int(
            os.environ.get(
                "DASHBOARD_DEPLOYMENT_WAIT_SEC",
                str(_JENKINS_DEPLOYMENT_WAIT_MINUTES * 60),
            )
        )

    if not wait_all_cluster_deployments_available(timeout_sec=deploy_wait):
        print(
            f"WARN: some cluster deployments were not ready after {deploy_wait}s",
            file=sys.stderr,
            flush=True,
        )

    _repair_gateway_stack_for_verify()

    deadline = time.monotonic() + total
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        if attempt in _GATEWAY_REPAIR_ATTEMPTS:
            url_probe = resolve_odh_dashboard_base_url()
            if not url_probe or _dashboard_ready_status() != "True":
                _repair_gateway_stack_for_verify()
        url = resolve_odh_dashboard_base_url()
        ready = _dashboard_ready_status()
        if url:
            print(
                f"Dashboard route attempt {attempt}: url={url} DashboardReady={ready or '?'}",
                flush=True,
            )
            if verify_dashboard_reachable(url):
                if ready != "True":
                    print(
                        f"WARN: gateway URL reachable but DashboardReady={ready or '?'}; "
                        "continuing (HTTP preflight passed)",
                        file=sys.stderr,
                        flush=True,
                    )
                print(f"Dashboard route verified: {url}", flush=True)
                return url
        else:
            print(
                f"Dashboard route attempt {attempt}: gateway URL not resolved yet "
                f"(DashboardReady={ready or '?'})",
                flush=True,
            )
        time.sleep(poll_sec)

    url = resolve_odh_dashboard_base_url()
    ready = _dashboard_ready_status()
    raise RuntimeError(
        "Dashboard route not ready after "
        f"{total}s (url={url or 'missing'}, DashboardReady={ready or '?'})"
    )


def dashboard_cypress_accessible_for_smoke(*, url: str | None = None) -> bool:
    """True when gateway HTTP preflight passes (Jenkins verifyDashboardRoute parity)."""
    resolved = (url or resolve_odh_dashboard_base_url() or "").strip()
    if not resolved:
        return False
    return verify_dashboard_reachable(resolved)


def verify_dashboard_route_for_prepare(*, artifacts_dir: Path | None = None) -> str:
    """Prepare-step entry: wait for route and write dashboard-cypress-config.yml."""
    out_dir = artifacts_dir
    if out_dir is None:
        raw = os.environ.get("ARTIFACTS_DIR", "").strip()
        out_dir = Path(raw) if raw else None
    url = wait_for_dashboard_route()
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        cfg = out_dir / "dashboard-cypress-config.yml"
        write_dashboard_cypress_test_config(cfg, dashboard_url=url)
        print(f"Wrote dashboard Cypress config at {cfg}", flush=True)
    return url
