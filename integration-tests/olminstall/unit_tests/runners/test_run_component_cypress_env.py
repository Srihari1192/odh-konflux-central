"""Tests for component runner env file parsing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from suite.component_runner_env import load_component_runner_env


def test_load_component_runner_env(tmp_path: Path) -> None:
    path = tmp_path / "component-golang.env"
    run_command = json.dumps("npm run cypress:run")
    path.write_text(
        "\n".join(
            [
                "SKIP=false",
                "WORKING_DIR=packages/cypress",
                f"RUN_COMMAND={run_command}",
                "export ODH_DASHBOARD_URL='https://dash.example'",
                'export CYPRESS_OC_TOKEN="token123"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert load_component_runner_env(path) == {
        "SKIP": "false",
        "WORKING_DIR": "packages/cypress",
        "RUN_COMMAND": "npm run cypress:run",
        "ODH_DASHBOARD_URL": "https://dash.example",
        "CYPRESS_OC_TOKEN": "token123",
    }
