"""Detect unreachable cluster API (EaaS lease DNS death, etc.) for fail-fast infra attribution."""

from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

from install.dsc_install import _discover_operator_admission_webhook_service, oc_run
from suite.its_trigger_params import CLUSTER_SOURCE_EAAS

_INFRA_PREFIX = "cluster API unreachable"

_UNREACHABLE_MARKERS: tuple[str, ...] = (
    "no such host",
    "name or service not known",
    "unable to connect to the server",
    "connection refused",
    "connection reset",
    "i/o timeout",
    "getaddrinfo",
    "enotfound",
    "nameresolutionerror",
    "dial tcp: lookup",
    "failed to resolve",
    "max retries exceeded",
    "no endpoints available for service",
)


def cluster_api_unreachable_text(*, stderr: str = "", stdout: str = "") -> str:
    """Return a one-line infra reason when *stderr*/*stdout* indicate API DNS/connect failure."""
    blob = f"{stderr}\n{stdout}".strip()
    if not blob:
        return ""
    lower = blob.lower()
    for marker in _UNREACHABLE_MARKERS:
        if marker in lower:
            for line in blob.splitlines():
                if marker in line.lower():
                    snippet = line.strip()[:240]
                    return f"{_INFRA_PREFIX}: {snippet}"
            return f"{_INFRA_PREFIX}: {marker}"
    return ""


def cluster_api_unreachable_reason(*, probe: bool = True) -> str:
    """Return non-empty infra reason when the cluster API cannot be reached."""
    if not probe:
        return ""
    try:
        result = oc_run(
            ["get", "datasciencecluster", "default-dsc"],
            check=False,
            capture_output=True,
            timeout=15,
        )
    except Exception as exc:
        return cluster_api_unreachable_text(stderr=str(exc)) or f"{_INFRA_PREFIX}: {exc}"
    msg = cluster_api_unreachable_text(
        stderr=result.stderr or "",
        stdout=result.stdout or "",
    )
    if msg:
        return msg
    return ""


def _oc_infra_reason_from_exception(exc: Exception) -> str:
    return cluster_api_unreachable_text(stderr=str(exc)) or f"{_INFRA_PREFIX}: {exc}"


def _oc_infra_reason_from_output(*, stderr: str = "", stdout: str = "") -> str:
    return cluster_api_unreachable_text(stderr=stderr, stdout=stdout)


def _service_has_ready_endpoints(*, service: str, namespace: str) -> tuple[bool, str]:
    try:
        result = oc_run(
            [
                "get",
                "endpoints",
                service,
                "-n",
                namespace,
                "-o",
                "jsonpath={.subsets[*].addresses[*].ip}",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception as exc:
        return False, _oc_infra_reason_from_exception(exc)
    if result.returncode == 0 and (result.stdout or "").strip():
        return True, ""
    api_msg = _oc_infra_reason_from_output(
        stderr=result.stderr or "",
        stdout=result.stdout or "",
    )
    if api_msg:
        return False, api_msg
    return False, f"{_INFRA_PREFIX}: {service} webhook has no endpoints in {namespace}"


def operator_admission_webhook_unavailable_reason(*, probe: bool = True) -> str:
    """Return infra reason when the operator admission webhook Service has no endpoints."""
    if not probe:
        return ""
    ns = (os.environ.get("OPERATOR_NAMESPACE") or "redhat-ods-operator").strip()
    svc = _discover_operator_admission_webhook_service(ns)
    ready, reason = _service_has_ready_endpoints(service=svc, namespace=ns)
    return "" if ready else reason


def _console_hostname_unreachable_reason(hostname: str) -> str:
    """Return infra reason when the console route hostname does not resolve."""
    host = (hostname or "").strip().rstrip(".")
    if not host:
        return f"{_INFRA_PREFIX}: openshift console unavailable: empty hostname"
    try:
        socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        msg = cluster_api_unreachable_text(stderr=str(exc))
        if msg:
            return msg
        return f"{_INFRA_PREFIX}: openshift console unavailable: {exc}"
    return ""


def openshift_console_route_unavailable_reason(*, probe: bool = True) -> str:
    """Return infra reason when the cluster console route hostname is unreachable (EaaS lease DNS)."""
    if not probe:
        return ""
    try:
        result = oc_run(
            ["whoami", "--show-console"],
            check=False,
            capture_output=True,
            timeout=15,
        )
    except Exception as exc:
        return cluster_api_unreachable_text(stderr=str(exc)) or f"{_INFRA_PREFIX}: {exc}"
    msg = cluster_api_unreachable_text(
        stderr=result.stderr or "",
        stdout=result.stdout or "",
    )
    if msg:
        return msg
    if result.returncode != 0:
        snippet = (result.stderr or result.stdout or "").strip()[:240]
        if snippet:
            return f"{_INFRA_PREFIX}: openshift console unavailable: {snippet}"
        return ""
    console_url = (result.stdout or "").strip()
    if not console_url:
        return ""
    host = urlparse(console_url).hostname or ""
    return _console_hostname_unreachable_reason(host)


def _extended_eaas_infra_probes_enabled() -> bool:
    """Webhook/console probes run on EaaS Tekton steps (not local unit tests)."""
    source = os.environ.get("CLUSTER_SOURCE", "").strip()
    if source not in ("", CLUSTER_SOURCE_EAAS):
        return False
    in_pipeline = bool(
        os.environ.get("PIPELINE_RUN_NAME", "").strip()
        or os.environ.get("ARTIFACTS_DIR", "").strip()
    )
    return in_pipeline and bool(os.environ.get("KUBECONFIG", "").strip())


def cluster_smoke_infra_blocked_reason(*, probe: bool = True) -> str:
    """Combined infra probe: API, operator webhook, and console route (EaaS lease death)."""
    reason = cluster_api_unreachable_reason(probe=probe)
    if reason:
        return reason
    if not probe or not _extended_eaas_infra_probes_enabled():
        return ""
    for check in (
        operator_admission_webhook_unavailable_reason,
        openshift_console_route_unavailable_reason,
    ):
        reason = check(probe=probe)
        if reason:
            return reason
    return ""


def is_definitive_infra_error(message: str) -> bool:
    """True when *message* is a hard infra block (API death, MaaS Removed, gateway never ready)."""
    if not message:
        return False
    lower = message.lower()
    if lower.startswith(_INFRA_PREFIX.lower()):
        return True
    markers = (
        "modelsasservice.managementstate=removed",
        "models as service.managementstate=removed",
        "maas gateway https service not ready",
        "namespace \"oidc\" not found",
        'namespaces "oidc" not found',
        "byoidc-credentials",
        "kuadrant/authorino dependency operators are missing",
        "webhook has no endpoints",
        "openshift console unavailable",
        "broken hypershift admission webhook",
        "xxx-invalid-service-xxx",
        "block-resources.hypershift.openshift.io",
    )
    return any(m in lower for m in markers)
