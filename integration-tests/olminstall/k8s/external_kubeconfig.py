"""Upload local kubeconfig as a tenant Secret for external-cluster olminstall runs."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from suite.constants import (
    ANNOTATION_CLUSTER,
    DEFAULT_CLUSTER_IDLE_POLL_SEC,
    DEFAULT_CLUSTER_IDLE_WAIT_SEC,
    LABEL_CLUSTER,
)
from suite.errors import AppError
from install.kubeconfig_cluster_label import cluster_label_from_kubeconfig
from .oc_util import filter_warning_lines, run_cmd

RUN_OWNER_LABEL = "olminstall.run-owner"
RUN_OWNER_ANNOTATION = "olminstall.run-owner"
MANAGED_BY_LABEL = "olminstall.external-kubeconfig"
MANAGED_BY_VALUE = "olm_pipeline.py"
EXTERNAL_CLUSTER_VERIFY_TIMEOUT_S = 30.0
TEKTON_EXTERNAL_CLUSTER_VERIFY_TIMEOUT_S = 60.0
RHOAI_IDMS_SOURCE = "registry.redhat.io/rhoai"
RHOAI_IDMS_MIRROR = "quay.io/rhoai"


def idms_has_rhoai_mirror(spec: dict) -> bool:
    for entry in spec.get("imageDigestMirrors") or []:
        if entry.get("source") != RHOAI_IDMS_SOURCE:
            continue
        if RHOAI_IDMS_MIRROR in (entry.get("mirrors") or []):
            return True
    return False


def external_cluster_has_rhoai_idms(
    kubeconfig_path: Path | str,
    *,
    timeout: float = EXTERNAL_CLUSTER_VERIFY_TIMEOUT_S,
) -> bool:
    path = Path(kubeconfig_path).expanduser().resolve()
    proc = run_cmd(
        ["oc", "--kubeconfig", str(path), "get", "imagedigestmirrorset", "-o", "json"],
        capture=True,
        check=False,
        timeout=timeout,
    )
    if proc.returncode != 0:
        return False
    try:
        items = json.loads(proc.stdout or "{}").get("items") or []
    except json.JSONDecodeError:
        return False
    return any(idms_has_rhoai_mirror(item.get("spec") or {}) for item in items)


def external_cluster_is_hypershift_managed(
    kubeconfig_path: Path | str,
    *,
    timeout: float = EXTERNAL_CLUSTER_VERIFY_TIMEOUT_S,
) -> bool:
    path = Path(kubeconfig_path).expanduser().resolve()
    proc = run_cmd(
        [
            "oc",
            "--kubeconfig",
            str(path),
            "get",
            "imagedigestmirrorset",
            "cluster",
            "-o",
            "jsonpath={.metadata.labels.hypershift\\.openshift\\.io/managed}",
        ],
        capture=True,
        check=False,
        timeout=timeout,
    )
    return proc.returncode == 0 and (proc.stdout or "").strip() == "true"


def external_cluster_rosa_hcp_pull_ready(
    kubeconfig_path: Path | str,
    *,
    timeout: float = EXTERNAL_CLUSTER_VERIFY_TIMEOUT_S,
) -> bool:
    path = Path(kubeconfig_path).expanduser().resolve()
    base = ["oc", "--kubeconfig", str(path)]
    if run_cmd([*base, "get", "ns", "kyverno"], capture=True, check=False, timeout=timeout).returncode != 0:
        return False
    for name in ("sync-secrets", "add-imagepullsecrets", "replace-rhoai-registry"):
        proc = run_cmd(
            [*base, "get", "clusterpolicy", name, "-o", "jsonpath={.status.ready}"],
            capture=True,
            check=False,
            timeout=timeout,
        )
        if proc.returncode != 0 or (proc.stdout or "").strip().lower() != "true":
            return False
    return (
        run_cmd(
            [*base, "get", "secret", "pull-secret-quay", "-n", "openshift-config"],
            capture=True,
            check=False,
            timeout=timeout,
        ).returncode
        == 0
    )


def verify_external_cluster_rhoai_idms_mirror(
    kubeconfig_path: Path | str,
    *,
    timeout: float = EXTERNAL_CLUSTER_VERIFY_TIMEOUT_S,
) -> None:
    """Fail fast when an external install cluster cannot mirror rhoai bundle pulls."""
    if os.environ.get("OLMINSTALL_SKIP_IDMS_PREFLIGHT", "").strip().lower() in ("1", "true", "yes"):
        print("WARN OLMINSTALL_SKIP_IDMS_PREFLIGHT set — skipping rhoai IDMS preflight")
        return
    path = Path(kubeconfig_path).expanduser().resolve()
    if external_cluster_has_rhoai_idms(path, timeout=timeout):
        print(f"✓ External cluster IDMS mirror configured ({RHOAI_IDMS_SOURCE} → {RHOAI_IDMS_MIRROR})")
        return
    if external_cluster_is_hypershift_managed(path, timeout=timeout):
        if external_cluster_rosa_hcp_pull_ready(path, timeout=timeout):
            print("✓ External HyperShift cluster has ROSA HCP Kyverno pull setup")
            return
        print(
            "INFO HyperShift external cluster: ROSA HCP Kyverno pull setup runs in "
            "external-cluster-ready / patch-cluster-pull-secret before install/tests."
        )
        return
    raise AppError(
        "External cluster is missing the rhoai IDMS mirror required for OLM bundle-unpack "
        f"({RHOAI_IDMS_SOURCE} → {RHOAI_IDMS_MIRROR}). "
        "On HyperShift guest clusters, olminstall applies Jenkins-style Kyverno policies. "
        "Set OLMINSTALL_SKIP_IDMS_PREFLIGHT=1 to bypass this check.",
        2,
    )


def default_secret_name(run_owner: str, kubeconfig_path: Path | str | None = None) -> str:
    """Derive a DNS-safe Secret name from cluster label, else ``oc whoami``."""
    if kubeconfig_path:
        cluster = cluster_label_from_kubeconfig(kubeconfig_path)
        if cluster:
            prefix = "olminstall-kubeconfig-"
            owner = re.sub(r"[^a-z0-9-]", "-", (run_owner or "user").lower())
            owner = re.sub(r"-+", "-", owner).strip("-")[:16] or "user"
            max_suffix = 63 - len(prefix) - len(owner) - 1
            safe = re.sub(r"[^a-z0-9-]", "-", cluster.lower())
            safe = re.sub(r"-+", "-", safe).strip("-")[:max_suffix].strip("-") or "cluster"
            return f"{prefix}{safe}-{owner}"
    safe = re.sub(r"[^a-z0-9-]", "-", (run_owner or "user").lower())
    safe = re.sub(r"-+", "-", safe).strip("-")[:40] or "user"
    return f"olminstall-kubeconfig-{safe}"


def validate_kubeconfig_path(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_file():
        raise AppError(f"--external-kubeconfig must be an existing file: {p}", 2)
    return p.resolve()


def verify_external_cluster_login(
    kubeconfig_path: Path | str,
    *,
    timeout: float = EXTERNAL_CLUSTER_VERIFY_TIMEOUT_S,
) -> str:
    """Return ``oc whoami`` for *kubeconfig_path*; raise AppError when not logged in."""
    path = Path(kubeconfig_path).expanduser().resolve()
    if not path.is_file():
        raise AppError(f"External kubeconfig not found: {path}", 2)
    proc = run_cmd(
        ["oc", "--kubeconfig", str(path), "whoami", "--request-timeout=30s"],
        capture=True,
        check=False,
        timeout=timeout,
    )
    who = (proc.stdout or "").strip()
    if proc.returncode != 0 or not who or who == "system:anonymous":
        err = filter_warning_lines(f"{proc.stdout or ''}\n{proc.stderr or ''}").strip()[:500]
        raise AppError(
            f"External cluster login required (oc --kubeconfig {path} whoami failed): "
            f"{err or who or proc.returncode}",
            2,
        )
    return who


def verify_external_cluster_secret(
    *,
    namespace: str,
    secret_name: str,
    timeout: float = EXTERNAL_CLUSTER_VERIFY_TIMEOUT_S,
) -> str:
    """Load tenant Secret kubeconfig and verify API login."""
    import base64
    import tempfile

    name = (secret_name or "").strip()
    ns = (namespace or "").strip()
    if not name or not ns:
        raise AppError("namespace and secret name required to verify external kubeconfig Secret", 2)
    proc = run_cmd(
        [
            "oc",
            "get",
            "secret",
            name,
            "-n",
            ns,
            "-o",
            "jsonpath={.data.kubeconfig}",
        ],
        capture=True,
        check=False,
        timeout=timeout,
    )
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        detail = filter_warning_lines(f"{proc.stdout or ''}\n{proc.stderr or ''}").strip()[:500]
        raise AppError(
            f"Could not read external kubeconfig Secret {name!r} in {ns}: {detail or proc.returncode}",
            2,
        )
    try:
        raw = base64.b64decode(proc.stdout.strip())
    except ValueError as exc:
        raise AppError(f"Secret {name!r} kubeconfig data is not valid base64", 2) from exc
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".kubeconfig") as tf:
            tf.write(raw)
            tmp_path = tf.name
        return verify_external_cluster_login(tmp_path, timeout=timeout)
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


def _user_uses_exec_auth(user: object) -> bool:
    return isinstance(user, dict) and isinstance(user.get("exec"), dict)


def _token_from_exec_user(kubeconfig_path: Path, user: dict) -> str:
    exec_cfg = user.get("exec")
    if not isinstance(exec_cfg, dict):
        return ""
    command = str(exec_cfg.get("command") or "oc").strip() or "oc"
    args = [str(a) for a in (exec_cfg.get("args") or [])]
    env = os.environ.copy()
    env["KUBECONFIG"] = str(kubeconfig_path)
    for item in exec_cfg.get("env") or []:
        if isinstance(item, dict) and item.get("name"):
            env[str(item["name"])] = str(item.get("value", ""))
    try:
        proc = subprocess.run(
            [command, *args],
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=EXTERNAL_CLUSTER_VERIFY_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise AppError(
            f"Exec credential plugin ({command}) timed out after "
            f"{EXTERNAL_CLUSTER_VERIFY_TIMEOUT_S}s",
            2,
        ) from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:500]
        raise AppError(
            f"Could not resolve bearer token via exec auth ({command}): {err or proc.returncode}",
            2,
        )
    try:
        cred = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise AppError("Exec credential plugin returned non-JSON output", 2) from exc
    status = cred.get("status") if isinstance(cred, dict) else None
    if not isinstance(status, dict):
        raise AppError("Exec credential plugin JSON missing status", 2)
    token = str(status.get("token") or "").strip()
    if not token:
        raise AppError(
            "Exec credential plugin returned empty token; refresh external cluster login and retry.",
            2,
        )
    return token


def materialize_kubeconfig_for_tekton(kubeconfig_path: Path) -> tuple[Path, bool]:
    """Return a kubeconfig Tekton pods can use (token auth, no local ``oc`` exec).

    When the source uses ``user.exec`` (typical after ``oc login --web``), resolve a
    bearer token with the local ``oc`` CLI and write a minimal kubeconfig copy.
    Caller must delete the returned path when it differs from *kubeconfig_path*.
    """
    import yaml

    try:
        doc = yaml.safe_load(kubeconfig_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AppError(f"Cannot read kubeconfig {kubeconfig_path}: {exc}", 2) from exc
    except yaml.YAMLError as exc:
        raise AppError(f"Invalid kubeconfig YAML {kubeconfig_path}: {exc}", 2) from exc
    if not isinstance(doc, dict):
        raise AppError(f"Invalid kubeconfig (expected mapping): {kubeconfig_path}", 2)

    contexts = doc.get("contexts") if isinstance(doc.get("contexts"), list) else []
    users = doc.get("users") if isinstance(doc.get("users"), list) else []
    current = str(doc.get("current-context") or "").strip()
    ctx = next((c for c in contexts if isinstance(c, dict) and c.get("name") == current), None)
    if not isinstance(ctx, dict):
        return kubeconfig_path, False

    context = ctx.get("context") if isinstance(ctx.get("context"), dict) else {}
    user_name = str(context.get("user") or "").strip()
    cluster_name = str(context.get("cluster") or "").strip()
    user_entry = next((u for u in users if isinstance(u, dict) and u.get("name") == user_name), None)
    if not isinstance(user_entry, dict):
        return kubeconfig_path, False

    user = user_entry.get("user") if isinstance(user_entry.get("user"), dict) else {}
    if not user.get("token") and not _user_uses_exec_auth(user):
        return kubeconfig_path, False

    from steps.tekton_util import _resolve_bearer_token_from_kubeconfig

    env = {**os.environ, "KUBECONFIG": str(kubeconfig_path)}
    token = _resolve_bearer_token_from_kubeconfig(kubeconfig_path, env)
    if not token and _user_uses_exec_auth(user):
        token = _token_from_exec_user(kubeconfig_path, user)
    embedded = str(user.get("token") or "").strip()
    if not token:
        if embedded:
            return kubeconfig_path, False
        raise AppError(
            "Could not resolve bearer token for Tekton upload; refresh external cluster login and retry.",
            2,
        )
    if embedded and token == embedded and not _user_uses_exec_auth(user):
        return kubeconfig_path, False

    clusters = doc.get("clusters") if isinstance(doc.get("clusters"), list) else []
    cluster_entry = next(
        (c for c in clusters if isinstance(c, dict) and c.get("name") == cluster_name),
        None,
    )
    if not isinstance(cluster_entry, dict):
        raise AppError(f"Kubeconfig missing cluster {cluster_name!r} for context {current!r}", 2)

    materialized = {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [cluster_entry],
        "contexts": [ctx],
        "current-context": current,
        "users": [{"name": user_name, "user": {"token": token}}],
    }
    tmp = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".kubeconfig", delete=False)
    try:
        yaml.safe_dump(materialized, tmp, default_flow_style=False)
        tmp.flush()
        tmp.close()
        return Path(tmp.name), True
    except OSError:
        Path(tmp.name).unlink(missing_ok=True)
        raise


def ensure_external_kubeconfig_secret(
    *,
    namespace: str,
    kubeconfig_path: Path,
    secret_name: str,
    run_owner: str,
) -> str:
    """Create or update a generic Secret with key ``kubeconfig``; return secret name."""
    name = (secret_name or "").strip() or default_secret_name(run_owner, kubeconfig_path)
    upload_path, ephemeral = materialize_kubeconfig_for_tekton(kubeconfig_path)
    try:
        if ephemeral:
            print(
                "Materialized external kubeconfig with bearer token for Tekton "
                "(prefers 24h olminstall-cluster-admin SA when cluster-admin; "
                "source used oc exec auth; opendatahub-tests image has no oc binary).",
                flush=True,
            )
        who = verify_external_cluster_login(upload_path)
        print(f"External cluster preflight OK as {who}")
        proc = run_cmd(
            [
                "oc",
                "create",
                "secret",
                "generic",
                name,
                f"--from-file=kubeconfig={upload_path}",
                "-n",
                namespace,
                "--dry-run=client",
                "-o",
                "yaml",
            ],
            capture=True,
            check=True,
        )
    finally:
        if ephemeral:
            upload_path.unlink(missing_ok=True)
    apply = run_cmd(
        ["oc", "apply", "-n", namespace, "-f", "-"],
        capture=True,
        check=False,
        input_text=proc.stdout,
    )
    filtered = filter_warning_lines(f"{apply.stdout}\n{apply.stderr}")
    if filtered.strip():
        print(filtered)
    if apply.returncode != 0:
        raise AppError(f"Failed to apply external kubeconfig Secret {name!r} in {namespace}")

    cluster_label = cluster_label_from_kubeconfig(kubeconfig_path)
    annotate_argv = [
        "oc",
        "annotate",
        "secret",
        name,
        "-n",
        namespace,
        f"{RUN_OWNER_ANNOTATION}={run_owner}",
    ]
    if cluster_label:
        annotate_argv.append(f"{ANNOTATION_CLUSTER}={cluster_label}")
    annotate_argv.append("--overwrite")
    ann = run_cmd(annotate_argv, capture=True, check=False)
    lbl_argv = [
        "oc",
        "label",
        "secret",
        name,
        "-n",
        namespace,
        f"{RUN_OWNER_LABEL}={run_owner}",
        f"{MANAGED_BY_LABEL}={MANAGED_BY_VALUE}",
    ]
    if cluster_label:
        from runners.report.pipelinerun_metadata import sanitize_k8s_label_value

        cl = sanitize_k8s_label_value(cluster_label)
        if cl:
            lbl_argv.append(f"{LABEL_CLUSTER}={cl}")
    lbl_argv.append("--overwrite")
    lbl = run_cmd(lbl_argv, capture=True, check=False)
    for proc in (ann, lbl):
        msg = filter_warning_lines(f"{proc.stdout}\n{proc.stderr}").strip()
        if proc.returncode != 0 and msg:
            print(f"  WARN secret metadata: {msg}")
    print(f"External kubeconfig Secret: {name} (namespace {namespace})")
    return name


def cluster_label_from_tenant_secret(*, namespace: str, secret_name: str) -> str:
    """Context/cluster name from Secret annotation or embedded kubeconfig (best-effort)."""
    import base64
    import tempfile

    name = (secret_name or "").strip()
    ns = (namespace or "").strip()
    if not name or not ns:
        return ""
    ann_proc = run_cmd(
        [
            "oc",
            "get",
            "secret",
            name,
            "-n",
            ns,
            "-o",
            f"jsonpath={{.metadata.annotations['{ANNOTATION_CLUSTER}']}}",
        ],
        capture=True,
        check=False,
        timeout=30,
    )
    if ann_proc.returncode == 0:
        label = (ann_proc.stdout or "").strip()
        if label:
            return label
    for key in ("kubeconfig", "config"):
        proc = run_cmd(
            [
                "oc",
                "get",
                "secret",
                name,
                "-n",
                ns,
                "-o",
                f"jsonpath={{.data.{key}}}",
            ],
            capture=True,
            check=False,
            timeout=30,
        )
        if proc.returncode != 0 or not (proc.stdout or "").strip():
            continue
        try:
            raw = base64.b64decode(proc.stdout.strip())
        except ValueError:
            continue
        path = ""
        try:
            with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".kubeconfig") as tf:
                tf.write(raw)
                path = tf.name
            label = cluster_label_from_kubeconfig(path)
            if label:
                return label
        finally:
            if path:
                Path(path).unlink(missing_ok=True)
    return ""


def _pipelinerun_cluster_source_param(item: dict) -> str:
    spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
    for param in spec.get("params") or []:
        if not isinstance(param, dict) or param.get("name") != "CLUSTER_SOURCE":
            continue
        return str(param.get("value") or "").strip()
    return ""


def _normalize_cluster_id(value: str) -> str:
    return (value or "").strip().lower()


def cluster_label_from_secret_name(secret_name: str) -> str:
    """Derive cluster id from a tenant kubeconfig Secret name (best-effort)."""
    secret = (secret_name or "").strip()
    if not secret:
        return ""
    for prefix in ("olminstall-kubeconfig-", "kubeconfig-"):
        if secret.startswith(prefix):
            return secret[len(prefix) :].strip("-") or secret
    return secret


def resolve_cluster_id_for_external_cluster(
    *,
    namespace: str,
    cluster_source: str,
    cluster_id: str = "",
) -> str:
    """Physical cluster id for single-flight locking (label on PR / Secret annotation)."""
    explicit = _normalize_cluster_id(cluster_id)
    if explicit:
        return explicit
    source = (cluster_source or "").strip()
    if not source:
        return ""
    from suite.its_trigger_params import is_external_cluster_source

    if not is_external_cluster_source(source):
        return ""
    label = cluster_label_from_tenant_secret(namespace=namespace, secret_name=source)
    if label:
        return _normalize_cluster_id(label)
    return _normalize_cluster_id(cluster_label_from_secret_name(source))


_PIPELINE_RUN_WIND_DOWN_REASONS = frozenset(
    {
        "StoppedRunningFinally",
        "PipelineRunStopping",
        "PipelineRunCancelled",
        "Cancelled",
        "StoppedRunCancelled",
        "TaskRunCancelled",
    }
)


def _pipelinerun_succeeded_reason(item: dict) -> str:
    status = item.get("status") if isinstance(item.get("status"), dict) else {}
    for cond in status.get("conditions") or []:
        if not isinstance(cond, dict) or cond.get("type") != "Succeeded":
            continue
        return str(cond.get("reason") or "").strip()
    return ""


def _pipelinerun_is_active(item: dict) -> bool:
    status = item.get("status") if isinstance(item.get("status"), dict) else {}
    if status.get("completionTime"):
        return False
    reason = _pipelinerun_succeeded_reason(item)
    if reason in _PIPELINE_RUN_WIND_DOWN_REASONS:
        return False
    if "cancel" in reason.lower() or "stopping" in reason.lower():
        return False
    return True


def _pipelinerun_cluster_label(item: dict) -> str:
    meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    labels = meta.get("labels") if isinstance(meta.get("labels"), dict) else {}
    return _normalize_cluster_id(str(labels.get(LABEL_CLUSTER) or ""))


def _pipelinerun_matches_external_cluster(
    item: dict,
    *,
    namespace: str,
    cluster_id: str,
    cluster_source: str,
    secret_cluster_cache: dict[str, str],
) -> bool:
    pr_source = _pipelinerun_cluster_source_param(item)
    target_source = (cluster_source or "").strip()
    target_id = _normalize_cluster_id(cluster_id)
    if target_source and pr_source == target_source:
        return True
    if not target_id:
        return False
    pr_label = _pipelinerun_cluster_label(item)
    if pr_label and pr_label == target_id:
        return True
    if pr_source:
        if pr_source not in secret_cluster_cache:
            secret_cluster_cache[pr_source] = resolve_cluster_id_for_external_cluster(
                namespace=namespace,
                cluster_source=pr_source,
            )
        if secret_cluster_cache[pr_source] == target_id:
            return True
        derived = _normalize_cluster_id(cluster_label_from_secret_name(pr_source))
        if derived == target_id or derived.startswith(f"{target_id}-") or target_id.startswith(f"{derived}-"):
            return True
    return False


def _list_olminstall_pipelinerun_items(*, namespace: str) -> list[dict] | None:
    ns = (namespace or "").strip()
    if not ns:
        return []
    proc = run_cmd(
        ["oc", "get", "pipelinerun", "-n", ns, "-o", "json"],
        capture=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return []
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def list_active_pipelineruns_for_external_cluster(
    *,
    namespace: str,
    cluster_source: str,
    cluster_id: str = "",
    exclude_name: str = "",
) -> list[str] | None:
    """Incomplete olminstall PipelineRuns on the same physical external cluster.

    Matches ``olminstall.cluster`` label, ``CLUSTER_SOURCE`` param, or resolved Secret
    cluster id. Returns None when the Konflux API cannot be queried.
    """
    from suite.its_trigger_params import is_external_cluster_source

    source = (cluster_source or "").strip()
    if not is_external_cluster_source(source):
        return []
    target_id = resolve_cluster_id_for_external_cluster(
        namespace=namespace,
        cluster_source=source,
        cluster_id=cluster_id,
    )
    items = _list_olminstall_pipelinerun_items(namespace=namespace)
    if items is None:
        return None
    exclude = (exclude_name or "").strip()
    secret_cluster_cache: dict[str, str] = {}
    active: list[str] = []
    for item in items:
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        name = str(meta.get("name") or "").strip()
        if not name or not name.startswith("olminstall"):
            continue
        if name == exclude:
            continue
        if not _pipelinerun_is_active(item):
            continue
        if not _pipelinerun_matches_external_cluster(
            item,
            namespace=namespace,
            cluster_id=target_id,
            cluster_source=source,
            secret_cluster_cache=secret_cluster_cache,
        ):
            continue
        active.append(name)
    return sorted(active)


def wait_for_external_cluster_idle(
    *,
    namespace: str,
    cluster_source: str,
    cluster_id: str = "",
    exclude_pipelinerun: str = "",
    force: bool = False,
    timeout_sec: int = DEFAULT_CLUSTER_IDLE_WAIT_SEC,
    poll_interval_sec: int = DEFAULT_CLUSTER_IDLE_POLL_SEC,
) -> None:
    """Wait until no other olminstall run holds the same external cluster (Jenkins resource-lock style).

    No-op for EAAS or empty CLUSTER_SOURCE. When *force* is true, skip the check. When *timeout_sec*
    is 0, fail immediately if another run is active (legacy assert behavior).
    """
    from suite.its_trigger_params import is_external_cluster_source

    source = (cluster_source or "").strip()
    if not is_external_cluster_source(source) or force:
        return
    ns = (namespace or "").strip()
    if not ns:
        raise AppError("Konflux namespace is required to verify external cluster availability.", 1)
    target_id = resolve_cluster_id_for_external_cluster(
        namespace=ns,
        cluster_source=source,
        cluster_id=cluster_id,
    )
    poll = max(5, int(poll_interval_sec or DEFAULT_CLUSTER_IDLE_POLL_SEC))
    deadline = time.monotonic() + max(0, int(timeout_sec or 0))
    first_log = True
    while True:
        active = list_active_pipelineruns_for_external_cluster(
            namespace=ns,
            cluster_source=source,
            cluster_id=target_id,
            exclude_name=(exclude_pipelinerun or "").strip(),
        )
        if active is None:
            raise AppError(
                f"Cannot verify that external cluster {source!r} is idle (Konflux API query failed). "
                "Refusing to start another olminstall run on a shared external kubeconfig.",
                1,
            )
        if not active:
            if not first_log:
                print(
                    f"External cluster idle: cluster={target_id or source!r} "
                    f"(CLUSTER_SOURCE={source!r})",
                    flush=True,
                )
            return
        preview = ", ".join(active[:3])
        if len(active) > 3:
            preview = f"{preview} (+{len(active) - 3} more)"
        if timeout_sec <= 0:
            raise AppError(_external_cluster_busy_message(source, target_id, preview, active[0]), 1)
        if time.monotonic() >= deadline:
            raise AppError(
                f"Timed out after {timeout_sec}s waiting for external cluster "
                f"{target_id or source!r} to become idle. Still active: {preview}.",
                1,
            )
        if first_log:
            print(
                f"Waiting for external cluster {target_id or source!r} "
                f"(poll every {poll}s; active: {preview})",
                flush=True,
            )
            first_log = False
        else:
            print(f"  still waiting — active PipelineRun(s): {preview}", flush=True)
        time.sleep(poll)


def _external_cluster_busy_message(
    cluster_source: str,
    cluster_id: str,
    preview: str,
    watch_pr: str,
) -> str:
    cluster_ref = cluster_id or cluster_source
    return (
        f"External cluster {cluster_ref!r} is busy: active PipelineRun(s): {preview}. "
        "Only one olminstall run at a time is allowed per external cluster. "
        "Wait for the run to finish, pass --force-cluster-run to override, or watch with: "
        f"python3 integration-tests/olminstall/olm_pipeline.py -w {watch_pr}"
    )


def assert_external_cluster_idle(
    *,
    namespace: str,
    cluster_source: str,
    cluster_id: str = "",
    exclude_pipelinerun: str = "",
    force: bool = False,
) -> None:
    """Fail immediately when another olminstall PipelineRun still uses the same external cluster."""
    wait_for_external_cluster_idle(
        namespace=namespace,
        cluster_source=cluster_source,
        cluster_id=cluster_id,
        exclude_pipelinerun=exclude_pipelinerun,
        force=force,
        timeout_sec=0,
    )


def list_active_pipelineruns_for_cluster_source(
    *,
    namespace: str,
    cluster_source: str,
    exclude_name: str = "",
) -> list[str] | None:
    """PipelineRuns still active with the same ``CLUSTER_SOURCE`` param (narrow helper)."""
    return list_active_pipelineruns_for_external_cluster(
        namespace=namespace,
        cluster_source=cluster_source,
        exclude_name=exclude_name,
    )


def delete_external_kubeconfig_secret(
    *,
    namespace: str,
    secret_name: str,
    exclude_pipelinerun: str = "",
) -> None:
    if not secret_name:
        return
    others = list_active_pipelineruns_for_cluster_source(
        namespace=namespace,
        cluster_source=secret_name,
        exclude_name=exclude_pipelinerun,
    )
    if others is None:
        print(
            f"  WARN Keeping Secret {secret_name} (could not verify other PipelineRuns on Konflux)"
        )
        return
    if others:
        preview = ", ".join(others[:3])
        suffix = "…" if len(others) > 3 else ""
        print(
            f"  Keeping Secret {secret_name} ({len(others)} other PipelineRun(s) still active: "
            f"{preview}{suffix})"
        )
        return
    proc = run_cmd(
        ["oc", "delete", "secret", secret_name, "-n", namespace, "--ignore-not-found"],
        capture=True,
        check=False,
    )
    if proc.returncode == 0 and "deleted" in (proc.stdout or "").lower():
        print(f"  Deleted Secret {secret_name}")
