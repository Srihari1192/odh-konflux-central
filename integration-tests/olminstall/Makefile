# olminstall pipeline unit tests (no oc / cluster required).
# Run from this directory: make test
# Or from repo root: make -C integration-tests/olminstall test

PYTHON ?= python3
PYTEST ?= pytest

.PHONY: help test test-cli deps

help:
	@echo "olminstall local unit tests (pytest; no Konflux cluster)"
	@echo ""
	@echo "  make deps      Install Python test deps (requirements.txt)"
	@echo "  make test      Run full unit test suite"
	@echo "  make test-cli  Run CLI argument tests only"
	@echo ""
	@echo "Equivalent: cd $$(pwd) && pytest -q"

deps:
	$(PYTHON) -m pip install -r requirements.txt

test:
	$(PYTEST) -q --tb=short

test-cli:
	$(PYTEST) unit_tests/runners/test_olm_cli.py -q --tb=short
