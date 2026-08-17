"""Resolve or clone the olminstall repo for dependency install scripts."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from steps.tekton_util import git_clone
from suite.constants import DEFAULT_OLMINSTALL_REPO_REVISION, DEFAULT_OLMINSTALL_REPO_URL

_OLMINSTALL_DEST = Path("/tmp/olminstall-maas-prereqs")
_POST_INSTALL_SCRIPT = "resources/post-install-rhcl-operator.sh"


def resolve_olminstall_dir(*, require_post_install_script: bool = True) -> Path:
    """Return olminstall checkout (Tekton OLMINSTALL_DIR or clone under /tmp)."""
    existing = os.environ.get("OLMINSTALL_DIR", "").strip()
    if existing:
        path = Path(existing)
        script = path / _POST_INSTALL_SCRIPT
        if require_post_install_script and not script.is_file():
            raise FileNotFoundError(
                f"OLMINSTALL_DIR={existing!r} has no {_POST_INSTALL_SCRIPT}"
            )
        return path

    dest = _OLMINSTALL_DEST
    script = dest / _POST_INSTALL_SCRIPT
    url = os.environ.get("OLMINSTALL_REPO_URL", "").strip() or DEFAULT_OLMINSTALL_REPO_URL
    rev = os.environ.get("OLMINSTALL_REPO_REVISION", "").strip() or DEFAULT_OLMINSTALL_REPO_REVISION
    marker = dest / ".checkout_ref"
    stale = marker.is_file() and marker.read_text().strip() != f"{url}@{rev}"
    if not script.is_file() or stale:
        if dest.exists():
            shutil.rmtree(dest)
        print(f"Cloning olminstall ({url} @ {rev})...", flush=True)
        git_clone(url, rev, dest, tls_workaround=True)
        marker.write_text(f"{url}@{rev}")
        if require_post_install_script and not script.is_file():
            raise FileNotFoundError(f"Missing {script} after olminstall clone")
    return dest
