"""MaaS cluster-prep wait timeouts (env overrides for Tekton)."""

from __future__ import annotations

import os

_DEFAULT_MAAS_PREP_TIMEOUT_SEC = 900
_DEFAULT_MAAS_RESYNC_TIMEOUT_SEC = 300
_DEFAULT_MAAS_DSC_PREREQ_GRACE_SEC = 120
_DEFAULT_MAAS_GATEWAY_PROGRAMMED_WAIT_SEC = 600
_DEFAULT_MAAS_GATEWAY_HTTPS_WAIT_SEC = 120
_DEFAULT_MAAS_GATEWAY_PREP_PROGRAMMED_WAIT_SEC = 300
_DEFAULT_MAAS_GATEWAY_PREP_PROGRAMMED_WAIT_SEC_EAAS = 480


def maas_prep_timeout_sec() -> int:
    return int(os.environ.get("MAAS_PREP_TIMEOUT_SEC", str(_DEFAULT_MAAS_PREP_TIMEOUT_SEC)))


def maas_resync_timeout_sec() -> int:
    return int(os.environ.get("MAAS_RESYNC_TIMEOUT_SEC", str(_DEFAULT_MAAS_RESYNC_TIMEOUT_SEC)))


def maas_dsc_prereq_grace_sec() -> int:
    return int(os.environ.get("MAAS_DSC_PREREQ_GRACE_SEC", str(_DEFAULT_MAAS_DSC_PREREQ_GRACE_SEC)))


def maas_gateway_programmed_wait_sec() -> int:
    return int(
        os.environ.get(
            "MAAS_GATEWAY_PROGRAMMED_WAIT_SEC",
            str(_DEFAULT_MAAS_GATEWAY_PROGRAMMED_WAIT_SEC),
        )
    )


def maas_gateway_https_wait_sec() -> int:
    """Bounded wait for gateway-owned HTTPS service before enabling modelsAsService."""
    return int(
        os.environ.get(
            "MAAS_GATEWAY_HTTPS_WAIT_SEC",
            str(_DEFAULT_MAAS_GATEWAY_HTTPS_WAIT_SEC),
        )
    )


def maas_gateway_prep_programmed_wait_sec() -> int:
    """Wait for Gateway Programmed before HTTPS/modelsAsService (EaaS reconciles slowly)."""
    from install.gateway_config import cluster_source_is_eaas

    default = (
        _DEFAULT_MAAS_GATEWAY_PREP_PROGRAMMED_WAIT_SEC_EAAS
        if cluster_source_is_eaas()
        else _DEFAULT_MAAS_GATEWAY_PREP_PROGRAMMED_WAIT_SEC
    )
    return int(
        os.environ.get(
            "MAAS_GATEWAY_PREP_PROGRAMMED_WAIT_SEC",
            str(default),
        )
    )


def bvt_dsc_ready_timeout_sec() -> int:
    """Wait for DSC Ready before operator_health BVT (pytest only allows 120s)."""
    return int(os.environ.get("BVT_DSC_READY_TIMEOUT_SEC", "600"))
