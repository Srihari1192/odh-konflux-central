#!/usr/bin/env python3
"""Tests for trainer smoke patch on EaaS IDMS registry.redhat.io/rhoai mirror parity."""

from __future__ import annotations

import unittest

from components.trainer.smoke import (  # noqa: E402
    prepend_trainer_smoke_patch,
    trainer_smoke_rhoai_idms_patch_shell,
)

class TrainerSmokeTest(unittest.TestCase):
    def test_patch_targets_runtime_and_smoke_tests(self) -> None:
        shell = trainer_smoke_rhoai_idms_patch_shell()
        self.assertIn("expectedImage := imagePrefix +", shell)
        self.assertIn('expectedImage := strings.Replace(imagePrefix + "/" + expectedRuntime.Image', shell)
        self.assertIn('"odh-trainer", "odh-trainer")', shell)

    def test_sed_uses_hash_delimiter(self) -> None:
        shell = trainer_smoke_rhoai_idms_patch_shell()
        self.assertIn("sed -i 's#", shell)

    def test_prepend_wraps_run_command(self) -> None:
        out = prepend_trainer_smoke_patch("bash run-test.sh ./trainer")
        self.assertTrue(out.startswith("if [ -f trainer/cluster_training_runtimes_test.go ]"))
        self.assertTrue(out.endswith("bash run-test.sh ./trainer"))

