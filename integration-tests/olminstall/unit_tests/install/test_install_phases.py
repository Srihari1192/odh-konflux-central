"""Unit tests for install phase loading (no cluster)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from install.install_and_verify import validate_dns_label, validate_operator_namespace

class InstallValidationTest(unittest.TestCase):
    def test_validate_operator_namespace_accepts_default(self) -> None:
        validate_operator_namespace("redhat-ods-operator")

    def test_validate_dns_label_rejects_empty(self) -> None:
        with self.assertRaises(SystemExit):
            validate_dns_label("", "TEST")

class LoadInstallContextTest(unittest.TestCase):
    def test_load_install_context_missing_env(self) -> None:
        env = {k: v for k, v in os.environ.items() if not k.startswith("INSTALL_")}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit):
                from install.install_phases import load_install_context

                load_install_context()
