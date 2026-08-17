"""Trainer smoke patches for EaaS IDMS registry.redhat.io/rhoai mirror parity."""

from __future__ import annotations

_RUNTIME_TEST = "trainer/cluster_training_runtimes_test.go"
_SMOKE_TEST = "trainer/trainer_smoke_test.go"


def _sed_replace(path: str, old: str, new: str) -> str:
    return (
        f"if [ -f {path} ]; then "
        f"sed -i 's#{old}#{new}#g' {path} && "
        f"echo 'trainer: patched {path} for EaaS RHOAI IDMS parity'; "
        "fi"
    )


def trainer_smoke_rhoai_idms_patch_shell() -> str:
    return " && ".join(
        [
            _sed_replace(
                _RUNTIME_TEST,
                "expectedImage := imagePrefix + \"/\" + expectedRuntime.Image",
                'expectedImage := strings.Replace(imagePrefix + "/" + expectedRuntime.Image, "quay.io/rhoai/", "registry.redhat.io/rhoai/", 1)',
            ),
            _sed_replace(
                _SMOKE_TEST,
                'runSmoke(t, "kubeflow-trainer-controller-manager", "odh-trainer", "trainer")',
                'runSmoke(t, "kubeflow-trainer-controller-manager", "odh-trainer", "odh-trainer")',
            ),
        ]
    )


def prepend_trainer_smoke_patch(run_command: str) -> str:
    cmd = (run_command or "").strip()
    if not cmd:
        return cmd
    return f"{trainer_smoke_rhoai_idms_patch_shell()} && {cmd}"
