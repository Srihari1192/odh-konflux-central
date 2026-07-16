"""MaaS smoke surface prep (gateway, DB, auth). Shared by prepare and install-dep-operators."""

from __future__ import annotations

from components.maas_billing.auth import (
    ensure_maas_auth_policy_ready,
    ensure_maas_authorino_ready,
)
from components.maas_billing.auth import ensure_authorino_tls
from components.maas_billing.bbr_pre_processing import (
    ensure_maas_bbr_pre_processing,
    repair_payload_pre_processing_selector_conflict,
)
from components.maas_billing.common import maas_api_deployment_exists
from components.maas_billing.cluster_cleanup import (
    cleanup_maas_smoke_leaked_rbac,
    cleanup_maas_smoke_stale_gateway_leaks,
    ensure_maas_gateway_auth_policy_alias,
)
from components.maas_billing.database import ensure_maas_database
from components.maas_billing.gateway import (
    ensure_maas_api_auth_policy,
    ensure_maas_gateway,
    ensure_maas_gateway_ingress_tls_secret,
    ensure_maas_gateway_route,
)
from components.maas_billing.timeouts import (
    maas_gateway_prep_programmed_wait_sec,
    maas_prep_timeout_sec,
)
from components.maas_billing.uwm import ensure_user_workload_monitoring
from components.maas_billing.wait import (
    _wait_for_maas_smoke_ready,
    _wait_maas_gateway_https_for_models_as_service,
)
from install.dependency_operators import (
    existing_smoke_without_install_dependencies,
    require_maas_dependency_operators,
)
from install.dsc_install import ensure_dsc_models_as_service
from install.rhcl_deps import ensure_maas_rhcl_dependency_stack
from steps.cluster_prep_state import (
    dep_operators_already_done,
    maas_gateway_mas_already_done,
    maas_smoke_prep_attempted,
    maas_smoke_surface_already_done,
    mark_maas_gateway_mas_done,
    mark_maas_smoke_prep_attempted,
    mark_maas_smoke_surface_done,
)


def _restart_maas_api_after_gateway() -> None:
    """Restart maas-api when it already exists so it picks up the gateway HTTPS service."""
    if not maas_api_deployment_exists():
        return
    from components.maas_billing.auth import _rollout_restart_deployment
    from components.maas_billing.common import _MAAS_APPS_NS

    _rollout_restart_deployment(_MAAS_APPS_NS, "maas-api", timeout_sec=120)
    print("✓ Restarted maas-api after gateway HTTPS service prep", flush=True)


def ensure_maas_gateway_before_models_as_service(*, https_wait_sec: int | None = None) -> None:
    """Gateway HTTPS service must exist before modelsAsService enables maas-api."""
    from components.maas_billing.auth import recover_kuadrant_after_gateway_api_provider
    from steps.cluster_prep_state import maas_gateway_https_blocked_reason

    # cleanup+reinstall: Kuadrant often stuck MissingDependency until GatewayClass exists;
    # restart operator once provider is present so MaaS smoke can run.
    recover_kuadrant_after_gateway_api_provider()
    blocked = maas_gateway_https_blocked_reason()
    if blocked:
        raise RuntimeError(blocked)
    if maas_gateway_mas_already_done():
        print("Skipping duplicate MaaS gateway/modelsAsService prep (already done this run)", flush=True)
        return
    ensure_maas_gateway_ingress_tls_secret()
    ensure_authorino_tls()
    ensure_maas_gateway()
    ensure_maas_gateway_route()
    timeout = https_wait_sec if https_wait_sec is not None else maas_gateway_prep_programmed_wait_sec()
    _wait_maas_gateway_https_for_models_as_service(timeout_sec=timeout)
    ensure_dsc_models_as_service()
    _restart_maas_api_after_gateway()
    mark_maas_gateway_mas_done()


def try_prepare_maas_smoke() -> None:
    """MaaS smoke surface prep (gateway, DB, auth policies). RHCL runs in install-dep-operators."""
    from install.dsc_install import dsc_crd_available

    if not dsc_crd_available():
        print(
            "NOTE: skipping MaaS smoke prep until DataScienceCluster CRD exists (post install-rhoai)",
            flush=True,
        )
        return
    repaired = repair_payload_pre_processing_selector_conflict()
    if maas_smoke_surface_already_done() and not repaired:
        print("Skipping duplicate MaaS smoke prep (surface already prepared this run)", flush=True)
        return
    if maas_smoke_prep_attempted() and not repaired:
        print(
            "Skipping duplicate MaaS smoke prep (auth/readiness wait already attempted this run)",
            flush=True,
        )
        return
    if not dep_operators_already_done():
        if existing_smoke_without_install_dependencies():
            require_maas_dependency_operators()
        else:
            ensure_maas_rhcl_dependency_stack()
            require_maas_dependency_operators()
    else:
        print(
            "Skipping RHCL/dependency-operator setup (install-dep-operators already completed)",
            flush=True,
        )
        # After cleanup+reinstall, install-dep-operators may leave a stale incomplete
        # marker (Kuadrant race before RHOAI). Re-probe live stack; if still incomplete,
        # retry RHCL post-install now that the operator apps namespace exists.
        from helpers.gateway_stack_marker import (
            gateway_stack_incomplete,
            reconcile_gateway_stack_incomplete_marker,
        )

        if gateway_stack_incomplete() and not reconcile_gateway_stack_incomplete_marker():
            print(
                "Retrying RHCL/Kuadrant post-install after install-dep incomplete marker "
                "(post install-rhoai / DSC available)...",
                flush=True,
            )
            ensure_maas_rhcl_dependency_stack()
            require_maas_dependency_operators(allow_deferred_authorino=True)
    ensure_maas_gateway_before_models_as_service()
    ensure_maas_database()
    cleanup_maas_smoke_leaked_rbac()
    cleanup_maas_smoke_stale_gateway_leaks()
    ensure_maas_bbr_pre_processing()
    ensure_user_workload_monitoring()
    authorino_ns = ensure_maas_authorino_ready()
    if not maas_api_deployment_exists():
        print(
            "WARN: maas-api deployment not present yet (operator workloads still reconciling); "
            "deferring MaaS API auth policy and readiness wait to prepare-components-prerequisites",
            flush=True,
        )
        return
    try:
        ensure_maas_api_auth_policy()
        ensure_maas_gateway_auth_policy_alias()
        ensure_maas_auth_policy_ready(authorino_ns=authorino_ns)
        _wait_for_maas_smoke_ready(timeout_sec=maas_prep_timeout_sec())
        mark_maas_smoke_surface_done()
    finally:
        mark_maas_smoke_prep_attempted()
