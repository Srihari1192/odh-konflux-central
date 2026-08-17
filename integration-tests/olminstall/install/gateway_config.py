"""RHOAI GatewayConfig OIDC + readiness (Jenkins Verify RHODS / Patch GatewayConfig)."""

from __future__ import annotations

import base64
import json
import os
import sys
import time

from install.dsc_install import oc_run
from install.ldap import _cluster_is_byoidc
from suite.its_trigger_params import CLUSTER_SOURCE_EAAS

_GATEWAY_NAME = "default-gateway"
_OIDC_SECRET_NAME = "keycloak-client-secret"
_OIDC_SECRET_NS = "openshift-ingress"
_OIDC_SECRET_KEY = "clientSecret"
_DEFAULT_OIDC_CLIENT_ID = "odh-client"
_AUTH_NAME = "auth"
_READY_CONDITIONS = ("Ready", "ProvisioningSucceeded", "GatewayConfigReady")
_SERVICEMESH_CSV_PREFIX = "servicemeshoperator"
_KUBE_AUTH_PROXY_NS = "openshift-ingress"
_KUBE_AUTH_PROXY_DEPLOY = "kube-auth-proxy"
_KUBE_AUTH_PROXY_CREDS = "kube-auth-proxy-creds"


def cluster_source_is_eaas() -> bool:
    source = os.environ.get("CLUSTER_SOURCE", "").strip()
    return source in ("", CLUSTER_SOURCE_EAAS)


def gateway_oidc_configured() -> bool:
    """True when GatewayConfig spec.oidc has a usable issuer and client ID."""
    doc = _gateway_config_doc()
    if not doc:
        return False
    existing = ((doc.get("spec") or {}).get("oidc") or {})
    issuer = str(existing.get("issuerURL") or "").strip()
    client_id = str(existing.get("clientID") or "").strip()
    return bool(issuer) and bool(client_id) and not _malformed_oidc_client_id(client_id)


def _wait_for_byoidc_cluster_signals(*, retries: int = 24, delay_sec: float = 15.0) -> bool:
    """EaaS may expose ``oidc/byoidc-credentials`` after gateway CR is Ready."""
    for attempt in range(retries):
        if _cluster_is_byoidc():
            return True
        if attempt + 1 < retries:
            time.sleep(delay_sec)
    return _cluster_is_byoidc()


def _resolve_byoidc_for_gateway() -> bool:
    if _cluster_is_byoidc():
        return True
    if not cluster_source_is_eaas():
        return False
    print(
        "EaaS cluster: waiting for BYOIDC issuer/credentials before GatewayConfig OIDC patch...",
        flush=True,
    )
    return _wait_for_byoidc_cluster_signals()


def _byoidc_issuer_url() -> str:
    r = oc_run(
        [
            "get",
            "authentication",
            "cluster",
            "-o",
            "jsonpath={.spec.oidcProviders[0].issuer.issuerURL}",
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if r.returncode != 0:
        return ""
    return (r.stdout or "").strip()


def _gateway_oidc_audiences() -> list[str]:
    """Return individual OIDC audience strings from cluster Authentication."""
    r = oc_run(
        [
            "get",
            "authentication",
            "cluster",
            "-o",
            "jsonpath={.spec.oidcProviders[0].issuer.audiences[*]}",
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if r.returncode != 0:
        return []
    return [a for a in (r.stdout or "").split() if a]


def _gateway_oidc_client_id() -> str:
    env_id = os.environ.get("GATEWAY_OIDC_CLIENT_ID", "").strip()
    if env_id:
        return env_id
    audiences = _gateway_oidc_audiences()
    for audience in audiences:
        if audience == _DEFAULT_OIDC_CLIENT_ID:
            return _DEFAULT_OIDC_CLIENT_ID
    if audiences:
        if _cluster_is_byoidc():
            print(
                f"Using BYOIDC audience {audiences[0]!r} for GatewayConfig clientID",
                flush=True,
            )
            return audiences[0]
        return _DEFAULT_OIDC_CLIENT_ID
    if _cluster_is_byoidc():
        raise RuntimeError(
            "BYOIDC cluster has no OIDC audiences on Authentication; "
            "set GATEWAY_OIDC_CLIENT_ID to the issuer client ID."
        )
    return _DEFAULT_OIDC_CLIENT_ID


def _malformed_oidc_client_id(client_id: str) -> bool:
    cid = (client_id or "").strip()
    return not cid or any(ch in cid for ch in ('[', ']', ',', '"'))


def _kube_auth_proxy_client_id() -> str:
    r = oc_run(
        [
            "get",
            "secret",
            _KUBE_AUTH_PROXY_CREDS,
            "-n",
            _KUBE_AUTH_PROXY_NS,
            "-o",
            "jsonpath={.data.OAUTH2_PROXY_CLIENT_ID}",
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if r.returncode != 0 or not (r.stdout or "").strip():
        return ""
    try:
        return base64.b64decode((r.stdout or "").strip()).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return ""


def _rollout_restart_kube_auth_proxy(*, timeout_sec: int = 180) -> None:
    r = oc_run(
        [
            "rollout",
            "restart",
            f"deployment/{_KUBE_AUTH_PROXY_DEPLOY}",
            "-n",
            _KUBE_AUTH_PROXY_NS,
        ],
        check=False,
        capture_output=True,
        timeout=60,
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        print(f"WARN: kube-auth-proxy rollout restart failed: {err}", file=sys.stderr)
        return
    oc_run(
        [
            "rollout",
            "status",
            f"deployment/{_KUBE_AUTH_PROXY_DEPLOY}",
            "-n",
            _KUBE_AUTH_PROXY_NS,
            f"--timeout={timeout_sec}s",
        ],
        check=False,
        capture_output=True,
        timeout=timeout_sec + 30,
    )
    print(f"✓ Restarted deployment/{_KUBE_AUTH_PROXY_DEPLOY} in {_KUBE_AUTH_PROXY_NS}", flush=True)


def sync_kube_auth_proxy_oidc_client(client_id: str) -> bool:
    """Align kube-auth-proxy OAuth client ID with GatewayConfig (operator may leave a JSON array)."""
    if not _cluster_is_byoidc():
        return False
    current = _kube_auth_proxy_client_id()
    if current == client_id and not _malformed_oidc_client_id(current):
        return False
    patch_doc = {"stringData": {"OAUTH2_PROXY_CLIENT_ID": client_id}}
    r = oc_run(
        [
            "patch",
            "secret",
            _KUBE_AUTH_PROXY_CREDS,
            "-n",
            _KUBE_AUTH_PROXY_NS,
            "--type=merge",
            "-p",
            json.dumps(patch_doc),
        ],
        check=False,
        capture_output=True,
        timeout=60,
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        raise RuntimeError(f"kube-auth-proxy client ID patch failed: {err or 'unknown error'}")
    print(
        f"✓ Patched {_KUBE_AUTH_PROXY_CREDS} OAUTH2_PROXY_CLIENT_ID={client_id!r}",
        flush=True,
    )
    _rollout_restart_kube_auth_proxy()
    return True


def _gateway_config_doc() -> dict | None:
    r = oc_run(
        ["get", "gatewayconfig", _GATEWAY_NAME, "-o", "json"],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if r.returncode != 0 or not (r.stdout or "").strip():
        return None
    try:
        doc = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    return doc if isinstance(doc, dict) else None


def _condition_status(doc: dict, condition_type: str) -> str:
    for item in (doc.get("status") or {}).get("conditions") or []:
        if isinstance(item, dict) and item.get("type") == condition_type:
            return str(item.get("status") or "")
    return ""


def gateway_config_ready() -> bool:
    doc = _gateway_config_doc()
    if not doc:
        return False
    return all(_condition_status(doc, name) == "True" for name in _READY_CONDITIONS)


def patch_gateway_config_oidc() -> bool:
    """Patch cluster GatewayConfig with external OIDC settings on BYOIDC clusters."""
    if not _resolve_byoidc_for_gateway():
        print("✓ Cluster not BYOIDC — skipping GatewayConfig OIDC patch", flush=True)
        return False
    issuer = _byoidc_issuer_url()
    if not issuer:
        print("WARN: BYOIDC cluster but issuer URL missing; skip GatewayConfig OIDC", file=sys.stderr)
        return False

    doc = _gateway_config_doc()
    existing = ((doc or {}).get("spec") or {}).get("oidc") or {}
    client_id = _gateway_oidc_client_id()
    existing_client_id = str(existing.get("clientID") or "")
    gateway_ok = (
        existing.get("issuerURL") == issuer
        and existing_client_id == client_id
        and not _malformed_oidc_client_id(existing_client_id)
        and (existing.get("clientSecretRef") or {}).get("name") == _OIDC_SECRET_NAME
    )
    changed = False
    if gateway_ok:
        print("✓ GatewayConfig OIDC already configured", flush=True)
    else:
        patch_doc = {
            "spec": {
                "oidc": {
                    "issuerURL": issuer,
                    "clientID": client_id,
                    "clientSecretRef": {
                        "name": _OIDC_SECRET_NAME,
                        "key": _OIDC_SECRET_KEY,
                        "namespace": _OIDC_SECRET_NS,
                    },
                }
            }
        }
        r = oc_run(
            [
                "patch",
                "gatewayconfig",
                _GATEWAY_NAME,
                "--type=merge",
                "-p",
                json.dumps(patch_doc),
            ],
            check=False,
            capture_output=True,
            timeout=60,
        )
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            raise RuntimeError(f"GatewayConfig OIDC patch failed: {err or 'unknown error'}")
        print(
            f"✓ Patched GatewayConfig/{_GATEWAY_NAME} OIDC (issuer={issuer}, clientID={client_id})",
            flush=True,
        )
        changed = True
    if sync_kube_auth_proxy_oidc_client(client_id):
        changed = True
    return changed


def configure_auth_cr_groups() -> bool:
    """Ensure Auth CR allows authenticated OIDC users (Jenkins Configure OIDC Auth CR Groups)."""
    r = oc_run(["get", "auth", _AUTH_NAME, "-o", "json"], check=False, capture_output=True, timeout=30)
    if r.returncode != 0:
        print(f"WARN: Auth/{_AUTH_NAME} not found; skip group patch", file=sys.stderr)
        return False
    try:
        doc = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return False
    spec = doc.get("spec") or {}
    allowed = list(spec.get("allowedGroups") or [])
    admin = list(spec.get("adminGroups") or [])
    changed = False
    if "system:authenticated" not in allowed:
        allowed.append("system:authenticated")
        changed = True
    if "rhods-admins" not in admin:
        admin.append("rhods-admins")
        changed = True
    extra_allowed = os.environ.get("GATEWAY_AUTH_ALLOWED_GROUPS", "").strip()
    if extra_allowed:
        for group in (g.strip() for g in extra_allowed.split(",") if g.strip()):
            if group not in allowed:
                allowed.append(group)
                changed = True
    if not changed:
        return False
    patch_doc = {"spec": {"allowedGroups": allowed, "adminGroups": admin}}
    pr = oc_run(
        ["patch", "auth", _AUTH_NAME, "--type=merge", "-p", json.dumps(patch_doc)],
        check=False,
        capture_output=True,
        timeout=60,
    )
    if pr.returncode != 0:
        err = (pr.stderr or pr.stdout or "").strip()
        print(f"WARN: Auth/{_AUTH_NAME} group patch failed: {err}", file=sys.stderr)
        return False
    print(f"✓ Patched Auth/{_AUTH_NAME} allowedGroups/adminGroups", flush=True)
    return True


def _servicemesh_subscription_names(sub: dict) -> set[str]:
    status = sub.get("status") or {}
    names: set[str] = set()
    for key in ("currentCSV", "installedCSV"):
        val = str(status.get(key) or "").strip()
        if val:
            names.add(val)
    return names


def _subscription_resolution_failed(sub: dict) -> bool:
    for cond in (sub.get("status") or {}).get("conditions") or []:
        if isinstance(cond, dict) and cond.get("type") == "ResolutionFailed" and cond.get("status") == "True":
            return True
    return False


def _is_servicemesh_csv_name(name: str) -> bool:
    return name.lower().startswith(_SERVICEMESH_CSV_PREFIX)


def _subscription_installplan_missing(sub: dict) -> bool:
    for cond in (sub.get("status") or {}).get("conditions") or []:
        if not isinstance(cond, dict):
            continue
        if cond.get("type") == "InstallPlanMissing" and cond.get("status") == "True":
            return True
        reason = str(cond.get("reason") or "")
        if reason == "ReferencedInstallPlanNotFound":
            return True
    return False


def _subscription_csv_names(sub: dict) -> set[str]:
    status = sub.get("status") or {}
    names: set[str] = set()
    for key in ("currentCSV", "installedCSV"):
        val = str(status.get(key) or "").strip()
        if val:
            names.add(val)
    return names


def _csv_exists(csv_names: set[str], csv_items: list[dict]) -> bool:
    if not csv_names:
        return True
    present = {
        str((item.get("metadata") or {}).get("name") or "").strip()
        for item in csv_items
        if isinstance(item, dict)
    }
    return all(name in present for name in csv_names)


def _recreate_subscription(namespace: str, sub: dict) -> bool:
    meta = sub.get("metadata") or {}
    spec = sub.get("spec") or {}
    name = str(meta.get("name") or "").strip()
    if not name:
        return False
    channel = str(spec.get("channel") or "").strip()
    source = str(spec.get("source") or "").strip()
    source_ns = str(spec.get("sourceNamespace") or "").strip()
    if not channel or not source or not source_ns:
        print(
            f"WARN: cannot recreate Subscription/{name} in {namespace} (incomplete spec)",
            file=sys.stderr,
        )
        return False
    new_doc = {
        "apiVersion": "operators.coreos.com/v1alpha1",
        "kind": "Subscription",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "channel": channel,
            "name": str(spec.get("name") or name).strip(),
            "source": source,
            "sourceNamespace": source_ns,
            "installPlanApproval": str(spec.get("installPlanApproval") or "Manual").strip()
            or "Manual",
        },
    }
    dr = oc_run(
        ["delete", "subscription", name, "-n", namespace, "--wait=true"],
        check=False,
        capture_output=True,
        timeout=180,
    )
    if dr.returncode != 0:
        err = (dr.stderr or dr.stdout or "").strip()
        print(f"WARN: could not delete Subscription/{name}: {err}", file=sys.stderr)
        return False
    ar = oc_run(
        ["apply", "-f", "-"],
        stdin_text=json.dumps(new_doc),
        check=False,
        capture_output=True,
        timeout=60,
    )
    if ar.returncode != 0:
        err = (ar.stderr or ar.stdout or "").strip()
        print(f"WARN: could not recreate Subscription/{name}: {err}", file=sys.stderr)
        return False
    print(f"✓ Recreated Subscription/{name} in {namespace} (stale InstallPlan ref)", flush=True)
    return True


def repair_servicemesh_subscription_stale_refs(namespace: str = "openshift-operators") -> int:
    """Recreate Service Mesh subscriptions stuck with missing InstallPlan or CSV."""
    sub_r = oc_run(
        ["get", "subscription", "-n", namespace, "-o", "json"],
        check=False,
        capture_output=True,
        timeout=60,
    )
    if sub_r.returncode != 0:
        return 0
    try:
        sub_doc = json.loads(sub_r.stdout or "{}")
    except json.JSONDecodeError:
        return 0

    csv_r = oc_run(
        ["get", "csv", "-n", namespace, "-o", "json"],
        check=False,
        capture_output=True,
        timeout=60,
    )
    csv_items: list[dict] = []
    if csv_r.returncode == 0:
        try:
            csv_items = [
                item
                for item in (json.loads(csv_r.stdout or "{}").get("items") or [])
                if isinstance(item, dict)
            ]
        except json.JSONDecodeError:
            csv_items = []

    repaired = 0
    for item in sub_doc.get("items") or []:
        if not isinstance(item, dict):
            continue
        name = ((item.get("metadata") or {}).get("name") or "").lower()
        if not _is_servicemesh_csv_name(name):
            continue
        csv_names = _subscription_csv_names(item)
        needs_repair = _subscription_installplan_missing(item) or (
            bool(csv_names) and not _csv_exists(csv_names, csv_items)
        )
        if needs_repair and _recreate_subscription(namespace, item):
            repaired += 1
    return repaired


def reconcile_servicemesh_olm_conflicts(namespace: str = "openshift-operators") -> int:
    """Drop orphan Pending/Failed Service Mesh CSVs blocking OLM resolution on HCP clusters."""
    repaired = repair_servicemesh_subscription_stale_refs(namespace)
    sub_r = oc_run(
        ["get", "subscription", "-n", namespace, "-o", "json"],
        check=False,
        capture_output=True,
        timeout=60,
    )
    if sub_r.returncode != 0:
        return 0
    try:
        sub_doc = json.loads(sub_r.stdout or "{}")
    except json.JSONDecodeError:
        return 0

    target_csvs: set[str] = set()
    resolution_failed = False
    for item in sub_doc.get("items") or []:
        if not isinstance(item, dict):
            continue
        name = ((item.get("metadata") or {}).get("name") or "").lower()
        if not _is_servicemesh_csv_name(name):
            continue
        target_csvs |= _servicemesh_subscription_names(item)
        if _subscription_resolution_failed(item):
            resolution_failed = True

    csv_r = oc_run(
        ["get", "csv", "-n", namespace, "-o", "json"],
        check=False,
        capture_output=True,
        timeout=60,
    )
    if csv_r.returncode != 0:
        return repaired
    try:
        csv_doc = json.loads(csv_r.stdout or "{}")
    except json.JSONDecodeError:
        return 0

    orphan_names: list[str] = []
    for item in csv_doc.get("items") or []:
        if not isinstance(item, dict):
            continue
        csv_name = ((item.get("metadata") or {}).get("name") or "").strip()
        if not _is_servicemesh_csv_name(csv_name):
            continue
        phase = ((item.get("status") or {}).get("phase") or "").strip()
        if phase == "Succeeded":
            continue
        if csv_name in target_csvs and phase in ("Installing", "Replacing"):
            continue
        if phase in ("Pending", "Failed") and csv_name not in target_csvs:
            orphan_names.append(csv_name)
        elif resolution_failed and phase == "Pending" and len(target_csvs) == 1 and csv_name in target_csvs:
            orphan_names.append(csv_name)

    if not orphan_names:
        return 0

    removed = 0
    for csv_name in sorted(set(orphan_names)):
        dr = oc_run(
            ["delete", "csv", csv_name, "-n", namespace, "--wait=false"],
            check=False,
            capture_output=True,
            timeout=60,
        )
        if dr.returncode == 0:
            print(f"✓ Removed orphan Service Mesh CSV/{csv_name} in {namespace}", flush=True)
            removed += 1
        else:
            err = (dr.stderr or dr.stdout or "").strip()
            print(f"WARN: could not delete CSV/{csv_name}: {err}", file=sys.stderr)

    ip_r = oc_run(
        ["get", "installplan", "-n", namespace, "-o", "json"],
        check=False,
        capture_output=True,
        timeout=60,
    )
    if ip_r.returncode == 0:
        try:
            ip_doc = json.loads(ip_r.stdout or "{}")
        except json.JSONDecodeError:
            ip_doc = {}
        orphan_set = set(orphan_names)
        for item in ip_doc.get("items") or []:
            if not isinstance(item, dict):
                continue
            spec = item.get("spec") or {}
            csvs = {str(c) for c in (spec.get("clusterServiceVersionNames") or [])}
            if not csvs & orphan_set:
                continue
            phase = ((item.get("status") or {}).get("phase") or "").strip()
            if phase not in ("Failed", "RequiresApproval"):
                continue
            ip_name = (item.get("metadata") or {}).get("name") or ""
            if not ip_name:
                continue
            oc_run(
                ["delete", "installplan", ip_name, "-n", namespace, "--wait=false"],
                check=False,
                capture_output=True,
                timeout=60,
            )
            print(f"✓ Removed stale InstallPlan/{ip_name} for orphan Service Mesh CSV", flush=True)
    return repaired + removed


def wait_servicemesh_csv_succeeded(
    namespace: str = "openshift-operators",
    *,
    timeout_sec: int = 900,
) -> bool:
    """Wait for a Service Mesh operator CSV to reach Succeeded (after InstallPlan approve)."""
    if gateway_config_ready():
        print("✓ GatewayConfig already Ready — skipping Service Mesh CSV wait", flush=True)
        return True
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        r = oc_run(
            ["get", "csv", "-n", namespace, "-o", "json"],
            check=False,
            capture_output=True,
            timeout=60,
        )
        if r.returncode == 0:
            try:
                doc = json.loads(r.stdout or "{}")
            except json.JSONDecodeError:
                doc = {}
            for item in doc.get("items") or []:
                if not isinstance(item, dict):
                    continue
                name = ((item.get("metadata") or {}).get("name") or "").lower()
                if not name.startswith(_SERVICEMESH_CSV_PREFIX):
                    continue
                phase = ((item.get("status") or {}).get("phase") or "").strip()
                if phase == "Succeeded":
                    print(f"✓ Service Mesh CSV {name} is Succeeded", flush=True)
                    return True
                if phase == "Failed":
                    print(f"WARN: Service Mesh CSV {name} is Failed", file=sys.stderr)
                    return False
                print(f"  Service Mesh CSV {name} phase={phase or '?'}", flush=True)
        time.sleep(15)
    print(f"WARN: Service Mesh CSV not Succeeded within {timeout_sec}s", file=sys.stderr)
    return False


def wait_gateway_config_ready(*, timeout_sec: int = 900) -> bool:
    """Poll GatewayConfig until Ready, ProvisioningSucceeded, and GatewayConfigReady are True."""
    print(f"Waiting for GatewayConfig/{_GATEWAY_NAME} Ready (up to {timeout_sec}s)...", flush=True)
    deadline = time.monotonic() + timeout_sec
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        doc = _gateway_config_doc()
        if doc:
            statuses = {name: _condition_status(doc, name) for name in _READY_CONDITIONS}
            if all(status == "True" for status in statuses.values()):
                print(f"✓ GatewayConfig/{_GATEWAY_NAME} is Ready", flush=True)
                return True
            print(f"  GatewayConfig attempt {attempt}: {statuses}", flush=True)
        else:
            print(f"  GatewayConfig attempt {attempt}: CR not found yet", flush=True)
        time.sleep(15)
    print(f"WARN: GatewayConfig/{_GATEWAY_NAME} not Ready within {timeout_sec}s", file=sys.stderr)
    return False


def ensure_rhoai_gateway_for_install(
    *,
    wait_timeout_sec: int = 900,
    wait_servicemesh_first: bool = False,
) -> None:
    """Post-operator install: OIDC patch + Auth groups + optional SM wait + gateway Ready wait."""
    if wait_servicemesh_first:
        timeout = int(os.environ.get("SERVICEMESH_CSV_WAIT_SEC", "900"))
        wait_servicemesh_csv_succeeded(timeout_sec=timeout)
    patch_gateway_config_oidc()
    configure_auth_cr_groups()
    if not wait_servicemesh_first:
        timeout = int(os.environ.get("SERVICEMESH_CSV_WAIT_SEC", "300"))
        wait_servicemesh_csv_succeeded(timeout_sec=timeout)
    if not wait_gateway_config_ready(timeout_sec=wait_timeout_sec):
        print(
            "WARN: GatewayConfig not Ready after install prep; dashboard/gateway tests may fail",
            file=sys.stderr,
        )
