"""KFTO smoke patches for ROSA HCP / Kyverno quay.io/rhoai mirror (Jenkins registry parity)."""

from __future__ import annotations

_KFTO_SMOKE_REL = "kfto/kfto_smoke_test.go"
_OLD_PREFIX_CHECK = 'strings.HasPrefix(imagePrefix, "quay.io")'
_NEW_PREFIX_CHECK = 'strings.HasPrefix(imagePrefix, "quay.io/opendatahub")'


def kfto_smoke_rhoai_quay_patch_shell() -> str:
    """Return a shell fragment that patches bundled KFTO smoke sources before ``go test``."""
    return (
        f'if [ -f {_KFTO_SMOKE_REL} ] && grep -Fq {_OLD_PREFIX_CHECK!r} {_KFTO_SMOKE_REL}; then '
        f"sed -i 's#{_OLD_PREFIX_CHECK}#{_NEW_PREFIX_CHECK}#' {_KFTO_SMOKE_REL} && "
        'echo "distributed_workloads: patched kfto_smoke_test.go for quay.io/rhoai RHOAI HCP mirror"; '
        "fi"
    )


def prepend_kfto_smoke_patch(run_command: str) -> str:
    cmd = (run_command or "").strip()
    if not cmd:
        return cmd
    return f"{kfto_smoke_rhoai_quay_patch_shell()} && {cmd}"
