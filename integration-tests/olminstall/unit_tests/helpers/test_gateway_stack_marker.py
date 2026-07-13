"""Unit tests for helpers.gateway_stack_marker."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from helpers import gateway_stack_marker as marker


class GatewayStackMarkerTest(unittest.TestCase):
    def test_write_and_read_under_tests_shared(self) -> None:
        with mock.patch.dict(os.environ, {"TESTS_SHARED": "/workspace/tests-shared"}, clear=False):
            with (
                mock.patch.object(
                    marker,
                    "gateway_stack_marker_paths",
                    return_value=[Path("/workspace/tests-shared/tests-payload/results/.gateway-auth-stack-incomplete")],
                ),
                mock.patch.object(Path, "mkdir"),
                mock.patch.object(Path, "write_text") as write_text,
                mock.patch.object(Path, "is_file", return_value=True),
            ):
                marker.write_gateway_stack_incomplete_marker()
                self.assertTrue(marker.gateway_stack_incomplete())
                write_text.assert_called_once()


if __name__ == "__main__":
    unittest.main()
