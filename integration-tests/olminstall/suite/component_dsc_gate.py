"""DSC enablement probes for per-component smoke (fail-fast when Removed)."""

from __future__ import annotations

import os
import sys
import time

from install.dependency_operators import maas_dependency_operators_ready
from install.dsc_install import (
    _dsc_smoke_managed_components,
    _resolve_operator_version_for_dsc,
    components_need_models_as_service,
    oc_run,
)
from install.llama_stack_deps import _LLAMA_STACK_CRD, llama_stack_crd_present
from suite.cluster_api_health import (
    cluster_api_unreachable_text,
    cluster_smoke_infra_blocked_reason,
)
from suite.errors import AppError
from components.maas_billing.common import _dsc_condition_types, _maas_smoke_ready

# Primary Ready condition per smoke catalog id (status.conditions[].reason == Removed).
_SMOKE_READY_CONDITION: dict[str, str] = {
    "workbenches": "WorkbenchesReady",
    "model_registry": "ModelRegistryReady",
    "model_server": "KserveReady",
    "model_runtime": "KserveReady",
    "maas_billing": "ModelsAsServiceReady",
    "ai_pipelines": "AIPipelinesReady",
    "kuberay": "RayReady",
    "mlflow": "MLflowOperatorReady",
    "ai_safety": "TrustyAIReady",
    "llama_stack": "LlamaStackOperatorReady",
    "dashboard_cypress": "DashboardReady",
    "trainer": "TrainerReady",
    "distributed_workloads": "TrainingOperatorReady",
    "spark_operator": "SparkOperatorReady",
    "codeflare_sdk": "RayReady",
    "ogx": "OGXReady",
}

_DSC_KEY_READY_CONDITION: dict[str, str] = {
    "dashboard": "DashboardReady",
    "workbenches": "WorkbenchesReady",
    "modelregistry": "ModelRegistryReady",
    "kserve": "KserveReady",
    "aipipelines": "AIPipelinesReady",
    "feastoperator": "FeastOperatorReady",
    "trainer": "TrainerReady",
    "trainingoperator": "TrainingOperatorReady",
    "ray": "RayReady",
    "mlflowoperator": "MLflowOperatorReady",
    "trustyai": "TrustyAIReady",
    "llamastackoperator": "LlamaStackOperatorReady",
    "ogx": "OGXReady",
    "sparkoperator": "SparkOperatorReady",
    "codeflare": "RayReady",
}


def _ready_conditions_for_smoke(smoke_id: str) -> list[str]:
    """DSC Ready conditions to wait for after patching Managed keys for *smoke_id*."""
    managed = _dsc_smoke_managed_components(smoke_id)
    conditions: list[str] = []
    primary = _SMOKE_READY_CONDITION.get(smoke_id, "")
    if primary:
        conditions.append(primary)
    for key in sorted(managed):
        ready_type = _DSC_KEY_READY_CONDITION.get(key)
        if ready_type and ready_type not in conditions:
            conditions.append(ready_type)
    return [cond for cond in conditions if cond in _dsc_condition_types()]


def _dsc_condition(condition_type: str) -> tuple[str, str, str]:
    path = (
        f'{{.status.conditions[?(@.type=="{condition_type}")].status}}'
        f'\t{{.status.conditions[?(@.type=="{condition_type}")].reason}}'
        f'\t{{.status.conditions[?(@.type=="{condition_type}")].message}}'
    )
    r = oc_run(
        ["get", "datasciencecluster", "default-dsc", "-o", f"jsonpath={path}"],
        check=False,
        capture_output=True,
        timeout=30,
    )
    parts = (r.stdout or "").strip().split("\t")
    while len(parts) < 3:
        parts.append("")
    return parts[0], parts[1], parts[2]


def _component_management_state(dsc_key: str) -> str:
    r = oc_run(
        [
            "get",
            "datasciencecluster",
            "default-dsc",
            "-o",
            f"jsonpath={{.spec.components.{dsc_key}.managementState}}",
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    return (r.stdout or "").strip()


def _models_as_service_management_state() -> str:
    r = oc_run(
        [
            "get",
            "datasciencecluster",
            "default-dsc",
            "-o",
            "jsonpath={.spec.components.kserve.modelsAsService.managementState}",
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    return (r.stdout or "").strip()


def dsc_disabled_reason_from_states(
    smoke_id: str,
    *,
    management_states: dict[str, str],
    models_as_service_state: str = "",
    ready_reasons: dict[str, str] | None = None,
) -> str:
    """Pure probe for unit tests. Empty string means not disabled."""
    ready_reasons = ready_reasons or {}
    ready_type = _SMOKE_READY_CONDITION.get(smoke_id, "")
    if ready_type and ready_reasons.get(ready_type) == "Removed":
        return f"{ready_type} reason=Removed"

    for key in _dsc_smoke_managed_components(smoke_id):
        if management_states.get(key) == "Removed":
            return f"spec.components.{key}.managementState=Removed"

    if components_need_models_as_service({smoke_id}):
        if models_as_service_state == "Removed":
            return "spec.components.kserve.modelsAsService.managementState=Removed"

    return ""


def wait_for_smoke_dsc_ready_after_patch(
    smoke_id: str,
    *,
    timeout_sec: int | None = None,
) -> None:
    """Wait after DSC Managed patch until Ready condition reconciles (reason=Removed may be stale)."""
    ready_types = _ready_conditions_for_smoke(smoke_id)
    if not ready_types:
        return
    timeout = timeout_sec
    if timeout is None:
        timeout = int(os.environ.get("OLMINSTALL_DSC_RECONCILE_WAIT_SEC", "600"))
    deadline = time.time() + timeout
    managed_keys = _dsc_smoke_managed_components(smoke_id)
    pending = set(ready_types)
    while time.time() < deadline:
        pending = {
            ready_type
            for ready_type in pending
            if _dsc_condition(ready_type)[0] != "True"
        }
        if not pending:
            print(f"✓ DSC ready for {smoke_id}: {', '.join(ready_types)}", flush=True)
            return
        if int(time.time()) % 60 < 12:
            details = []
            for ready_type in sorted(pending):
                status, reason, msg = _dsc_condition(ready_type)
                details.append(
                    f"{ready_type}={status or '?'} reason={reason or '?'}"
                    f" ({(msg or 'reconciling...')[:80]})"
                )
            print(
                f"Waiting for DSC ({smoke_id}): {'; '.join(details)}",
                flush=True,
            )
        for ready_type in list(pending):
            status, cond_reason, msg = _dsc_condition(ready_type)
            if (
                ready_type == "ModelsAsServiceReady"
                and cond_reason == "PrerequisitesNotMet"
            ):
                print(
                    f"WARN: skipping DSC wait for {smoke_id} ({ready_type} "
                    f"PrerequisitesNotMet: {(msg or 'infra blocked')[:120]})",
                    flush=True,
                )
                pending.discard(ready_type)
                continue
            if cond_reason == "Removed" and not any(
                _component_management_state(key) == "Managed" for key in managed_keys
            ):
                raise RuntimeError(
                    f"{ready_type} reason=Removed and DSC not Managed for {smoke_id}"
                )
        if not pending:
            print(f"✓ DSC ready for {smoke_id}: {', '.join(ready_types)}", flush=True)
            return
        time.sleep(12)
    details = []
    for ready_type in sorted(pending):
        status, reason, msg = _dsc_condition(ready_type)
        details.append(
            f"{ready_type} status={status or '?'} reason={reason or '?'}: "
            f"{(msg or 'reconcile incomplete')[:120]}"
        )
    raise RuntimeError(
        f"DSC not ready for {smoke_id} after {timeout}s ({'; '.join(details)})"
    )


def wait_for_smoke_dsc_ready_batch(
    component_ids: set[str],
    *,
    timeout_sec: int | None = None,
) -> None:
    """Wait once for union of DSC ready conditions after batch Managed patch."""
    if not component_ids:
        return
    ready_types: list[str] = []
    for smoke_id in sorted(component_ids):
        if smoke_id == "maas_billing":
            continue
        for cond in _ready_conditions_for_smoke(smoke_id):
            if cond not in ready_types:
                ready_types.append(cond)
    # ModelsAsServiceReady is deferred to maas_billing per-component prep (maas-api wait).
    if not ready_types:
        return
    timeout = timeout_sec
    if timeout is None:
        timeout = int(os.environ.get("OLMINSTALL_DSC_RECONCILE_WAIT_SEC", "600"))
    deadline = time.time() + timeout
    pending = set(ready_types)
    while time.time() < deadline:
        pending = {
            ready_type
            for ready_type in pending
            if _dsc_condition(ready_type)[0] != "True"
        }
        if not pending:
            print(f"✓ DSC ready for batch prep: {', '.join(ready_types)}", flush=True)
            return
        if int(time.time()) % 60 < 12:
            details = []
            for ready_type in sorted(pending):
                status, reason, msg = _dsc_condition(ready_type)
                details.append(
                    f"{ready_type}={status or '?'} reason={reason or '?'}"
                    f" ({(msg or 'reconciling...')[:80]})"
                )
            print(f"Waiting for DSC batch: {'; '.join(details)}", flush=True)
        for ready_type in list(pending):
            _, cond_reason, msg = _dsc_condition(ready_type)
            if ready_type == "ModelsAsServiceReady" and cond_reason == "PrerequisitesNotMet":
                print(
                    f"WARN: skipping batch DSC wait for {ready_type} "
                    f"PrerequisitesNotMet: {(msg or 'infra blocked')[:120]}",
                    flush=True,
                )
                pending.discard(ready_type)
        if not pending:
            print(f"✓ DSC ready for batch prep: {', '.join(ready_types)}", flush=True)
            return
        time.sleep(12)
    details = []
    for ready_type in sorted(pending):
        status, reason, msg = _dsc_condition(ready_type)
        details.append(
            f"{ready_type} status={status or '?'} reason={reason or '?'}: "
            f"{(msg or 'reconcile incomplete')[:120]}"
        )
    raise RuntimeError(
        f"DSC not ready for batch component prep after {timeout}s ({'; '.join(details)})"
    )


def smoke_component_dsc_disabled(smoke_id: str) -> tuple[bool, str]:
    """Return (disabled, reason) when DSC has the smoke component Removed."""
    try:
        probe = oc_run(
            ["get", "datasciencecluster", "default-dsc"],
            check=False,
            capture_output=True,
            timeout=15,
        )
    except AppError as exc:
        print(f"WARN: skipping DSC disabled probe for {smoke_id}: {exc}", file=sys.stderr, flush=True)
        return False, ""
    if probe.returncode != 0:
        api_reason = cluster_api_unreachable_text(
            stderr=probe.stderr or "",
            stdout=probe.stdout or "",
        )
        if api_reason:
            return True, api_reason
        return False, ""

    ready_type = _SMOKE_READY_CONDITION.get(smoke_id, "")
    if ready_type:
        _, reason, _ = _dsc_condition(ready_type)
        if reason == "Removed":
            return True, f"{ready_type} reason=Removed"

    for key in _dsc_smoke_managed_components(
        smoke_id,
        operator_version=_resolve_operator_version_for_dsc(),
    ):
        if _component_management_state(key) == "Removed":
            return True, f"spec.components.{key}.managementState=Removed"

    if components_need_models_as_service({smoke_id}):
        if _models_as_service_management_state() == "Removed":
            return True, "spec.components.kserve.modelsAsService.managementState=Removed"

    return False, ""


def _ready_condition_detail(ready_type: str, *, status: str, reason: str, message: str) -> str:
    detail = f"{ready_type} status={status or '?'} reason={reason or '?'}"
    if message:
        detail = f"{detail}: {message[:200]}"
    return detail


def smoke_component_prereq_reason_from_states(
    smoke_id: str,
    *,
    management_states: dict[str, str],
    models_as_service_state: str = "",
    ready_status: str = "",
    ready_reason: str = "",
    ready_message: str = "",
    exposes_ready_condition: bool = True,
    crd_present: bool = False,
    maas_deps_ready: bool = True,
    maas_prereq_status: str = "",
    maas_status: str = "",
    dsc_ready_status: str = "",
    require_maas_prereq_condition: bool = False,
    gateway_url: str = "",
    gateway_url_reachable: bool = False,
) -> str:
    """Pure probe for unit tests. Non-empty means smoke should fail without pytest."""
    disabled = dsc_disabled_reason_from_states(
        smoke_id,
        management_states=management_states,
        models_as_service_state=models_as_service_state,
        ready_reasons={_SMOKE_READY_CONDITION.get(smoke_id, ""): ready_reason}
        if ready_reason
        else {},
    )
    if disabled:
        return disabled

    if components_need_models_as_service({smoke_id}):
        if not maas_deps_ready:
            return (
                "Kuadrant/Authorino dependency operators are missing "
                "(expected install-dep-operators / setup-dependencies.sh)"
            )
        if not _maas_smoke_ready(
            prereq_status=maas_prereq_status,
            maas_status=maas_status,
            ready_status=dsc_ready_status,
            require_prereq_condition=require_maas_prereq_condition,
        ):
            prereq_display = maas_prereq_status if require_maas_prereq_condition else "n/a"
            return (
                "MaaS smoke prerequisites not ready "
                f"(MaaSPrerequisites={prereq_display or '?'}, "
                f"ModelsAsService={maas_status or '?'}, DSC Ready={dsc_ready_status or '?'})"
            )

    ready_type = _SMOKE_READY_CONDITION.get(smoke_id, "")
    if smoke_id == "dashboard_cypress":
        if gateway_url_reachable:
            return ""
        if not gateway_url:
            return "dashboard gateway URL not resolved (consolelink/rh-ai route)"
        return f"dashboard gateway not reachable at {gateway_url}"
    if not ready_type:
        return ""

    if ready_reason == "Removed":
        return f"{ready_type} reason=Removed"

    if exposes_ready_condition:
        if ready_status == "True":
            return ""
        return _ready_condition_detail(
            ready_type,
            status=ready_status,
            reason=ready_reason,
            message=ready_message,
        )

    if smoke_id == "llama_stack":
        if crd_present:
            return ""
        return (
            f"{_LLAMA_STACK_CRD} CRD not found "
            f"({ready_type} condition not exposed on DSC)"
        )

    return ""


def _dashboard_cypress_gateway_unavailable_reason() -> str:
    """Gateway HTTP preflight gate for dashboard Cypress orchestrate skip."""
    from components.dashboard_cypress.config import resolve_odh_dashboard_base_url
    from components.dashboard_cypress.verify_route import dashboard_cypress_accessible_for_smoke

    url = resolve_odh_dashboard_base_url()
    if not url:
        return "dashboard gateway URL not resolved (consolelink/rh-ai route)"
    if dashboard_cypress_accessible_for_smoke(url=url):
        return ""
    return f"dashboard gateway not reachable at {url}"


def _maas_prereq_unavailable_reason(smoke_id: str) -> str:
    del smoke_id  # MaaS prereqs are shared across MaaS smoke components.
    from components.maas_billing.common import (
        deps_only_install_dependencies_smoke,
        maas_smoke_acceptable_for_run,
    )

    if deps_only_install_dependencies_smoke():
        from components.maas_billing.common import maas_functional_smoke_ready

        ready, reason = maas_functional_smoke_ready()
        return "" if ready else reason

    acceptable, reason = maas_smoke_acceptable_for_run()
    return "" if acceptable else reason


def smoke_component_prereq_unavailable(smoke_id: str) -> tuple[bool, str]:
    """Return (unavailable, reason) when a component should fail smoke without pytest."""
    api_reason = cluster_smoke_infra_blocked_reason()
    if api_reason:
        return True, api_reason

    disabled, reason = smoke_component_dsc_disabled(smoke_id)
    if disabled:
        return True, reason

    if components_need_models_as_service({smoke_id}):
        maas_reason = _maas_prereq_unavailable_reason(smoke_id)
        if maas_reason:
            return True, maas_reason

    if smoke_id == "dashboard_cypress":
        gateway_reason = _dashboard_cypress_gateway_unavailable_reason()
        if gateway_reason:
            return True, gateway_reason
        return False, ""

    ready_type = _SMOKE_READY_CONDITION.get(smoke_id, "")
    if not ready_type:
        return False, ""

    _, reason, msg = _dsc_condition(ready_type)
    if reason == "Removed":
        return True, f"{ready_type} reason=Removed"

    if ready_type in _dsc_condition_types():
        status, cond_reason, cond_msg = _dsc_condition(ready_type)
        if status == "True":
            return False, ""
        return True, _ready_condition_detail(
            ready_type,
            status=status,
            reason=cond_reason,
            message=cond_msg,
        )

    if smoke_id == "llama_stack":
        if llama_stack_crd_present():
            return False, ""
        return True, (
            f"{_LLAMA_STACK_CRD} CRD not found "
            f"({ready_type} condition not exposed on DSC)"
        )

    return False, ""


def smoke_llama_stack_operator_unavailable() -> tuple[bool, str]:
    return smoke_component_prereq_unavailable("llama_stack")


def llama_stack_smoke_prereq_reason_from_states(**kwargs) -> str:
    return smoke_component_prereq_reason_from_states("llama_stack", **kwargs)
