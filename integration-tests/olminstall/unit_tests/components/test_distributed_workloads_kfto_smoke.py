#!/usr/bin/env python3
"""Tests for KFTO smoke patch on quay.io/rhoai HCP clusters."""

from __future__ import annotations

import unittest

from components.distributed_workloads.kfto_smoke import (  # noqa: E402
    kfto_smoke_rhoai_quay_patch_shell,
    prepend_kfto_smoke_patch,
)

class DistributedWorkloadsKftoSmokeTest(unittest.TestCase):
    def test_patch_targets_odh_only_quay_prefix(self) -> None:
        shell = kfto_smoke_rhoai_quay_patch_shell()
        self.assertIn("quay.io/opendatahub", shell)
        self.assertIn("kfto/kfto_smoke_test.go", shell)
        self.assertIn("sed -i 's#", shell)

    def test_sed_uses_hash_delimiter_for_slashes(self) -> None:
        shell = kfto_smoke_rhoai_quay_patch_shell()
        self.assertNotIn("sed -i 's/strings.HasPrefix", shell)
        self.assertIn('s#strings.HasPrefix(imagePrefix, "quay.io")#', shell)

    def test_prepend_wraps_run_command(self) -> None:
        out = prepend_kfto_smoke_patch("bash run-test.sh ./kfto")
        self.assertTrue(out.startswith("if [ -f kfto/kfto_smoke_test.go ]"))
        self.assertTrue(out.endswith("bash run-test.sh ./kfto"))

