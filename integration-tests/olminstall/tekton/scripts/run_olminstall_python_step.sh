#!/usr/bin/env bash
# Run a Python module under integration-tests/olminstall from Tekton component tasks.
# Usage: run_olminstall_python_step.sh steps.summarize_test_output
# Env: SCRIPTS_REPO_ROOT (olminstall checkout path).
set -euo pipefail

module="${1:-}"
if [[ -z "$module" ]]; then
  echo "usage: run_olminstall_python_step.sh PYTHON_MODULE" >&2
  exit 2
fi

root="${SCRIPTS_REPO_ROOT:-}"
if [[ -z "$root" ]]; then
  echo "ERROR: SCRIPTS_REPO_ROOT is required" >&2
  exit 1
fi

cd "$root"
exec python3 -m "$module"
