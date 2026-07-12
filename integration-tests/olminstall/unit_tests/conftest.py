"""Pipeline unit tests for integration-tests/olminstall (no cluster)."""

from __future__ import annotations

import pytest

pytest_plugins = ["unit_tests.runners.olm_cli_fixtures"]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        item.add_marker(pytest.mark.unit)
