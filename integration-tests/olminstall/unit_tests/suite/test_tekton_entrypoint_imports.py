"""Import checks for every Tekton ``python -m`` entrypoint (see suite/tekton_python_entrypoints.py).

Catches eager imports of smoke-only deps (e.g. ``maas_billing.uwm`` → PyYAML) that pass
local unit tests because ``requirements.txt`` installs PyYAML but the opendatahub-tests
image does not.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

from suite.tekton_python_entrypoints import discover_tekton_python_entrypoints
from unit_tests._paths import OLMINSTALL_ROOT

_BLOCK_PYYAML = textwrap.dedent(
    """
    import builtins
    real_import = builtins.__import__
    def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.split('.', 1)[0] == 'yaml':
            raise ImportError('simulated missing PyYAML (opendatahub-tests image)')
        return real_import(name, globals, locals, fromlist, level)
    builtins.__import__ = blocked_import
    """
)

_FORBIDDEN_AT_IMPORT = ("components.maas_billing.uwm",)

def _run_probe(*parts: str) -> subprocess.CompletedProcess[str]:
    script = "".join(textwrap.dedent(part) for part in parts)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(OLMINSTALL_ROOT)
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=OLMINSTALL_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "module" not in metafunc.fixturenames:
        return
    modules = discover_tekton_python_entrypoints()
    assert modules, "expected at least one Tekton python -m entrypoint"
    if "lean_image" in metafunc.fixturenames:
        cases = [(name, lean) for name, lean in sorted(modules.items()) if lean]
        metafunc.parametrize(
            "module,lean_image",
            cases,
            ids=[name for name, _ in cases],
        )
        return
    metafunc.parametrize("module", sorted(modules), ids=sorted(modules))

def test_tekton_entrypoint_import(module: str) -> None:
    proc = _run_probe(
        f"""
        import importlib
        importlib.import_module({module!r})
        """
    )
    assert proc.returncode == 0, f"{module}: {proc.stderr or proc.stdout}"

def test_lean_image_entrypoint_import_without_pyyaml(module: str, lean_image: bool) -> None:
    assert lean_image
    proc = _run_probe(
        _BLOCK_PYYAML,
        f"""
        import importlib
        import sys
        importlib.import_module({module!r})
        for forbidden in {list(_FORBIDDEN_AT_IMPORT)!r}:
            assert forbidden not in sys.modules, forbidden
        """,
    )
    assert proc.returncode == 0, f"{module}: {proc.stderr or proc.stdout}"

def test_eager_maas_prep_import_requires_pyyaml() -> None:
    """Document why lazy-import in component_prereqs matters for lean-image entrypoints."""
    proc = _run_probe(
        _BLOCK_PYYAML,
        """
        import importlib
        try:
            importlib.import_module('components.maas_billing.prep')
        except ImportError as exc:
            assert 'PyYAML' in str(exc)
        else:
            raise SystemExit('expected ImportError for eager maas prep without PyYAML')
        """,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
