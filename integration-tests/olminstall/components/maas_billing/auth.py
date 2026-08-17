"""Authorino TLS, Kuadrant readiness, and MaaS API AuthPolicy acceptance."""

from __future__ import annotations

import json
import os
import sys
import time

from install.dependency_operators import _authorino_cr_exists
from install.dsc_install import oc_run
from install.olminstall_checkout import resolve_olminstall_dir
from install.rhcl_deps import run_post_install_rhcl_operator

from components.maas_billing.common import (
    _AUTHORINO_CR_NAME,
    _AUTHORINO_SVC,
    _AUTHORINO_TLS_SECRET,
    _KUADRANT_CR_NAME,
    _KUADRANT_OPERATOR_LABELS,
    _MAAS_APPS_NS,
    _MAAS_AUTH_POLICY,
)


def _oc_run(*args, **kwargs):
    return oc_run(*args, **kwargs)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _authorino_namespace() -> str:
    override = os.environ.get("AUTHORINO_NAMESPACE", "").strip()
    if override:
        return override
    for ns in ("kuadrant-system", "rh-connectivity-link"):
        r = _oc_run(["get", "authorino", _AUTHORINO_CR_NAME, "-n", ns], check=False, capture_output=True, timeout=15)
        if r.returncode == 0:
            return ns
    return "kuadrant-system"


def _run_post_install_rhcl() -> bool:
    return run_post_install_rhcl_operator()


def _authorino_deployment_ready(namespace: str) -> bool:
    r = _oc_run(
        [
            "get",
            "deployment",
            "authorino",
            "-n",
            namespace,
            "-o",
            "jsonpath={.status.readyReplicas}",
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if r.returncode != 0:
        return False
    try:
        return int((r.stdout or "0").strip() or "0") >= 1
    except ValueError:
        return False


def _authorino_service_present(namespace: str) -> bool:
    r = _oc_run(
        ["get", "svc", "-n", namespace, "-o", "jsonpath={.items[*].metadata.name}"],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if r.returncode != 0:
        return False
    names = (r.stdout or "").split()
    return _AUTHORINO_SVC in names or any("authorino" in name for name in names)


def _authorino_tls_configured(namespace: str) -> bool:
    r = _oc_run(
        [
            "get",
            "authorino",
            _AUTHORINO_CR_NAME,
            "-n",
            namespace,
            "-o",
            "jsonpath={.spec.listener.tls.enabled}",
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if r.returncode != 0:
        return False
    if (r.stdout or "").strip().lower() != "true":
        return False
    secret_r = _oc_run(
        [
            "get",
            "authorino",
            _AUTHORINO_CR_NAME,
            "-n",
            namespace,
            "-o",
            "jsonpath={.spec.listener.tls.certSecretRef.name}",
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    return bool((secret_r.stdout or "").strip())


def authorino_workload_tls_ready() -> bool:
    """True when Authorino CR, deployment, service, and listener TLS are ready."""
    from install.dependency_operators import _authorino_cr_exists

    if not _authorino_cr_exists():
        return False
    ns = _authorino_namespace()
    return (
        _authorino_deployment_ready(ns)
        and _authorino_service_present(ns)
        and _authorino_tls_configured(ns)
    )


def _wait_authorino_workload_ready(*, timeout_sec: int) -> str:
    """Wait until Authorino CR deployment and service exist (post-install-rhcl prerequisite)."""
    deadline = time.time() + timeout_sec
    cached_ns = ""
    while time.time() < deadline:
        if _authorino_cr_exists():
            if not cached_ns:
                cached_ns = _authorino_namespace()
            dep_ready = _authorino_deployment_ready(cached_ns)
            svc_present = _authorino_service_present(cached_ns)
            if dep_ready and svc_present:
                print(f"✓ Authorino workload ready in {cached_ns}", flush=True)
                return cached_ns
            if int(time.time()) % 30 < 12:
                print(
                    f"Waiting for Authorino deployment/service in {cached_ns} "
                    f"(deployment ready={dep_ready}, service present={svc_present})...",
                    flush=True,
                )
        elif int(time.time()) % 30 < 12:
            print("Waiting for Authorino CR...", flush=True)
        _sleep(15)

    raise RuntimeError(f"Authorino service not ready after {timeout_sec}s")


def _prepare_authorino_tls_via_gitops() -> bool:
    try:
        olm_dir = resolve_olminstall_dir()
    except FileNotFoundError as exc:
        print(f"WARN: {exc}; skipping odh-gitops prepare-authorino-tls", file=sys.stderr, flush=True)
        return False
    gitops = olm_dir / "odh-gitops"
    if not gitops.is_dir():
        return False
    from install.dependency_operators import _run_odh_gitops_make

    rc = _run_odh_gitops_make(olm_dir, "prepare-authorino-tls", "KUSTOMIZE_MODE=false")
    if rc != 0:
        print(
            f"WARN: prepare-authorino-tls exited {rc}; continuing with inline Authorino TLS setup",
            file=sys.stderr,
            flush=True,
        )
        return False
    print("✓ odh-gitops prepare-authorino-tls completed", flush=True)
    return True


def _kuadrant_namespace() -> str:
    for ns in ("kuadrant-system", "rh-connectivity-link"):
        r = _oc_run(
            ["get", "kuadrant", _KUADRANT_CR_NAME, "-n", ns],
            check=False,
            capture_output=True,
            timeout=15,
        )
        if r.returncode == 0:
            return ns
    return "kuadrant-system"


def _kuadrant_ready_status(namespace: str) -> tuple[str, str]:
    path = (
        f'{{.status.conditions[?(@.type=="Ready")].status}}'
        f'\t{{.status.conditions[?(@.type=="Ready")].reason}}'
    )
    r = _oc_run(
        ["get", "kuadrant", _KUADRANT_CR_NAME, "-n", namespace, "-o", f"jsonpath={path}"],
        check=False,
        capture_output=True,
        timeout=30,
    )
    parts = (r.stdout or "").strip().split("\t") + ["", ""]
    return parts[0], parts[1]


def kuadrant_cr_ready() -> bool:
    """True when Kuadrant CR Ready condition is True in kuadrant-system (or rh-connectivity-link)."""
    status, _reason = _kuadrant_ready_status(_kuadrant_namespace())
    return status == "True"


def maas_gateway_auth_stack_live_ready() -> bool:
    """Live probe: Kuadrant Ready and Authorino workload+TLS after cleanup/reinstall."""
    return kuadrant_cr_ready() and authorino_workload_tls_ready()


def _gateway_api_provider_present() -> bool:
    """True when a GatewayClass is Accepted (Istio / OpenShift gateway controller)."""
    r = _oc_run(
        [
            "get",
            "gatewayclass",
            "-o",
            'jsonpath={range .items[*]}{.metadata.name}{"\\t"}{.status.conditions[?(@.type=="Accepted")].status}{"\\n"}{end}',
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if r.returncode != 0:
        return False
    for line in (r.stdout or "").splitlines():
        parts = line.strip().split("\t")
        if len(parts) >= 2 and parts[1] == "True":
            return True
    return False


def recover_kuadrant_after_gateway_api_provider(*, timeout_sec: int | None = None) -> bool:
    """Clear Kuadrant MissingDependency after Gateway API provider appears (cleanup+reinstall).

    post-install-rhcl often runs before Service Mesh / GatewayClass exists. Kuadrant then
    stays Ready=False (MissingDependency) until the operator is restarted once the provider
    is installed. Returns True when Kuadrant is Ready (already or after recovery).
    """
    from helpers.gateway_stack_marker import clear_gateway_stack_incomplete_marker

    if kuadrant_cr_ready():
        clear_gateway_stack_incomplete_marker()
        return True

    ns = _kuadrant_namespace()
    status, reason = _kuadrant_ready_status(ns)
    if not _gateway_api_provider_present():
        print(
            f"Kuadrant not Ready (status={status or '?'}, reason={reason or '?'}) "
            "and no Accepted GatewayClass yet — cannot recover MissingDependency",
            flush=True,
        )
        return False

    raw_timeout = os.environ.get("KUADRANT_GATEWAY_PROVIDER_RECOVER_TIMEOUT_SEC", "").strip()
    if timeout_sec is not None:
        wait_sec = timeout_sec
    elif raw_timeout:
        try:
            wait_sec = int(raw_timeout)
        except ValueError:
            wait_sec = 300
    else:
        wait_sec = 300

    print(
        f"Kuadrant Ready={status or '?'} reason={reason or '?'} with GatewayClass present — "
        f"restarting Kuadrant operator pods in {ns} (up to {wait_sec}s)...",
        flush=True,
    )
    _restart_kuadrant_operator_pods(ns)
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        if kuadrant_cr_ready():
            clear_gateway_stack_incomplete_marker()
            print("✓ Kuadrant Ready after Gateway API provider recovery restart", flush=True)
            return True
        if int(time.time()) % 20 < 12:
            st, rs = _kuadrant_ready_status(ns)
            print(
                f"Waiting for Kuadrant Ready after operator restart "
                f"(status={st or '?'}, reason={rs or '?'})...",
                flush=True,
            )
        _sleep(10)
    st, rs = _kuadrant_ready_status(ns)
    print(
        f"WARN: Kuadrant still not Ready after {wait_sec}s "
        f"(status={st or '?'}, reason={rs or '?'})",
        file=sys.stderr,
        flush=True,
    )
    return False


def _restart_kuadrant_operator_pods(namespace: str) -> None:
    for label in _KUADRANT_OPERATOR_LABELS:
        _oc_run(
            [
                "delete",
                "pod",
                "-n",
                namespace,
                "-l",
                label,
                "--force",
                "--grace-period=0",
            ],
            check=False,
            capture_output=True,
            timeout=60,
        )


def _maas_auth_policy_accepted() -> bool:
    r = _oc_run(
        [
            "get",
            "authpolicy",
            _MAAS_AUTH_POLICY,
            "-n",
            _MAAS_APPS_NS,
            "-o",
            "jsonpath={.status.conditions[?(@.type=='Accepted')].status}",
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    return (r.stdout or "").strip() == "True"


def _wait_maas_api_auth_policy_accepted(*, timeout_sec: int) -> None:
    kuadrant_ns = _kuadrant_namespace()
    deadline = time.time() + timeout_sec
    restarted_operator = False
    while time.time() < deadline:
        if _maas_auth_policy_accepted():
            print(f"✓ MaaS API AuthPolicy {_MAAS_APPS_NS}/{_MAAS_AUTH_POLICY} Accepted", flush=True)
            return

        ready_status, ready_reason = _kuadrant_ready_status(kuadrant_ns)
        if ready_reason == "MissingDependency" or ready_status != "True":
            if not restarted_operator:
                print(
                    f"Kuadrant not ready (status={ready_status or '?'}, reason={ready_reason or '?'}) "
                    f"— restarting operator pods in {kuadrant_ns}...",
                    flush=True,
                )
                _restart_kuadrant_operator_pods(kuadrant_ns)
                restarted_operator = True
                _sleep(20)
                continue

        if int(time.time()) % 30 < 12:
            print(
                f"Waiting for {_MAAS_AUTH_POLICY} Accepted "
                f"(Kuadrant Ready={ready_status or '?'} reason={ready_reason or '?'})...",
                flush=True,
            )
        _sleep(10)

    raise RuntimeError(
        f"MaaS API AuthPolicy {_MAAS_APPS_NS}/{_MAAS_AUTH_POLICY} not Accepted after {timeout_sec}s "
        f"(Kuadrant may still report MissingDependency — AuthPolicy enforcement required for MaaS API)"
    )


def _rollout_restart_deployment(namespace: str, name: str, *, timeout_sec: int = 120) -> None:
    r = _oc_run(
        ["get", "deployment", name, "-n", namespace],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if r.returncode != 0:
        return
    _oc_run(
        ["rollout", "restart", f"deployment/{name}", "-n", namespace],
        check=False,
        capture_output=True,
        timeout=60,
    )
    _oc_run(
        ["rollout", "status", f"deployment/{name}", "-n", namespace, f"--timeout={timeout_sec}s"],
        check=False,
        capture_output=True,
        timeout=timeout_sec + 30,
    )


def _restart_maas_auth_workloads(authorino_ns: str) -> None:
    """Pick up Authorino TLS and AuthPolicy changes (models-as-a-service deploy.sh parity)."""
    _rollout_restart_deployment(authorino_ns, "authorino")
    _rollout_restart_deployment(_MAAS_APPS_NS, "maas-api")
    print("✓ Restarted authorino and maas-api deployments after auth gateway setup", flush=True)


def _wait_maas_api_deployment_ready(*, timeout_sec: int) -> None:
    """Wait until maas-api exists so AuthPolicy validation URL can resolve."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        r = _oc_run(
            [
                "get",
                "deployment",
                "maas-api",
                "-n",
                _MAAS_APPS_NS,
                "-o",
                "jsonpath={.status.readyReplicas}",
            ],
            check=False,
            capture_output=True,
            timeout=30,
        )
        if r.returncode == 0:
            try:
                if int((r.stdout or "0").strip() or "0") >= 1:
                    print(f"✓ maas-api deployment ready in {_MAAS_APPS_NS}", flush=True)
                    return
            except ValueError:
                pass
        if int(time.time()) % 30 < 12:
            print(f"Waiting for maas-api deployment in {_MAAS_APPS_NS}...", flush=True)
        _sleep(10)

    raise RuntimeError(f"maas-api deployment not ready in {_MAAS_APPS_NS} after {timeout_sec}s")


def ensure_maas_authorino_ready() -> str:
    """Wait for Authorino workload and configure TLS before enabling ModelsAsService."""
    from steps.cluster_prep_state import dep_operators_already_done

    authorino_timeout = int(os.environ.get("AUTHORINO_READY_TIMEOUT_SEC", "600"))
    authorino_ns = _wait_authorino_workload_ready(timeout_sec=authorino_timeout)
    skip_rhcl = dep_operators_already_done() and authorino_workload_tls_ready()
    rhcl_ok = False
    if skip_rhcl:
        print(
            "Skipping post-install-rhcl-operator.sh (dep-operators done, Authorino TLS ready)",
            flush=True,
        )
        rhcl_ok = True
    else:
        try:
            rhcl_ok = _run_post_install_rhcl()
        except FileNotFoundError as exc:
            print(f"WARN: {exc}; falling back to inline Authorino TLS setup", file=sys.stderr, flush=True)
    if not rhcl_ok:
        _prepare_authorino_tls_via_gitops()
        ensure_authorino_tls()
    return authorino_ns


def ensure_maas_auth_policy_ready(*, authorino_ns: str | None = None) -> None:
    """Wait for MaaS API AuthPolicy Accepted after maas-api is running."""
    policy_timeout = int(os.environ.get("MAAS_AUTH_POLICY_TIMEOUT_SEC", "600"))
    maas_api_timeout = int(os.environ.get("MAAS_API_READY_TIMEOUT_SEC", "600"))
    ns = authorino_ns or _authorino_namespace()
    _wait_maas_api_deployment_ready(timeout_sec=maas_api_timeout)
    _wait_maas_api_auth_policy_accepted(timeout_sec=policy_timeout)
    _restart_maas_auth_workloads(ns)


def ensure_maas_auth_gateway_ready() -> None:
    """Authorino TLS + AuthPolicy Accepted (legacy single-call sequence)."""
    authorino_ns = ensure_maas_authorino_ready()
    ensure_maas_auth_policy_ready(authorino_ns=authorino_ns)


def ensure_authorino_tls() -> None:
    """Enable Authorino listener TLS (models-as-a-service scripts/setup-authorino-tls.sh parity)."""
    ns = _authorino_namespace()
    svc = _oc_run(["get", "svc", _AUTHORINO_SVC, "-n", ns], check=False, capture_output=True, timeout=30)
    if svc.returncode != 0:
        print(f"WARN: Authorino service {_AUTHORINO_SVC} not found in {ns}; skipping TLS setup", file=sys.stderr)
        return

    _oc_run(
        [
            "annotate",
            "svc",
            _AUTHORINO_SVC,
            "-n",
            ns,
            f"service.beta.openshift.io/serving-cert-secret-name={_AUTHORINO_TLS_SECRET}",
            "--overwrite",
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    patch_doc = json.dumps(
        {
            "spec": {
                "listener": {
                    "tls": {
                        "enabled": True,
                        "certSecretRef": {"name": _AUTHORINO_TLS_SECRET},
                    }
                }
            }
        }
    )
    _oc_run(
        ["patch", "authorino", _AUTHORINO_CR_NAME, "-n", ns, "--type=merge", "-p", patch_doc],
        check=False,
        capture_output=True,
        timeout=30,
    )
    _oc_run(
        [
            "set",
            "env",
            "deployment/authorino",
            "-n",
            ns,
            "SSL_CERT_FILE=/etc/ssl/certs/openshift-service-ca/service-ca-bundle.crt",
            "REQUESTS_CA_BUNDLE=/etc/ssl/certs/openshift-service-ca/service-ca-bundle.crt",
        ],
        check=False,
        capture_output=True,
        timeout=60,
    )
    print(f"✓ Authorino TLS configured in {ns}", flush=True)
