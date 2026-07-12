"""Tests for cluster_registry safe pull-secret merge."""

from __future__ import annotations

import unittest

from install.cluster_registry import merge_docker_auths, rhoai_scoped_dockerconfig


class RhoaiScopedDockerconfigTest(unittest.TestCase):
    def test_strips_bare_quay_io_from_overlay(self) -> None:
        quay = {
            "auths": {
                "quay.io": {"auth": "cmhvYWk="},
                "quay.io/rhoai": {"auth": "cmhvYWk="},
                "registry.redhat.io": {"auth": "cmg="},
            }
        }
        scoped = rhoai_scoped_dockerconfig(quay)
        self.assertIn("quay.io/rhoai", scoped["auths"])
        self.assertNotIn("quay.io", scoped["auths"])
        self.assertNotIn("registry.redhat.io", scoped["auths"])

    def test_merge_preserves_existing_bare_quay_io(self) -> None:
        existing = {"auths": {"quay.io": {"auth": "broad"}, "registry.redhat.io": {"auth": "rh"}}}
        overlay = rhoai_scoped_dockerconfig(
            {"auths": {"quay.io": {"auth": "rhoai-only"}, "quay.io/rhoai": {"auth": "rhoai-only"}}}
        )
        merged = merge_docker_auths(existing, overlay)
        self.assertEqual(merged["auths"]["quay.io"]["auth"], "broad")
        self.assertEqual(merged["auths"]["quay.io/rhoai"]["auth"], "rhoai-only")


if __name__ == "__main__":
    unittest.main()
