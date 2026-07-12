"""Tekton run-step helpers for dashboard Cypress (clone, cluster prep, JUnit)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from components.dashboard_cypress.config import (
    _find_all_junit_reports,
    _find_mochawesome_json_paths,
    _junit_report_has_testcases,
    _mochawesome_json_to_junit_xml,
    discover_cypress_results_subdirs,
    merge_smokeset_junit_reports,
    write_merged_junit_reports,
)
from install.dsc_install import oc_run
import install.ldap as _ldap

_DASHBOARD_AUTH_NS = "redhat-ods-applications"
_RUNTIME_CONFIG_PATCH_KEYS = (
    "ODH_DASHBOARD_URL",
    "ODH_DASHBOARD_PROJECT_NAME",
    "OPERATOR_NAMESPACE",
    "APPLICATIONS_NAMESPACE",
    "MONITORING_NAMESPACE",
    "NOTEBOOKS_NAMESPACE",
    "OPERATOR_NAME",
    "CLUSTER_AUTH",
)
_ENVOY_FILTER = "data-science-authn-filter"
_ENVOY_FILTER_NS_CANDIDATES = (
    "openshift-ingress",
    "istio-system",
    "redhat-ods-applications",
)
_VAULT_MOUNT = Path("/component-vault-credentials")
_VAULT_SKIP_KEYS = frozenset({"AWS_CA_BUNDLE", "test-variables.yml", "CY_TEST_CONFIG"})
_VAULT_CONFIG_KEYS = ("CY_TEST_CONFIG", "test-variables.yml")
_K8S_IN_CLUSTER_ENV = (
    "KUBERNETES_SERVICE_HOST",
    "KUBERNETES_SERVICE_PORT",
    "KUBERNETES_PORT",
    "KUBERNETES_PORT_443_TCP",
    "KUBERNETES_PORT_443_TCP_ADDR",
    "KUBERNETES_PORT_443_TCP_PORT",
    "KUBERNETES_PORT_443_TCP_PROTO",
)
_CI_AUTH_BYPASS_SRC = Path(__file__).with_name("assets") / "ci-auth-bypass.ts"
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DASHBOARD_OK_HTTP = frozenset({"200", "302", "403"})
_HTPASSWD_HCP_EXTRA_SKIP_TAGS = (
    "@ModelServingCI @KServeCI @ModelServing @MaaSCI @ProjectsCI @PipelinesCI "
    "@Pipelines @ci-dashboard-regression-tags @ModelRegistryCI @AutoMLCI @Tier1"
)
_BYOIDC_EXTRA_SKIP_TAGS = (
    "@FeatureStore @FeatureStoreCI @ConnectionTypesCI @SettingsCI "
    "@MaaSCI @MaasSubscriptions @ODS-327 @ODS-492 "
    "@ModelServingCI @KServeCI @ModelServing @LLMDServingCI "
    "@ProjectsCI @PipelinesCI @Pipelines @ci-dashboard-regression-tags "
    "@ModelRegistryCI @AutoMLCI @Tier1 "
    "@HardwareProfilesCI @HardwareProfileModelServing"
)
_KONFLUX_MANIFEST_EXTRA_SKIP_TAGS = "@ODS-327 @ODS-492"


def stage_writable_kubeconfig(artifacts_dir: Path, kubeconfig_src: str) -> Path:
    """Copy kubeconfig into artifacts so the runtime user can read/write it."""
    stage_dir = artifacts_dir / ".kube"
    stage_dir.mkdir(parents=True, exist_ok=True)
    dest = stage_dir / "config"
    shutil.copy2(kubeconfig_src, dest)
    return dest


def unset_in_cluster_k8s_env() -> None:
    for key in _K8S_IN_CLUSTER_ENV:
        os.environ.pop(key, None)


def _dashboard_workspace_package_names(dashboard_src: Path, working_dir_rel: str) -> list[str]:
    """Return npm workspace package names needed for Cypress (frontend + packages/cypress)."""
    names: list[str] = []
    for rel, fallback in (
        (working_dir_rel, working_dir_rel),
        ("packages/cypress", "packages/cypress"),
    ):
        pkg_path = dashboard_src / rel / "package.json"
        if not pkg_path.is_file():
            continue
        try:
            data = json.loads(pkg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            names.append(fallback)
            continue
        name = str(data.get("name", "")).strip()
        names.append(name or fallback)
    return names


def _dashboard_npm_ci_command(dashboard_src: Path, working_dir_rel: str) -> list[str]:
    """Limit npm ci to Cypress workspaces so rhoai-3.4 monorepos fit the tests-shared PVC."""
    workspace_names = _dashboard_workspace_package_names(dashboard_src, working_dir_rel)
    if len(workspace_names) >= 2 and (dashboard_src / "package.json").is_file():
        # rhoai-3.4 root postinstall runs turbo for every package; skip for Cypress-only install.
        return ["npm", "ci", "--ignore-scripts", *[f"-w={name}" for name in workspace_names]]
    return ["npm", "ci"]


def _reset_dashboard_src_if_ref_changed(
    artifacts_dir: Path,
    dashboard_src: Path,
    source_repo: str,
    source_ref: str,
) -> None:
    ref_marker = artifacts_dir / ".dashboard-source-ref"
    marker = json.dumps({"repo": source_repo, "ref": source_ref}, sort_keys=True)
    previous = ref_marker.read_text(encoding="utf-8").strip() if ref_marker.is_file() else ""
    if dashboard_src.is_dir() and previous and previous != marker:
        print(
            f"Removing stale dashboard-src ({previous!r} -> {marker!r})...",
            flush=True,
        )
        shutil.rmtree(dashboard_src)
    ref_marker.write_text(f"{marker}\n", encoding="utf-8")


def _install_cypress_binary(dashboard_src: Path, working_dir_rel: str) -> None:
    """Populate CYPRESS_CACHE_FOLDER for the dashboard branch's Cypress major (e.g. 13.x on rhoai-3.4)."""
    cypress_pkg = dashboard_src / "packages" / "cypress"
    cwd = cypress_pkg if (cypress_pkg / "package.json").is_file() else dashboard_src / working_dir_rel
    if not (cwd / "package.json").is_file():
        return
    print(f"Installing Cypress binary (npx cypress install) in {cwd}...", flush=True)
    subprocess.run(["npx", "cypress", "install"], cwd=cwd, check=True)


def _hoist_tslib_for_cypress(dashboard_src: Path) -> None:
    """Scoped npm ci nests tslib under frontend/; Cypress resolves from packages/cypress."""
    root_tslib = dashboard_src / "node_modules" / "tslib"
    if root_tslib.exists():
        return
    src = dashboard_src / "frontend" / "node_modules" / "tslib"
    if not src.is_dir():
        return
    root_modules = dashboard_src / "node_modules"
    root_modules.mkdir(parents=True, exist_ok=True)
    root_tslib.symlink_to(os.path.relpath(src, root_modules), target_is_directory=True)


def _ensure_cypress_binary_installed(dashboard_src: Path, working_dir_rel: str) -> None:
    marker = dashboard_src / ".olminstall-cypress-binary-done"
    if marker.is_file():
        return
    _install_cypress_binary(dashboard_src, working_dir_rel)
    marker.touch()


def prepare_dashboard_worktree(
    *,
    artifacts_dir: Path,
    source_repo: str,
    source_ref: str,
    working_dir_rel: str,
    results_dir_rel: str,
) -> tuple[Path, Path]:
    """Clone odh-dashboard, npm ci, return absolute working and results dirs."""
    dashboard_src = artifacts_dir / "dashboard-src"
    home = artifacts_dir / "home"
    npm_cache = artifacts_dir / ".npm-cache"
    home.mkdir(parents=True, exist_ok=True)
    npm_cache.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(home)
    os.environ["npm_config_cache"] = str(npm_cache)
    os.environ.setdefault("CYPRESS_CACHE_FOLDER", "/cypress_cache")
    os.environ.setdefault(
        "ELECTRON_EXTRA_LAUNCH_ARGS",
        "--disable-dev-shm-usage --no-sandbox --disable-gpu",
    )
    _reset_dashboard_src_if_ref_changed(artifacts_dir, dashboard_src, source_repo, source_ref)
    if not (dashboard_src / ".git").is_dir():
        print(f"Cloning {source_repo} ({source_ref}) into {dashboard_src}...", flush=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", source_ref, source_repo, str(dashboard_src)],
            check=True,
        )
    npm_marker = dashboard_src / ".olminstall-npm-ci-done"
    if not npm_marker.is_file():
        npm_cmd = _dashboard_npm_ci_command(dashboard_src, working_dir_rel)
        print(f"Running {' '.join(npm_cmd)} in {dashboard_src}...", flush=True)
        subprocess.run(npm_cmd, cwd=dashboard_src, check=True)
        npm_marker.touch()
    _hoist_tslib_for_cypress(dashboard_src)
    _ensure_cypress_binary_installed(dashboard_src, working_dir_rel)
    patch_dashboard_cypress_upstream_tests(dashboard_src)
    patch_dashboard_cypress_ldap_gateway_login(dashboard_src)
    os.environ["DASHBOARD_SRC"] = str(dashboard_src)
    return dashboard_src / working_dir_rel, dashboard_src / results_dir_rel


def patch_dashboard_cypress_upstream_tests(dashboard_src: Path) -> None:
    """Patch upstream Cypress hooks that break on Konflux external clusters."""
    about = (
        dashboard_src
        / "packages/cypress/cypress/tests/e2e/applications/testAboutDialog.cy.ts"
    )
    if not about.is_file():
        return
    text = about.read_text(encoding="utf-8")
    if "olminstall-patched-about-dialog" in text:
        return
    text = text.replace("retryableBefore(async () => {", "retryableBefore(() => {")
    text = text.replace(
        "getInstalledProductName('default').then",
        (
            "return getInstalledProductName(Cypress.env('OPERATOR_NAMESPACE') || "
            "'redhat-ods-operator').then"
        ),
    )
    text = text.replace(
        "getCsvByDisplayName(productName, 'default').then",
        (
            "return getCsvByDisplayName(productName, Cypress.env('OPERATOR_NAMESPACE') || "
            "'redhat-ods-operator').then"
        ),
    )
    about.write_text(
        "// olminstall-patched-about-dialog\n" + text,
        encoding="utf-8",
    )
    print(f"✓ Patched {about.name} for operator-namespace CSV lookup", flush=True)


def patch_dashboard_cypress_ldap_gateway_login(dashboard_src: Path) -> None:
    """Pooled PSI LDAP: use OAuth IdP login, not Keycloak, when AUTH_TYPE is ldap-*."""
    app_ts = (
        dashboard_src
        / "packages/cypress/cypress/support/commands/application.ts"
    )
    if not app_ts.is_file():
        return
    text = app_ts.read_text(encoding="utf-8")
    marker = "olminstall-patched-ldap-gateway-login-v2"
    if marker in text:
        return
    if "TEST_USER_AUTH_TYPE" in text and "ldapAuthType.startsWith('ldap')" in text:
        app_ts.write_text(f"// {marker}\n" + text.lstrip(), encoding="utf-8")
        return
    old_403 = """        if (isBYOIDCCluster) {
          // For BYOIDC clusters, we expect to be redirected to Keycloak
          handleKeycloakLogin(credentials);
        } else {"""
    new_403 = """        const ldapAuthType = String(credentials.AUTH_TYPE || '').toLowerCase();
        if (isBYOIDCCluster && !ldapAuthType.startsWith('ldap')) {
          // For BYOIDC clusters, we expect to be redirected to Keycloak
          handleKeycloakLogin(credentials);
        } else {"""
    old_url = """      if (currentUrl.includes('keycloak') || currentUrl.includes('/protocol/openid-connect/auth')) {
        handleKeycloakLogin(credentials);
      }"""
    new_url = """      const ldapAuthType = String(credentials.AUTH_TYPE || '').toLowerCase();
      if (
        (currentUrl.includes('keycloak') || currentUrl.includes('/protocol/openid-connect/auth'))
        && !ldapAuthType.startsWith('ldap')
      ) {
        handleKeycloakLogin(credentials);
      }"""
    if old_403 not in text or old_url not in text:
        print(
            f"WARN: skip LDAP gateway login patch; {app_ts.name} layout changed",
            flush=True,
        )
        return
    old_visit = """Cypress.Commands.add('visitWithLogin', (relativeUrl, credentials = HTPASSWD_CLUSTER_ADMIN_USER) => {
  if (Cypress.env('MOCK')) {"""
    new_visit = """Cypress.Commands.add('visitWithLogin', (relativeUrl, credentials = HTPASSWD_CLUSTER_ADMIN_USER) => {
  const envLdapAuth = String(Cypress.env('TEST_USER_AUTH_TYPE') || '').toLowerCase();
  if (envLdapAuth.startsWith('ldap') && Cypress.env('TEST_USER_USERNAME')) {
    credentials = {
      USERNAME: Cypress.env('TEST_USER_USERNAME'),
      PASSWORD: Cypress.env('TEST_USER_PASSWORD'),
      AUTH_TYPE: Cypress.env('TEST_USER_AUTH_TYPE'),
    };
  } else {
    const vaultUser = Cypress.env('TEST_USER');
    if (
      vaultUser
      && String(vaultUser.AUTH_TYPE || '').toLowerCase().startsWith('ldap')
    ) {
      credentials = vaultUser;
    }
  }
  if (Cypress.env('MOCK')) {"""
    if old_visit not in text:
        print(
            f"WARN: skip LDAP visitWithLogin patch; {app_ts.name} layout changed",
            flush=True,
        )
        return
    text = (
        text.replace(old_visit, new_visit, 1)
        .replace(old_403, new_403, 1)
        .replace(old_url, new_url, 1)
    )
    app_ts.write_text(f"// {marker}\n" + text, encoding="utf-8")
    print(f"✓ Patched {app_ts.name} for LDAP gateway login on BYOIDC pools", flush=True)


def _find_envoyfilter_namespace(envoy_filter: str) -> str:
    """Return namespace hosting ``envoy_filter`` (``oc get NAME -A`` is invalid)."""
    for ns in _ENVOY_FILTER_NS_CANDIDATES:
        r = oc_run(
            ["get", f"envoyfilter.networking.istio.io/{envoy_filter}", "-n", ns],
            check=False,
            capture_output=True,
            timeout=30,
        )
        if r.returncode == 0:
            return ns
    r = oc_run(
        [
            "get",
            "envoyfilter",
            "-A",
            "--no-headers",
            "-o",
            "custom-columns=NS:.metadata.namespace,NAME:.metadata.name",
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if r.returncode != 0:
        return ""
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == envoy_filter:
            return parts[0]
    return ""


def patch_gateway_envoyfilter_if_needed() -> None:
    """When kube-auth-proxy is missing, allow ext_authz failure so bearer auth works."""
    svc = oc_run(
        ["get", "service", "kube-auth-proxy", "-n", _DASHBOARD_AUTH_NS],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if svc.returncode == 0:
        return
    print(
        f"NOTICE: kube-auth-proxy service missing in {_DASHBOARD_AUTH_NS} — "
        "OAP gateway ext_authz broken.",
        flush=True,
    )
    filter_ns = _find_envoyfilter_namespace(_ENVOY_FILTER)
    if not filter_ns:
        print(f"WARN: EnvoyFilter {_ENVOY_FILTER} not found; skip patch.", flush=True)
        return
    cur = oc_run(
        [
            "get",
            f"envoyfilter.networking.istio.io/{_ENVOY_FILTER}",
            "-n",
            filter_ns,
            "-o",
            "jsonpath={.spec.configPatches[0].patch.value.typed_config.failure_mode_allow}",
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if (cur.stdout or "").strip() == "true":
        print("EnvoyFilter already has failure_mode_allow=true; skip.", flush=True)
        return
    print(f"Patching EnvoyFilter failure_mode_allow=true in ns/{filter_ns}...", flush=True)
    patch_payload = (
        '[{"op":"add","path":"/spec/configPatches/{idx}/patch/value/typed_config/'
        'failure_mode_allow","value":true}]'
    )
    for idx in (0, 1):
        r = oc_run(
            [
                "patch",
                f"envoyfilter.networking.istio.io/{_ENVOY_FILTER}",
                "-n",
                filter_ns,
                "--type=json",
                "-p",
                patch_payload.replace("{idx}", str(idx)),
            ],
            check=False,
            capture_output=True,
            timeout=30,
        )
        if r.returncode == 0:
            print(f"  ✓ Patched configPatches[{idx}]", flush=True)


def resolve_cypress_support_dir(working_dir: Path) -> Path | None:
    """Locate odh-dashboard ``packages/cypress/cypress/support`` for CI injections."""
    candidates: list[Path] = []
    dashboard_src = os.environ.get("DASHBOARD_SRC", "").strip()
    if dashboard_src:
        candidates.append(Path(dashboard_src) / "packages" / "cypress" / "cypress" / "support")
    repo_root = working_dir.parent if working_dir.name in ("frontend", "packages") else working_dir
    candidates.append(repo_root / "packages" / "cypress" / "cypress" / "support")
    candidates.append(working_dir / "cypress" / "support")
    for support_dir in candidates:
        if support_dir.is_dir():
            return support_dir
    return None


def inject_ci_auth_bypass(working_dir: Path) -> None:
    """Copy ci-auth-bypass.ts into cypress/support and import from e2e entrypoint."""
    if not _CI_AUTH_BYPASS_SRC.is_file():
        print(f"WARN: missing CI auth bypass template {_CI_AUTH_BYPASS_SRC}", file=sys.stderr, flush=True)
        return
    support_dir = resolve_cypress_support_dir(working_dir)
    if support_dir is None:
        print(f"WARN: cypress/support not found under {working_dir}", file=sys.stderr, flush=True)
        return
    dest = support_dir / "ci-auth-bypass.ts"
    shutil.copy2(_CI_AUTH_BYPASS_SRC, dest)
    for entry_name in ("e2e.ts", "e2e.js"):
        entry = support_dir / entry_name
        if not entry.is_file():
            continue
        text = entry.read_text(encoding="utf-8")
        if "ci-auth-bypass" not in text:
            entry.write_text(text + "\nimport './ci-auth-bypass';\n", encoding="utf-8")
        print(f"✓ Injected CI auth bypass into {entry}", flush=True)
        return


def load_component_vault_env() -> dict[str, str]:
    """Export vault secret keys into env (except config file keys loaded separately)."""
    out: dict[str, str] = {}
    if not _VAULT_MOUNT.is_dir():
        return out
    for path in sorted(_VAULT_MOUNT.iterdir()):
        if not path.is_file():
            continue
        key = path.name
        if key in _VAULT_SKIP_KEYS or not _ENV_KEY_RE.match(key):
            continue
        if not os.environ.get(key):
            out[key] = path.read_text(encoding="utf-8").strip("\n")
    for cfg_key in _VAULT_CONFIG_KEYS:
        cfg_path = _VAULT_MOUNT / cfg_key
        if cfg_path.is_file():
            out["CY_TEST_CONFIG"] = str(cfg_path)
            break
    return out


def _default_runtime_config_overrides() -> dict[str, str]:
    """Cluster namespace defaults when vault CY_TEST_CONFIG is incomplete."""
    from components.dashboard_cypress.config import (
        _DASHBOARD_CYPRESS_CONFIG_DEFAULTS,
        resolve_odh_dashboard_project_name,
    )

    overrides = dict(_DASHBOARD_CYPRESS_CONFIG_DEFAULTS)
    project_name = resolve_odh_dashboard_project_name()
    if project_name:
        overrides["ODH_DASHBOARD_PROJECT_NAME"] = project_name
    return overrides


from components.dashboard_cypress.auth_overlay import (  # noqa: E402
    _apply_gateway_auth_overlay,
    _deep_merge_dict,
    _load_yaml_dict,
    _merge_test_clusters_into_runtime_config,
    _yaml_scalar,
    dashboard_url_is_local,
    gateway_use_byoidc_auth,
    is_konflux_eaas_gateway_url,
    resolve_gateway_auth_overlay,
    resolve_test_clusters_overlay,
    sync_cypress_auth_env_from_config,
    _byoidc_cypress_poll_settings,
)

# Re-export auth overlay helpers for existing imports from runtime.


def htpasswd_hcp_extra_cypress_skip_tags(*, odh_dashboard_url: str) -> str:
    """Extra grep skipTags for htpasswd gateway clusters without LDAP (e.g. personal HCP)."""
    if dashboard_url_is_local(odh_dashboard_url):
        return ""
    if _ldap._cluster_is_byoidc():
        return ""
    if is_konflux_eaas_gateway_url(odh_dashboard_url):
        return ""
    from install.ldap import cluster_has_ldap_identity

    if cluster_has_ldap_identity():
        return ""
    return _HTPASSWD_HCP_EXTRA_SKIP_TAGS


def byoidc_extra_cypress_skip_tags(*, odh_dashboard_url: str) -> str:
    """Extra skipTags for BYOIDC pooled clusters (LDAP/user-management paths unsupported)."""
    if dashboard_url_is_local(odh_dashboard_url):
        return ""
    if not _ldap._cluster_is_byoidc():
        return ""
    return _BYOIDC_EXTRA_SKIP_TAGS


def konflux_olminstall_extra_cypress_skip_tags() -> str:
    """Skip specs that need outbound docs.redhat.com from Konflux CI pods (egress 403)."""
    if os.environ.get("ARTIFACTS", "").strip() or os.environ.get("WORKSPACE", "").strip():
        return _KONFLUX_MANIFEST_EXTRA_SKIP_TAGS
    return ""


def cypress_extra_skip_tags(*, odh_dashboard_url: str) -> str:
    """Merge runtime Cypress skipTags for gateway auth mode and CI environment."""
    parts = [
        htpasswd_hcp_extra_cypress_skip_tags(odh_dashboard_url=odh_dashboard_url),
        byoidc_extra_cypress_skip_tags(odh_dashboard_url=odh_dashboard_url),
        konflux_olminstall_extra_cypress_skip_tags(),
    ]
    return " ".join(part for part in parts if part.strip())


def patch_runtime_cy_test_config(
    artifacts_dir: Path,
    *,
    cy_test_config: str,
    odh_dashboard_url: str,
    extra_overrides: dict[str, str] | None = None,
    cluster_label: str = "",
) -> str:
    """Copy CY_TEST_CONFIG and apply runtime cluster overrides for this run."""
    runtime_cfg = artifacts_dir / "cypress-runtime-config.yml"
    src = Path(cy_test_config)
    if src.resolve() != runtime_cfg.resolve():
        shutil.copy2(cy_test_config, runtime_cfg)
    if cluster_label:
        _merge_test_clusters_into_runtime_config(runtime_cfg, src, cluster_label)
    _apply_gateway_auth_overlay(
        runtime_cfg,
        src,
        cluster_label=cluster_label,
        odh_dashboard_url=odh_dashboard_url,
    )
    overrides = dict(_default_runtime_config_overrides())
    overrides["ODH_DASHBOARD_URL"] = odh_dashboard_url
    if extra_overrides:
        overrides.update({k: v for k, v in extra_overrides.items() if v})
    lines = runtime_cfg.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        if not line or line.lstrip().startswith("#") or line.startswith(" "):
            new_lines.append(line)
            continue
        key = line.split(":", 1)[0].strip() if ":" in line else ""
        if key in overrides:
            new_lines.append(f"{key}: {_yaml_scalar(overrides[key])}")
            seen.add(key)
        else:
            new_lines.append(line)
    for key in _RUNTIME_CONFIG_PATCH_KEYS:
        if key in overrides and key not in seen:
            new_lines.append(f"{key}: {_yaml_scalar(overrides[key])}")
    runtime_cfg.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return str(runtime_cfg)


def verify_dashboard_reachable(url: str) -> bool:
    """HEAD request with retries; accept 200/302/403 (Jenkins verifyDashboardRoute)."""
    print(f"Verify dashboard is accessible at {url}...", flush=True)
    headers: list[str] = []
    token = os.environ.get("OC_TOKEN", "").strip() or os.environ.get("CYPRESS_OC_TOKEN", "").strip()
    if token and ("127.0.0.1" in url or "localhost" in url):
        headers = ["-H", f"Authorization: Bearer {token}"]
    proc = subprocess.run(
        [
            "curl",
            "--head",
            "--write-out",
            "%{http_code}",
            "--output",
            "/dev/null",
            "--insecure",
            "--max-time",
            "10",
            "--connect-timeout",
            "5",
            "--retry",
            "20",
            "--retry-delay",
            "5",
            "--retry-max-time",
            "180",
            "--retry-all-errors",
            "--no-progress-meter",
            *headers,
            url,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    code = (proc.stdout or "").strip()
    return code in _DASHBOARD_OK_HTTP


def _prepend_pythonpath(entry: str) -> None:
    import sys

    existing = os.environ.get("PYTHONPATH", "").strip()
    if entry and entry not in {p for p in existing.split(":") if p}:
        os.environ["PYTHONPATH"] = f"{entry}:{existing}" if existing else entry
    if entry and entry not in sys.path:
        sys.path.insert(0, entry)


def prepend_staged_python_deps() -> bool:
    """Expose staged .tools/python on PYTHONPATH when orchestrate installed PyYAML."""
    from steps.tests_payload import resolve_tests_payload_root, tests_payload_tools_python_dir

    artifacts = Path(os.environ.get("ARTIFACTS_DIR", "/artifacts").strip() or "/artifacts")
    target = tests_payload_tools_python_dir(resolve_tests_payload_root(artifacts))
    if not target.is_dir():
        return False
    if not (target / "yaml" / "__init__.py").is_file():
        return False
    from runners.orchestrator import _remove_staged_pyyaml_binaries

    _remove_staged_pyyaml_binaries(target)
    _prepend_pythonpath(str(target))
    return True


def _pip_install_to_target(package: str, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-cache-dir",
            "--target",
            str(target),
            package,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"pip install {package} to {target} failed: {detail or proc.returncode}")


def _ensure_pyyaml_available() -> None:
    try:
        import yaml  # noqa: F401
        return
    except ImportError:
        pass
    if prepend_staged_python_deps():
        try:
            import yaml  # noqa: F401
            return
        except ImportError:
            pass
    if os.geteuid() == 0:
        if shutil.which("dnf"):
            print("Installing python3-pyyaml for Cypress model-catalog helpers...", flush=True)
            subprocess.run(["dnf", "install", "-y", "--nodocs", "python3-pyyaml"], check=True)
        elif shutil.which("microdnf"):
            subprocess.run(["microdnf", "install", "-y", "python3-pyyaml"], check=True)
        import yaml  # noqa: F401
        return
    import importlib.util

    if importlib.util.find_spec("pip") is None:
        raise RuntimeError(
            "PyYAML not available in the Cypress image and pip is missing; "
            "orchestrate step should stage .tools/python"
        )
    from steps.tests_payload import resolve_tests_payload_root, tests_payload_tools_python_dir

    artifacts = Path(os.environ.get("ARTIFACTS_DIR", "/artifacts").strip() or "/artifacts")
    target = tests_payload_tools_python_dir(resolve_tests_payload_root(artifacts))
    print(f"Installing PyYAML to {target} (non-root Cypress task)...", flush=True)
    _pip_install_to_target("pyyaml", target)
    _prepend_pythonpath(str(target))
    import yaml  # noqa: F401


def ensure_cypress_cli_packages() -> None:
    """Ensure jq and PyYAML are available (stage_cypress_cli_tools should run first)."""
    jq_ok = False
    jq_path = shutil.which("jq")
    if jq_path:
        probe = subprocess.run([jq_path, "--version"], check=False, capture_output=True, text=True)
        jq_ok = probe.returncode == 0 and "error while loading shared libraries" not in (
            (probe.stderr or "") + (probe.stdout or "")
        )
    if not jq_ok:
        if os.geteuid() != 0:
            raise RuntimeError(
                "jq not found after staging Cypress CLI tools; "
                "cannot install packages as non-root under Konflux SCC"
            )
        if shutil.which("dnf"):
            print("Installing jq for Cypress oc.exec helpers...", flush=True)
            subprocess.run(["dnf", "install", "-y", "--nodocs", "jq"], check=True)
        elif shutil.which("microdnf"):
            subprocess.run(["microdnf", "install", "-y", "jq"], check=True)
    _ensure_pyyaml_available()


def ensure_google_chrome() -> None:
    """Install Chrome when absent (optional Cypress browser fallback)."""
    if shutil.which("google-chrome") or shutil.which("google-chrome-stable"):
        return
    if os.geteuid() != 0:
        print(
            "WARN: google-chrome not found; skipping install in non-root Cypress task (Electron fallback)",
            flush=True,
        )
        return
    print("Installing google-chrome-stable (optional Cypress browser fallback)...", flush=True)
    rpm = "https://dl.google.com/linux/direct/google-chrome-stable_current_x86_64.rpm"
    if shutil.which("dnf"):
        subprocess.run(["dnf", "install", "-y", "--nodocs", rpm], check=True)
    elif shutil.which("microdnf"):
        subprocess.run(["microdnf", "install", "-y", rpm], check=True)
    else:
        raise RuntimeError("google-chrome not found and no dnf/microdnf to install it")


def run_cypress_shell_command(run_command: str, *, test_timeout_sec: float | None) -> int:
    """Execute parallel Cypress shell command produced by orchestrate step."""
    print(f"Running (cypress): {run_command}", flush=True)
    if test_timeout_sec is not None:
        proc = subprocess.run(
            ["timeout", f"{int(test_timeout_sec)}s", "bash", "-c", run_command],
            check=False,
        )
    else:
        proc = subprocess.run(["bash", "-c", run_command], check=False)
    return int(proc.returncode)


def collect_cypress_junit(
    *,
    artifacts_dir: Path,
    artifact_prefix: str,
    results_dir: Path,
    results_subdirs: str,
) -> bool:
    """Merge parallel-set JUnit (or mochawesome) into ARTIFACTS/{artifact_prefix}.xml."""
    junit_dest = artifacts_dir / f"{artifact_prefix}.xml"
    subdir_list = [s for s in results_subdirs.split(",") if s]
    if not subdir_list:
        subdir_list = list(discover_cypress_results_subdirs(artifacts_dir))
    subdirs = subdir_list or None
    if merge_smokeset_junit_reports(artifacts_dir, junit_dest, results_subdirs=subdirs):
        return True
    for name in subdir_list or discover_cypress_results_subdirs(artifacts_dir):
        subdir = artifacts_dir / name
        reports = _find_all_junit_reports(subdir)
        if reports and write_merged_junit_reports(reports, junit_dest):
            return True
        for mo_path in _find_mochawesome_json_paths(subdir):
            if _mochawesome_json_to_junit_xml(mo_path, junit_dest):
                return True
    for pattern in (
        results_dir / "junit-report.xml",
        results_dir / "junit" / "junit-report.xml",
    ):
        if pattern.is_file() and _junit_report_has_testcases(pattern):
            shutil.copy2(pattern, junit_dest)
            return True
    for xml in results_dir.rglob("*.xml"):
        if _junit_report_has_testcases(xml):
            shutil.copy2(xml, junit_dest)
            return True
    cypress_pkg = Path(os.environ.get("CYPRESS_PKG_ROOT", "/home/cypress/packages/cypress"))
    if cypress_pkg.is_dir():
        for xml in cypress_pkg.rglob("*.xml"):
            if _junit_report_has_testcases(xml):
                shutil.copy2(xml, junit_dest)
                return True
    print(f"WARN: no JUnit under {results_dir}", file=sys.stderr, flush=True)
    return False
