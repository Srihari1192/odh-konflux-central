"""List/OCP helpers mixin for OLMInstallRunner."""

from __future__ import annotations

import json
import re
import sys
from typing import Any
from urllib.parse import quote

from suite.constants import (
    ANNOTATION_SNAPSHOT,
    DEFAULT_LIST_COUNT,
    LIST_SUPPORTED_OCP_MAX_PRS,
    OLMINSTALL_EAAS_ITS_NAME,
    olminstall_smoke_only_pipelinerun,
)
from suite.errors import AppError
from k8s.kubearchive import KubeArchiveAuthError
from k8s.oc_util import get_jsonpath, parse_json_output, run_cmd
from .runner_mixin_ocp import RunnerOcpMixin
from .runner_support import (
    PipelineRow,
    _snapshot_param_is_resource_name,
    filter_pipelinerun_items,
    pipelinerun_list_state,
)


class RunnerListMixin(RunnerOcpMixin):
    def get_pipelineruns(self, namespace: str, selector: str | None = None) -> list[dict[str, Any]]:
        cmd = ["oc", "get", "pipelineruns", "-n", namespace, "-o", "json"]
        if selector:
            cmd.extend(["-l", selector])
        data = parse_json_output(cmd)
        return data.get("items", []) if data else []


    def succeeded_condition_detail(self, pr_name: str) -> tuple[str, str, str]:
        """``Succeeded`` condition: status, reason, message (empty strings if missing)."""
        data = parse_json_output(["oc", "get", "pipelinerun", pr_name, "-n", self.args.namespace, "-o", "json"])
        for cond in data.get("status", {}).get("conditions", []):
            if cond.get("type") == "Succeeded":
                return (
                    cond.get("status", "Unknown"),
                    (cond.get("reason") or "").strip(),
                    (cond.get("message") or "").strip(),
                )
        return "Unknown", "", ""


    def succeeded_condition(self, pr_name: str) -> tuple[str, str]:
        c, r, _ = self.succeeded_condition_detail(pr_name)
        return c, r

    @staticmethod

    def _is_resolver_couldnt_get_pipeline(reason: str, message: str) -> bool:
        r = (reason or "").strip()
        m = (message or "").lower()
        if r == "CouldntGetPipeline":
            return True
        return "couldntgetpipeline" in r.lower() or "resolver failed" in m or "file does not exist" in m

    @staticmethod

    def _coerce_snapshot_payload_to_spec(obj: Any) -> dict[str, Any] | None:
        """Normalize Konflux ``SNAPSHOT`` param JSON to a ``Snapshot.spec``-shaped dict."""
        if not isinstance(obj, dict):
            return None
        if isinstance(obj.get("application"), str) and isinstance(obj.get("components"), list):
            return obj
        spec = obj.get("spec")
        if isinstance(spec, dict) and isinstance(spec.get("application"), str) and isinstance(spec.get("components"), list):
            return spec
        return None

    @classmethod

    def _parse_snapshot_param_as_spec(cls, snap_value: str) -> dict[str, Any] | None:
        try:
            obj = json.loads(snap_value)
        except json.JSONDecodeError:
            return None
        return cls._coerce_snapshot_payload_to_spec(obj)


    def _raise_resolver_terminal(self, pr_name: str, reason: str, message: str) -> None:
        """Fail fast: Tekton never started tasks (CouldntGetPipeline / resolver)."""
        self._warn_couldnt_get_pipeline_git_source()
        excerpt = (message or reason or "").strip()
        if len(excerpt) > 500:
            excerpt = excerpt[:500] + "…"
        raise AppError(
            "PipelineRun failed before tasks started (pipeline definition could not be loaded). "
            f"``{pr_name}``: {excerpt}\n"
            f"Konflux: {self.konflux_ui}/ns/{self.args.namespace}/applications/{self.args.app}/pipelineruns/{pr_name}"
        )


    def _fail_fast_resolver_terminal(self, pr_name: str) -> None:
        """Raise if the run already failed on pipeline resolution (no tasks)."""
        cstat, reason, message = self.succeeded_condition_detail(pr_name)
        if cstat != "False" or not self._is_resolver_couldnt_get_pipeline(reason, message):
            return
        self._raise_resolver_terminal(pr_name, reason, message)


    def _warn_couldnt_get_pipeline_git_source(self) -> None:
        """Contextual hint after CouldntGetPipeline / missing pipeline file in Git resolver."""
        repo = (getattr(self.args, "konflux_repo", None) or "").strip()
        branch = (getattr(self.args, "konflux_branch", None) or "").strip()
        head = (
            "WARN Pipeline did not start: Git resolver could not load "
            "``integration-tests/olminstall/tekton/pipelines/olminstall-pipeline.yaml`` (CouldntGetPipeline).\n"
        )
        if repo and branch:
            ns = self.args.namespace
            its_name = OLMINSTALL_EAAS_ITS_NAME
            print(
                f"{head}"
                f"  This run applied the ITS with ``--konflux-repo`` / ``--konflux-branch``: **{repo}** @ **{branch}**.\n"
                "  Tekton still could not open that path at that ref: confirm the branch is pushed, the file exists "
                "on GitHub at that ref, and ``oc get integrationtestscenario -n "
                f"{ns} {its_name} -o yaml`` shows the same ``resolverRef`` url/revision after "
                "``olm_pipeline.py`` applied it.\n"
                "  If the branch is correct but the file is missing, add ``olminstall-pipeline.yaml`` (or fix "
                "``pathInRepo`` in the ITS) on that branch.",
                file=sys.stderr,
            )
            return
        if repo and not branch:
            print(
                f"{head}"
                f"  ``--konflux-repo`` is set ({repo}) but ``--konflux-branch`` is not; the ITS pipeline revision "
                "may still be the template default (often **main**), so the resolver may not see your fork branch.\n"
                "  Pass ``--konflux-branch <ref>`` and trigger again so ``resolverRef`` matches the ref that contains this path.",
                file=sys.stderr,
            )
            return
        if branch and not repo:
            print(
                f"{head}"
                f"  ``--konflux-branch`` is set ({branch!r}) but ``--konflux-repo`` is not; the ITS URL may still be the "
                "template default. Pass ``--konflux-repo`` as well so ``resolverRef`` points at your fork.",
                file=sys.stderr,
            )
            return
        print(
            f"{head}"
            "  With no ``--konflux-repo`` / ``--konflux-branch`` on the CLI, the ITS keeps the committed default: "
            "**opendatahub-io/odh-konflux-central** @ **main** (see its-olminstall-*.yaml). That ref may not have this path.\n"
            "  Re-apply the ITS with a fork + branch, then trigger again, e.g.\n"
            "    python3 olm_pipeline.py --tests bvt \\\n"
            "      --konflux-repo https://github.com/<you>/odh-konflux-central.git \\\n"
            "      --konflux-branch <branch>",
            file=sys.stderr,
        )


    def _ka_get_json_warn_empty(self, path: str, *, ctx: str) -> dict[str, Any]:
        assert self.ka is not None
        try:
            raw = self.ka.get_json(path)
        except KubeArchiveAuthError as exc:
            print(f"WARN KubeArchive auth failed ({ctx}): {exc}", file=sys.stderr)
            return {}
        return raw if isinstance(raw, dict) else {}


    def _ka_get_text_warn_empty(self, path: str, *, ctx: str) -> str:
        assert self.ka is not None
        try:
            return self.ka.get_text(path)
        except KubeArchiveAuthError as exc:
            print(f"WARN KubeArchive auth failed ({ctx}): {exc}", file=sys.stderr)
            if not getattr(self, "_ka_archive_text_auth_tip_shown", False):
                self._ka_archive_text_auth_tip_shown = True
                print(
                    "TIP: Re-login (``oc login``) so the KubeArchive client gets a fresh token; "
                    "see README ``KA_HOST`` / ``--ka-host``.",
                    file=sys.stderr,
                )
            return ""


    @staticmethod
    def _pipelinerun_snapshot_annotation(item: dict[str, Any]) -> str:
        return ((item.get("metadata") or {}).get("annotations") or {}).get(ANNOTATION_SNAPSHOT, "").strip()


    def _merged_pipelinerun_rows(self, limit: int, *, name_substr: str | None) -> list[PipelineRow]:
        rows: list[PipelineRow] = []
        for item in filter_pipelinerun_items(
            self.get_pipelineruns(self.args.namespace),
            app=self.args.app,
            olminstall_only=False,
            name_substr=name_substr,
        ):
            name = item.get("metadata", {}).get("name", "")
            app = item.get("metadata", {}).get("labels", {}).get("appstudio.openshift.io/application", "-")
            rows.append(
                PipelineRow(
                    name=name,
                    app=app,
                    state=pipelinerun_list_state(item),
                    created=item.get("metadata", {}).get("creationTimestamp", ""),
                    source="live",
                    snapshot=self._pipelinerun_snapshot_annotation(item),
                )
            )
        rows.sort(key=lambda r: r.created, reverse=True)
        rows = rows[:limit]

        needed = limit - len(rows)
        if needed > 0 and self.ka_available():
            assert self.ka is not None
            ka_limit = needed + limit
            path = (
                f"/apis/tekton.dev/v1/namespaces/{quote(self.args.namespace)}/pipelineruns"
                f"?labelSelector={quote(f'appstudio.openshift.io/application={self.args.app}')}&limit={ka_limit}"
            )
            data = self._ka_get_json_warn_empty(path, ctx="list archived PipelineRuns")
            for item in filter_pipelinerun_items(
                data.get("items", []),
                app=self.args.app,
                olminstall_only=False,
                name_substr=name_substr,
            ):
                name = item.get("metadata", {}).get("name", "")
                rows.append(
                    PipelineRow(
                        name=name,
                        app=item.get("metadata", {}).get("labels", {}).get("appstudio.openshift.io/application", "-"),
                        state=pipelinerun_list_state(item),
                        created=item.get("metadata", {}).get("creationTimestamp", ""),
                        source="archived",
                        snapshot=self._pipelinerun_snapshot_annotation(item),
                    )
                )

        merged: list[PipelineRow] = []
        seen: set[str] = set()
        for row in sorted(rows, key=lambda r: r.created, reverse=True):
            if row.name and row.name not in seen:
                merged.append(row)
                seen.add(row.name)
            if len(merged) >= limit:
                break
        return merged

    def _konflux_pipelinerun_url(self, application: str, pipelinerun_name: str) -> str:
        base = (self.konflux_ui or "").rstrip("/")
        if not base or not application or application == "-" or not pipelinerun_name:
            return ""
        return f"{base}/ns/{self.args.namespace}/applications/{application}/pipelineruns/{pipelinerun_name}"


    def list_pipelines(self) -> None:
        merged = self._merged_pipelinerun_rows(self.args.list_pipelines, name_substr=None)

        print(f"Latest {self.args.list_pipelines} PipelineRuns for app '{self.args.app}' in namespace '{self.args.namespace}':")
        if not merged:
            print(f"No PipelineRuns found for app '{self.args.app}'.")
            print("Tip: use --konflux-app <name> to target another application.")
            if self.ka is None:
                print(
                    "Tip: completed runs are often pruned from the cluster; set KA_HOST or --ka-host "
                    "(KubeArchive) to list archived PipelineRuns, or confirm oc context / namespace."
                )
            prj = run_cmd(["oc", "project", "-q"], capture=True, check=False)
            if prj.returncode == 0:
                current_ns = (prj.stdout or "").strip()
                if current_ns and current_ns != self.args.namespace:
                    print(
                        f"Tip: active oc project is '{current_ns}', but this command lists "
                        f"namespace '{self.args.namespace}' (not your current project). "
                        f"Use --konflux-namespace {current_ns} or switch: oc project {self.args.namespace}"
                    )
            return
        print("NAME\tAPP\tSTATE\tCREATED\tSNAPSHOT\tSOURCE\tLINK")
        for r in merged:
            link = self._konflux_pipelinerun_url(r.app, r.name) or "-"
            snap = r.snapshot or "-"
            print(f"{r.name}\t{r.app}\t{r.state}\t{r.created}\t{snap}\t{r.source}\t{link}")


    def get_snapshot_owner(self, snap: str) -> str:
        if not snap:
            return ""
        return get_jsonpath(
            [
                "oc",
                "get",
                "snapshot",
                snap,
                "-n",
                self.args.namespace,
                "-o",
                "jsonpath={.metadata.annotations.olminstall\\.run-owner}",
            ]
        )


    def _pick_newest_owned_pipelinerun(self, rows: list[tuple[str, str, str, str, str]]) -> str:
        """Pick newest PipelineRun name from rows (creation_ts, name, snapshot, run-owner, pipeline label)."""
        filtered = [r for r in rows if not olminstall_smoke_only_pipelinerun(r[1], r[4])]
        owned = [
            row
            for row in filtered
            if row[3] == self.run_owner
            or (
                _snapshot_param_is_resource_name(row[2])
                and self.get_snapshot_owner(row[2]) == self.run_owner
            )
        ]
        if not owned:
            return ""
        owned.sort(key=lambda x: x[0], reverse=True)
        return owned[0][1]


    def find_owned_live_watch_pr(self) -> str:
        items = self.get_pipelineruns(self.args.namespace)
        cands: list[tuple[str, str, str, str, str]] = []
        for item in items:
            name = item.get("metadata", {}).get("name", "")
            app = item.get("metadata", {}).get("labels", {}).get("appstudio.openshift.io/application", "")
            if "olminstall" not in name or app != self.args.app:
                continue
            snap = ""
            for p in item.get("spec", {}).get("params", []):
                if p.get("name") == "SNAPSHOT":
                    snap = str(p.get("value", ""))
                    break
            owner = item.get("metadata", {}).get("annotations", {}).get("olminstall.run-owner", "")
            pipe = item.get("metadata", {}).get("labels", {}).get("tekton.dev/pipeline", "")
            cands.append((item.get("metadata", {}).get("creationTimestamp", ""), name, snap, owner, pipe))
        return self._pick_newest_owned_pipelinerun(cands)


    def find_owned_archived_watch_pr(self) -> str:
        if not self.ka_available():
            return ""
        assert self.ka is not None
        # Match find_owned_live_watch_pr + _pick_newest_owned_pipelinerun: ownership may live only on
        # the Snapshot (olminstall.run-owner), while the archived PipelineRun annotation is unset or stale.
        path = f"/apis/tekton.dev/v1/namespaces/{quote(self.args.namespace)}/pipelineruns?labelSelector={quote(f'appstudio.openshift.io/application={self.args.app}')}"
        items = self._ka_get_json_warn_empty(path, ctx="archived PipelineRuns for watch").get("items", [])
        rows: list[tuple[str, str, str, str, str]] = []
        for item in items:
            name = item.get("metadata", {}).get("name", "")
            if "olminstall" not in name:
                continue
            snap = ""
            for p in item.get("spec", {}).get("params", []):
                if p.get("name") == "SNAPSHOT":
                    snap = str(p.get("value", ""))
                    break
            owner = item.get("metadata", {}).get("annotations", {}).get("olminstall.run-owner", "")
            pipe = item.get("metadata", {}).get("labels", {}).get("tekton.dev/pipeline", "")
            rows.append((item.get("metadata", {}).get("creationTimestamp", ""), name, snap, owner, pipe))
        return self._pick_newest_owned_pipelinerun(rows)


    def _pipelinerun_resolver_unusable_for_logs(self, name: str, source: str) -> bool:
        """True when Tekton never resolved the Pipeline (e.g. CouldntGetPipeline) — nothing useful to replay."""
        prj: dict[str, Any] = {}
        if source == "live":
            proc = run_cmd(
                ["oc", "get", "pipelinerun", name, "-n", self.args.namespace, "-o", "json"],
                capture=True,
                check=False,
            )
            if proc.returncode != 0:
                return False
            try:
                prj = json.loads(proc.stdout or "{}")
            except json.JSONDecodeError:
                return False
        elif self.ka_available():
            prj = self._ka_get_json_warn_empty(
                f"/apis/tekton.dev/v1/namespaces/{quote(self.args.namespace)}/pipelineruns/{quote(name)}",
                ctx="pipelinerun resolver check (archived)",
            )
        if not isinstance(prj, dict) or not prj.get("metadata", {}).get("name"):
            return False
        for cond in prj.get("status", {}).get("conditions", []) or []:
            if cond.get("type") != "Succeeded":
                continue
            if cond.get("status") != "False":
                continue
            reason = (cond.get("reason") or "").strip()
            if reason == "CouldntGetPipeline":
                return True
            msg = (cond.get("message") or "").lower()
            if "couldntgetpipeline" in msg or "resolver failed" in msg:
                return True
        return False


    def find_newest_olminstall_any_owner_for_watch(self) -> str:
        """Newest non-smoke olminstall PipelineRun for ``--konflux-app`` (any owner), same merge order as ``-l``."""
        scan = max(DEFAULT_LIST_COUNT, LIST_SUPPORTED_OCP_MAX_PRS)
        merged = self._merged_pipelinerun_rows(scan, name_substr="olminstall")
        fallback = ""
        for row in merged:
            if "olminstall" not in row.name:
                continue
            if olminstall_smoke_only_pipelinerun(row.name, ""):
                continue
            if not fallback:
                fallback = row.name
            if self._pipelinerun_resolver_unusable_for_logs(row.name, row.source):
                continue
            return row.name
        return fallback


    def get_applications(self, namespace: str) -> list[str]:
        data = parse_json_output(["oc", "get", "applications", "-n", namespace, "-o", "json"])
        return [item.get("metadata", {}).get("name", "") for item in data.get("items", []) if item.get("metadata", {}).get("name")]


    def latest_matching_image(
        self, namespace: str, app_name: str, pattern: str
    ) -> tuple[str, str, dict[str, Any] | None]:
        """Return (creationTimestamp, containerImage, snapshot metadata) for the newest matching Snapshot."""
        proc = run_cmd(
            [
                "oc",
                "get",
                "snapshots",
                "-n",
                namespace,
                "-l",
                f"appstudio.openshift.io/application={app_name}",
                "--sort-by=.metadata.creationTimestamp",
                "-o",
                "custom-columns=NAME:.metadata.name,TS:.metadata.creationTimestamp",
            ],
            capture=True,
            check=False,
            timeout=120,
        )
        if proc.returncode != 0 or not (proc.stdout or "").strip():
            return "", "", None
        rows: list[tuple[str, str]] = []
        for line in (proc.stdout or "").splitlines():
            ln = line.strip()
            if not ln or ln.upper().startswith("NAME "):
                continue
            parts = ln.split()
            if len(parts) < 2:
                continue
            ts = parts[-1]
            name = parts[0]
            if name and ts and re.match(r"^\d{4}-\d{2}-\d{2}T", ts):
                rows.append((ts, name))
        if (proc.stdout or "").strip() and not rows:
            print(
                f"WARN Snapshot list column parse skipped non-ISO timestamps for app={app_name!r}; "
                "using slow path or empty match.",
                file=sys.stderr,
            )
        if not rows:
            return "", "", None
        # Only walk the newest N snapshots per app; each oc get is small but hundreds add up.
        max_walk = 120
        scan = rows[-max_walk:] if len(rows) > max_walk else rows
        for ts, snap_name in reversed(scan):
            proc2 = run_cmd(
                [
                    "oc",
                    "get",
                    "snapshot",
                    snap_name,
                    "-n",
                    namespace,
                    "-o",
                    "jsonpath={range .spec.components[*]}{.containerImage}{'\\n'}{end}",
                ],
                capture=True,
                check=False,
                timeout=60,
            )
            if proc2.returncode != 0:
                continue
            for raw in (proc2.stdout or "").splitlines():
                img = raw.strip()
                if img and re.search(pattern, img):
                    meta_proc = run_cmd(
                        ["oc", "get", "snapshot", snap_name, "-n", namespace, "-o", "json"],
                        capture=True,
                        check=False,
                        timeout=60,
                    )
                    snap_meta = None
                    if meta_proc.returncode == 0 and (meta_proc.stdout or "").strip():
                        try:
                            snap_obj = json.loads(meta_proc.stdout)
                            snap_meta = snap_obj.get("metadata") if isinstance(snap_obj, dict) else None
                        except json.JSONDecodeError:
                            snap_meta = None
                    return ts, img, snap_meta
        if len(rows) <= max_walk:
            return "", "", None
        print(
            f"WARN No component matched {pattern!r} in the newest {max_walk} snapshots for {app_name}; "
            "falling back to full Snapshot list (may be slow).",
            file=sys.stderr,
        )
        proc_big = run_cmd(
            ["oc", "get", "snapshots", "-n", namespace, "-l", f"appstudio.openshift.io/application={app_name}", "-o", "json"],
            capture=True,
            check=False,
            timeout=300,
        )
        data: dict[str, Any] = {}
        if proc_big.returncode == 0 and (proc_big.stdout or "").strip():
            try:
                data = json.loads(proc_big.stdout)
            except json.JSONDecodeError:
                data = {}
        best_ts = ""
        best_img = ""
        best_meta: dict[str, Any] | None = None
        for item in data.get("items", []):
            ts = item.get("metadata", {}).get("creationTimestamp", "")
            for comp in item.get("spec", {}).get("components", []):
                img = comp.get("containerImage", "")
                if re.search(pattern, img):
                    if ts > best_ts:
                        best_ts = ts
                        best_img = img
                        best_meta = item.get("metadata")
        return best_ts, best_img, best_meta

    def latest_named_component_image(
        self,
        namespace: str,
        app_name: str,
        component_name: str,
        image_pattern: str,
    ) -> tuple[str, str, dict[str, Any] | None]:
        """Newest ``containerImage`` for ``component_name`` in snapshots of ``app_name``."""
        want_name = (component_name or "").strip()
        if not want_name:
            return "", "", None
        proc = run_cmd(
            [
                "oc",
                "get",
                "snapshots",
                "-n",
                namespace,
                "-l",
                f"appstudio.openshift.io/application={app_name}",
                "-o",
                "json",
            ],
            capture=True,
            check=False,
            timeout=120,
        )
        if proc.returncode == 0 and (proc.stdout or "").strip():
            try:
                data = json.loads(proc.stdout)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict):
                best_ts = ""
                best_img = ""
                best_meta: dict[str, Any] | None = None
                for item in data.get("items", []) or []:
                    if not isinstance(item, dict):
                        continue
                    ts = (item.get("metadata") or {}).get("creationTimestamp", "")
                    for comp in item.get("spec", {}).get("components", []) or []:
                        if not isinstance(comp, dict):
                            continue
                        if (comp.get("name") or "").strip() != want_name:
                            continue
                        img = (comp.get("containerImage") or "").strip()
                        if img and re.search(image_pattern, img) and ts > best_ts:
                            best_ts = ts
                            best_img = img
                            meta = item.get("metadata")
                            best_meta = meta if isinstance(meta, dict) else None
                if best_img:
                    return best_ts, best_img, best_meta
        proc_names = run_cmd(
            [
                "oc",
                "get",
                "snapshots",
                "-n",
                namespace,
                "-l",
                f"appstudio.openshift.io/application={app_name}",
                "--sort-by=.metadata.creationTimestamp",
                "-o",
                "custom-columns=NAME:.metadata.name,TS:.metadata.creationTimestamp",
            ],
            capture=True,
            check=False,
            timeout=120,
        )
        if proc_names.returncode != 0 or not (proc_names.stdout or "").strip():
            return "", "", None
        rows: list[tuple[str, str]] = []
        for line in (proc_names.stdout or "").splitlines():
            ln = line.strip()
            if not ln or ln.upper().startswith("NAME "):
                continue
            parts = ln.split()
            if len(parts) < 2:
                continue
            ts = parts[-1]
            name = parts[0]
            if name and ts and re.match(r"^\d{4}-\d{2}-\d{2}T", ts):
                rows.append((ts, name))
        if not rows:
            return "", "", None
        max_walk = 120
        scan = rows[-max_walk:] if len(rows) > max_walk else rows
        for ts, snap_name in reversed(scan):
            meta_proc = run_cmd(
                ["oc", "get", "snapshot", snap_name, "-n", namespace, "-o", "json"],
                capture=True,
                check=False,
                timeout=60,
            )
            if meta_proc.returncode != 0 or not (meta_proc.stdout or "").strip():
                continue
            try:
                snap_obj = json.loads(meta_proc.stdout)
            except json.JSONDecodeError:
                continue
            if not isinstance(snap_obj, dict):
                continue
            for comp in snap_obj.get("spec", {}).get("components", []) or []:
                if not isinstance(comp, dict):
                    continue
                if (comp.get("name") or "").strip() != want_name:
                    continue
                img = (comp.get("containerImage") or "").strip()
                if img and re.search(image_pattern, img):
                    meta = snap_obj.get("metadata") if isinstance(snap_obj.get("metadata"), dict) else None
                    return ts, img, meta
        if len(rows) <= max_walk:
            return "", "", None
        proc_big = run_cmd(
            [
                "oc",
                "get",
                "snapshots",
                "-n",
                namespace,
                "-l",
                f"appstudio.openshift.io/application={app_name}",
                "-o",
                "json",
            ],
            capture=True,
            check=False,
            timeout=300,
        )
        if proc_big.returncode != 0 or not (proc_big.stdout or "").strip():
            return "", "", None
        try:
            data = json.loads(proc_big.stdout)
        except json.JSONDecodeError:
            return "", "", None
        best_ts = ""
        best_img = ""
        best_meta: dict[str, Any] | None = None
        for item in data.get("items", []) or []:
            if not isinstance(item, dict):
                continue
            ts = (item.get("metadata") or {}).get("creationTimestamp", "")
            for comp in item.get("spec", {}).get("components", []) or []:
                if not isinstance(comp, dict):
                    continue
                if (comp.get("name") or "").strip() != want_name:
                    continue
                img = (comp.get("containerImage") or "").strip()
                if img and re.search(image_pattern, img) and ts > best_ts:
                    best_ts = ts
                    best_img = img
                    meta = item.get("metadata")
                    best_meta = meta if isinstance(meta, dict) else None
        return best_ts, best_img, best_meta


