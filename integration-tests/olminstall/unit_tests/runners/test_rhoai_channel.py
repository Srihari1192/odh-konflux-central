#!/usr/bin/env python3
"""Unit tests for RHOAI OLM channel auto-selection."""

from __future__ import annotations

import unittest

from runners.cli.rhoai_channel import resolve_rhoai_update_channel

class RhoaiChannelTest(unittest.TestCase):
    def test_version_35_maps_to_beta(self) -> None:
        self.assertEqual(resolve_rhoai_update_channel(version="3.5"), "beta")

    def test_resolved_app_v35_maps_to_beta(self) -> None:
        self.assertEqual(
            resolve_rhoai_update_channel(resolved_app="rhoai-v3-5-ea-2"),
            "beta",
        )

    def test_version_34_maps_to_stable_34(self) -> None:
        self.assertEqual(resolve_rhoai_update_channel(version="3.4"), "stable-3.4")

    def test_resolved_app_34_maps_to_stable_34(self) -> None:
        self.assertEqual(
            resolve_rhoai_update_channel(resolved_app="rhoai-v3-4-foo"),
            "stable-3.4",
        )

    def test_generic_v3_app_falls_back_to_stable_3x(self) -> None:
        self.assertEqual(
            resolve_rhoai_update_channel(resolved_app="rhoai-v3-foo"),
            "stable-3.x",
        )

    def test_explicit_version_overrides_non_ea_app(self) -> None:
        self.assertEqual(
            resolve_rhoai_update_channel(version="3.4", resolved_app="rhoai-v3-5-ea-2"),
            "beta",
        )
        self.assertEqual(
            resolve_rhoai_update_channel(version="3.4", resolved_app="rhoai-v3-4-foo"),
            "stable-3.4",
        )

if __name__ == "__main__":
    raise SystemExit(unittest.main())
