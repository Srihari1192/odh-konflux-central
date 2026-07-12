"""MaaS and DSC readiness wait loops."""

from __future__ import annotations

import sys
import time

from install.dsc_install import oc_run

from components.maas_billing.common import (
    _dsc_condition,
    _dsc_condition_types,
    _GATEWAY_NAME,
    _GATEWAY_NS,
    _maas_smoke_ready,
    maas_functional_smoke_ready,
    maas_smoke_acceptable_for_run,
)
from components.maas_billing.timeouts import maas_dsc_prereq_grace_sec


def _wait_for_maas_gateway_programmed(*, timeout_sec: int) -> None:
    """Wait until gateway is smoke-ready (Programmed=True or EaaS functional fallback)."""
    from components.maas_billing.common import (
        _maas_gateway_programmed,
        _maas_gateway_ready_for_smoke,
    )
    from install.gateway_config import cluster_source_is_eaas

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        ready, reason = _maas_gateway_ready_for_smoke()
        if ready:
            programmed, _ = _maas_gateway_programmed()
            if programmed:
                print(
                    f"✓ MaaS gateway {_GATEWAY_NS}/{_GATEWAY_NAME} Programmed=True",
                    flush=True,
                )
            else:
                print(f"✓ {reason[:200]}", flush=True)
            return
        if cluster_source_is_eaas() and "missing annotation" in reason:
            try:
                from components.maas_billing.gateway import ensure_maas_gateway

                ensure_maas_gateway()
            except Exception as exc:
                print(
                    f"WARN: MaaS gateway re-annotate during wait: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
        if int(time.time()) % 30 < 12:
            print(f"Waiting for MaaS gateway Programmed: {reason[:120]}", flush=True)
        time.sleep(12)
    _, reason = _maas_gateway_ready_for_smoke()
    raise RuntimeError(
        f"MaaS gateway not Programmed after {timeout_sec}s — {reason[:300]}"
    )


_OPENSHIFT_GATEWAY_CONTROLLER_DEPLOYMENTS = (
    "istiod-openshift-gateway",
    "data-science-gateway-data-science-gateway-class",
)


def _ensure_openshift_gateway_controller_ready(*, timeout_sec: int = 180) -> None:
    """Wait for OpenShift Gateway API controller before MaaS gateway can expose HTTPS."""
    for name in _OPENSHIFT_GATEWAY_CONTROLLER_DEPLOYMENTS:
        r = oc_run(
            [
                "wait",
                "--for=condition=available",
                f"--timeout={timeout_sec}s",
                f"deployment/{name}",
                "-n",
                "openshift-ingress",
            ],
            check=False,
            capture_output=True,
            timeout=timeout_sec + 30,
        )
        if r.returncode != 0:
            print(
                f"WARN: deployment/{name} in openshift-ingress not available within {timeout_sec}s",
                file=sys.stderr,
                flush=True,
            )


def _nudge_maas_gateway_reconcile() -> None:
    """Re-apply gateway TLS/annotations so openshift-default listener can reconcile."""
    from components.maas_billing.auth import ensure_authorino_tls
    from components.maas_billing.gateway import (
        ensure_maas_gateway,
        ensure_maas_gateway_ingress_tls_secret,
    )

    ensure_maas_gateway_ingress_tls_secret()
    ensure_authorino_tls()
    ensure_maas_gateway()


def _wait_maas_gateway_https_service(*, timeout_sec: int) -> None:
    """Wait until maas-api can resolve the gateway-owned HTTPS service (strict; no EaaS fallback)."""
    from components.maas_billing.common import (
        _maas_gateway_https_service_ready,
        _maas_gateway_programmed,
    )

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        svc_ready, svc_detail = _maas_gateway_https_service_ready()
        if svc_ready:
            print(f"✓ MaaS gateway HTTPS service ready ({svc_detail})", flush=True)
            return
        programmed, prog_detail = _maas_gateway_programmed()
        if programmed:
            detail = f"Programmed=True but {svc_detail}"
        else:
            detail = prog_detail or svc_detail
        if int(time.time()) % 30 < 12:
            print(f"Waiting for MaaS gateway HTTPS service: {detail[:120]}", flush=True)
        time.sleep(12)
    _, svc_detail = _maas_gateway_https_service_ready()
    raise RuntimeError(
        f"MaaS gateway HTTPS service not ready after {timeout_sec}s — {svc_detail[:300]}"
    )


def _wait_maas_gateway_https_for_models_as_service(*, timeout_sec: int) -> None:
    """modelsAsService requires gateway-owned HTTPS; EaaS functional fallback is insufficient."""
    from components.maas_billing.common import (
        _maas_gateway_https_service_ready,
        _maas_gateway_programmed,
    )

    _ensure_openshift_gateway_controller_ready()
    deadline = time.time() + timeout_sec
    last_nudge = 0.0
    while time.time() < deadline:
        svc_ready, svc_detail = _maas_gateway_https_service_ready()
        if svc_ready:
            print(f"✓ MaaS gateway HTTPS service ready ({svc_detail})", flush=True)
            return
        now = time.time()
        if now - last_nudge >= 60:
            last_nudge = now
            _nudge_maas_gateway_reconcile()
        programmed, prog_detail = _maas_gateway_programmed()
        if programmed:
            detail = f"Programmed=True but {svc_detail}"
        else:
            detail = prog_detail or svc_detail
        if int(now) % 30 < 12:
            print(
                f"Waiting for MaaS gateway HTTPS service (modelsAsService gate): {detail[:120]}",
                flush=True,
            )
        time.sleep(12)
    _, svc_detail = _maas_gateway_https_service_ready()
    raise RuntimeError(
        f"MaaS gateway HTTPS service not ready after {timeout_sec}s — {svc_detail[:300]}"
    )


def _wait_for_maas_smoke_ready(*, timeout_sec: int) -> None:
    from components.maas_billing.common import deps_only_install_dependencies_smoke

    if deps_only_install_dependencies_smoke():
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            ready, reason = maas_functional_smoke_ready()
            if ready:
                print(
                    "✓ MaaS functional prerequisites ready (deps-only install-dependencies)",
                    flush=True,
                )
                return
            if int(time.time()) % 60 < 12:
                print(f"Waiting for MaaS functional readiness: {reason[:120]}", flush=True)
            time.sleep(12)
        _, reason = maas_functional_smoke_ready()
        raise RuntimeError(
            "MaaS functional prerequisites not ready after "
            f"{timeout_sec}s — {reason[:300]}"
        )

    require_prereq = "MaaSPrerequisitesAvailable" in _dsc_condition_types()
    if require_prereq:
        ready_label = (
            "MaaSPrerequisitesAvailable + ModelsAsServiceReady + DSC Ready"
        )
    else:
        ready_label = "ModelsAsServiceReady + DSC Ready (MaaSPrerequisitesAvailable not exposed)"
        print(
            "NOTE: DSC has no MaaSPrerequisitesAvailable condition on this cluster; "
            "waiting for ModelsAsServiceReady + DSC Ready only",
            flush=True,
        )

    grace_sec = maas_dsc_prereq_grace_sec()
    started = time.time()
    deadline = started + timeout_sec
    grace_deadline = started + grace_sec

    while time.time() < deadline:
        acceptable, accept_reason = maas_smoke_acceptable_for_run()
        if acceptable:
            if "lagging" in accept_reason or "DSC Ready=False" in accept_reason:
                print(f"WARN: {accept_reason}", file=sys.stderr, flush=True)
            print(f"✓ MaaS component prerequisites ready ({ready_label})", flush=True)
            return

        prereq_status, _, prereq_msg = _dsc_condition("MaaSPrerequisitesAvailable")
        maas_status, _, maas_msg = _dsc_condition("ModelsAsServiceReady")
        ready_status, _, ready_msg = _dsc_condition("Ready")

        if time.time() >= grace_deadline:
            func_ready, func_reason = maas_functional_smoke_ready()
            if maas_status == "True" and func_ready and ready_status == "True":
                print(
                    "WARN: accepting MaaS smoke after grace period "
                    f"({grace_sec}s) — functional ready but DSC MaaSPrerequisites "
                    f"still {(prereq_status or '?')}: {(prereq_msg or func_reason)[:120]}",
                    file=sys.stderr,
                    flush=True,
                )
                return

        if int(time.time()) % 60 < 12:
            prereq_display = prereq_status if require_prereq else "n/a"
            print(
                f"Waiting for MaaS smoke readiness "
                f"(MaaSPrerequisites={prereq_display or '?'} ModelsAsService={maas_status or '?'} "
                f"DSC Ready={ready_status or '?'}): "
                f"{(prereq_msg or maas_msg or 'reconciling...')[:120]}",
                flush=True,
            )
        time.sleep(12)

    acceptable, accept_reason = maas_smoke_acceptable_for_run()
    if acceptable:
        print(f"✓ MaaS component prerequisites ready at timeout boundary", flush=True)
        return

    prereq_status, _, prereq_msg = _dsc_condition("MaaSPrerequisitesAvailable")
    maas_status, _, maas_msg = _dsc_condition("ModelsAsServiceReady")
    ready_status, _, ready_msg = _dsc_condition("Ready")
    prereq_display = prereq_status if require_prereq else "n/a"
    raise RuntimeError(
        "MaaS component prerequisites not ready after "
        f"{timeout_sec}s "
        f"(MaaSPrerequisites={prereq_display}, ModelsAsService={maas_status}, "
        f"DSC Ready={ready_status}) — "
        f"{(prereq_msg or maas_msg or ready_msg or accept_reason or 'reconcile incomplete')[:300]}"
    )


def _wait_for_dsc_component_ready(*, condition_type: str, timeout_sec: int) -> None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        status, reason, msg = _dsc_condition(condition_type)
        if status == "True":
            print(f"✓ DataScienceCluster/default-dsc {condition_type}=True", flush=True)
            return
        if reason == "Removed":
            raise RuntimeError(f"DSC component disabled ({condition_type} reason=Removed)")
        if int(time.time()) % 60 < 12:
            print(
                f"Waiting for DSC {condition_type} "
                f"(status={status or '?'} reason={reason or '?'}): "
                f"{(msg or 'reconciling...')[:120]}",
                flush=True,
            )
        time.sleep(12)
    status, reason, msg = _dsc_condition(condition_type)
    raise RuntimeError(
        f"DSC {condition_type} not ready after {timeout_sec}s "
        f"(status={status or '?'}, reason={reason or '?'}): "
        f"{(msg or 'reconcile incomplete')[:300]}"
    )


def _wait_for_dsc_ready(*, timeout_sec: int) -> None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        r = oc_run(
            [
                "get",
                "datasciencecluster",
                "default-dsc",
                "-o",
                "jsonpath={.status.conditions[?(@.type=='Ready')].status}",
            ],
            check=False,
            capture_output=True,
            timeout=30,
        )
        if (r.stdout or "").strip() == "True":
            print("✓ DataScienceCluster/default-dsc Ready", flush=True)
            return
        time.sleep(10)
    print(
        "WARN: DataScienceCluster/default-dsc not Ready after "
        f"{timeout_sec}s — continuing smoke (tests may fail)",
        file=sys.stderr,
    )
