"""Cluster registry mirror + pull-secret prep for RHOAI/ODH olminstall."""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from k8s.oc_util import run_oc
from suite.errors import AppError

OLM_BUNDLE_UNPACK_UTILITY_IMAGE = (
    "quay.io/openshift-release-dev/ocp-v4.0-art-dev"
)


def extract_quay_auth(auths: dict[str, Any]) -> str | None:
    for key in ("quay.io/rhoai", "quay.io/rhoai/rhoai-fbc-fragment"):
        ent = auths.get(key) or {}
        auth = ent.get("auth")
        if auth:
            return str(auth)
    for k, v in auths.items():
        if k.startswith("quay.io/rhoai/") and isinstance(v, dict) and v.get("auth"):
            return str(v["auth"])
    # Legacy mounted secrets may still list bare quay.io — use only for token discovery,
    # never write it back under quay.io in the global pull secret.
    ent = auths.get("quay.io") or {}
    auth = ent.get("auth")
    if auth:
        return str(auth)
    return None


def rhoai_scoped_dockerconfig(quay: dict[str, Any]) -> dict[str, Any]:
    """Return dockerconfig with only quay.io/rhoai* auths (never bare quay.io)."""
    auths = quay.get("auths") or {}
    rhoai_entries = {
        k: v
        for k, v in auths.items()
        if k == "quay.io/rhoai" or k.startswith("quay.io/rhoai/")
    }
    quay_auth = extract_quay_auth(auths)
    if quay_auth:
        rhoai_entries.setdefault("quay.io/rhoai", {"auth": quay_auth})
    return {"auths": rhoai_entries}


def merge_docker_auths(existing: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    e_auth = dict(existing.get("auths") or {})
    o_auth = dict(overlay.get("auths") or {})
    out = dict(existing)
    out["auths"] = {**e_auth, **o_auth}
    return out


def dockerconfig_pull_secret_apply_manifest(name: str, namespace: str, dockerconfig_json: str) -> str:
    obj = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name, "namespace": namespace},
        "type": "kubernetes.io/dockerconfigjson",
        "stringData": {".dockerconfigjson": dockerconfig_json},
    }
    return json.dumps(obj)


def _strip_secret_metadata(obj: dict[str, Any]) -> dict[str, Any]:
    md = dict(obj.get("metadata") or {})
    for k in ("uid", "resourceVersion", "creationTimestamp", "managedFields", "ownerReferences", "generation"):
        md.pop(k, None)
    md.pop("selfLink", None)
    ann = dict(md.get("annotations") or {})
    ann.pop("kubectl.kubernetes.io/last-applied-configuration", None)
    if ann:
        md["annotations"] = ann
    else:
        md.pop("annotations", None)
    obj["metadata"] = md
    return obj


def load_global_pull_secret_auths() -> dict[str, Any]:
    raw = run_oc(["get", "secret/pull-secret", "-n", "openshift-config", "-o", "json"]).stdout
    pull_data = json.loads(raw)
    b64 = pull_data["data"][".dockerconfigjson"]
    existing = json.loads(base64.standard_b64decode(b64))
    return dict(existing.get("auths") or {})


def merge_rhoai_into_global_pull_secret(quay: dict[str, Any]) -> None:
    """Merge only quay.io/rhoai* keys into openshift-config/pull-secret."""
    print("Patching cluster global pull secret with quay.io/rhoai credentials...")
    raw = run_oc(["get", "secret/pull-secret", "-n", "openshift-config", "-o", "json"]).stdout
    pull_data = json.loads(raw)
    b64 = pull_data["data"][".dockerconfigjson"]
    existing = json.loads(base64.standard_b64decode(b64))
    overlay = rhoai_scoped_dockerconfig(quay)
    if not overlay.get("auths"):
        raise AppError("No quay.io/rhoai credentials to merge into global pull secret", code=1)
    merged = merge_docker_auths(existing, overlay)
    merged_raw = json.dumps(merged, separators=(",", ":")).encode()
    patch_b64 = base64.standard_b64encode(merged_raw).decode("ascii")
    obj = dict(pull_data)
    obj.setdefault("data", {})[".dockerconfigjson"] = patch_b64
    _strip_secret_metadata(obj)
    run_oc(
        ["apply", "-f", "-"],
        stdin_text=json.dumps(obj),
        check=True,
        capture_output=True,
        timeout=120,
    )
    print("✓ Global pull secret patched (quay.io/rhoai* only)")


def ensure_additional_pull_secret(quay: dict[str, Any]) -> None:
    auths = quay.get("auths") or {}
    quay_auth = extract_quay_auth(auths)
    if not quay_auth:
        raise AppError("No quay.io/rhoai auth for additional-pull-secret", code=1)
    print("Creating additional-pull-secret in kube-system (triggers HyperShift HCCO node sync)...")
    rhoai_entries = {k: v for k, v in auths.items() if k.startswith("quay.io/rhoai")}
    rhoai_auths = dict(rhoai_entries)
    rhoai_auths.setdefault("quay.io/rhoai", {"auth": quay_auth})
    creds_json = json.dumps({"auths": rhoai_auths}, separators=(",", ":"))
    run_oc(
        ["apply", "-f", "-"],
        stdin_text=dockerconfig_pull_secret_apply_manifest("additional-pull-secret", "kube-system", creds_json),
        check=True,
    )
    print("✓ additional-pull-secret created in kube-system")


def preflight_openshift_release_dev_pull(*, strict: bool) -> None:
    """OLM bundle-unpack uses quay.io/openshift-release-dev via the cluster quay.io pull cred."""
    auths = load_global_pull_secret_auths()
    ent = auths.get("quay.io") or {}
    if ent.get("auth"):
        print(f"✓ Global pull-secret has quay.io credential ({OLM_BUNDLE_UNPACK_UTILITY_IMAGE})")
        return
    msg = (
        "Cluster openshift-config/pull-secret is missing a broad quay.io credential required for "
        f"OLM bundle-unpack ({OLM_BUNDLE_UNPACK_UTILITY_IMAGE}). "
        "Merge a valid pull-secret from console.redhat.com (do not replace with RHOAI-only robot auth)."
    )
    if strict:
        raise AppError(f"❌ {msg}", code=1)
    print(f"WARN: {msg}")


def full_pull_setup_requested(product: str, quay_path: str) -> bool:
    if product.strip().lower() == "existing":
        return False
    if not os.environ.get("QUAY_PULL_SECRET_NAME", "").strip():
        return False
    return bool(quay_path) and os.path.isfile(quay_path)


def ensure_cluster_registry_for_rhoai(
    quay: dict[str, Any] | None,
    *,
    product: str,
    quay_path: str = "",
) -> None:
    """Idempotent registry prep: IDMS/Kyverno, safe pull-secret merge, unpack preflight."""
    from install.rosa_hcp_imagestream_mirror import ensure_rosa_hcp_imagestream_mirror
    from install.rosa_hcp_pull_setup import ensure_rosa_hcp_pull_setup, is_hypershift_managed_cluster

    product_l = product.strip().lower()
    strict_preflight = product_l in ("rhoai", "odh")
    do_full_pull = quay is not None and full_pull_setup_requested(product_l, quay_path)

    if is_hypershift_managed_cluster():
        if do_full_pull and quay is not None:
            ensure_rosa_hcp_pull_setup(quay)
        else:
            ensure_rosa_hcp_imagestream_mirror()

    if do_full_pull and quay is not None:
        merge_rhoai_into_global_pull_secret(quay)
        ensure_additional_pull_secret(quay)

    from install.install_and_verify import ensure_rhoai_registry_access

    ensure_rhoai_registry_access()
    preflight_openshift_release_dev_pull(strict=strict_preflight)
