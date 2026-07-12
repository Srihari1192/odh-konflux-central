"""Cluster label helpers (Jenkins getClusterNameFromUrl parity)."""

from __future__ import annotations

import pytest

from install.kubeconfig_cluster_label import (
    _sanitize_cluster_label,
    cluster_name_from_url,
)

@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://api.rmanos-konfluxl-dunx.p3.openshiftapps.com:6443",
            "rmanos-konfluxl-dunx",
        ),
        (
            "https://console-openshift-console.apps.ods-qe-psi-09.osp.rh-ods.com/",
            "ods-qe-psi-09",
        ),
        ("https://api.ods-qe-psi-09.osp.rh-ods.com", "ods-qe-psi-09"),
        ("", ""),
        ("https://example.com/nope", ""),
    ],
)
def test_cluster_name_from_url(url: str, expected: str) -> None:
    assert cluster_name_from_url(url) == expected

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "api-rmanos-konfluxl-dunx-p3-openshiftapps-com:443",
            "rmanos-konfluxl-dunx",
        ),
        (
            "default/api-ods-qe-psi-09-osp-rh-ods-com:6443/kube:admin",
            "ods-qe-psi-09",
        ),
        (
            "https://api.rmanos-konfluxl-dunx.p3.openshiftapps.com:6443",
            "rmanos-konfluxl-dunx",
        ),
    ],
)
def test_sanitize_cluster_label(raw: str, expected: str) -> None:
    assert _sanitize_cluster_label(raw) == expected
