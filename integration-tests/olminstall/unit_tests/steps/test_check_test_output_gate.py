"""Tests for shared TEST_OUTPUT gate checks."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from steps.check_test_output_gate import check_test_output_file, check_test_output_json

class CheckTestOutputGateTest(unittest.TestCase):
    def test_warning_allows_partial_pass(self) -> None:
        ec, msg = check_test_output_json(
            {
                "result": "WARNING",
                "successes": 1,
                "failures": 0,
                "skipped": 1,
                "note": "bvt: 50% pass rate (1 passed, 0 failed, 1 skipped)",
            },
            gate_label="BVT",
            allow_all_skipped_note_prefix="bvt:",
        )
        self.assertEqual(ec, 0)
        self.assertIn("50%", msg)

    def test_strict_blocks_warning_with_failures(self) -> None:
        ec, msg = check_test_output_json(
            {
                "result": "WARNING",
                "successes": 4,
                "failures": 1,
                "note": "bvt: 80% pass rate",
            },
            gate_label="BVT",
            strict=True,
        )
        self.assertEqual(ec, 1)
        self.assertIn("strict", msg.lower())

    def test_non_strict_warning_with_failures_passes(self) -> None:
        ec, msg = check_test_output_json(
            {
                "result": "WARNING",
                "successes": 33,
                "failures": 2,
                "note": "smoke: 22% pass rate",
            },
            gate_label="Smoke",
            strict=False,
        )
        self.assertEqual(ec, 0)

    def test_smoke_gate_allows_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"
            path.write_text(
                json.dumps(
                    {
                        "result": "WARNING",
                        "successes": 33,
                        "failures": 2,
                        "skipped": 114,
                        "note": "smoke: 22% pass rate (33 passed, 2 failed, 114 skipped)",
                    }
                ),
                encoding="utf-8",
            )
            ec, msg = check_test_output_file(path, gate_label="Smoke")
            self.assertEqual(ec, 0)
            self.assertIn("22%", msg)

