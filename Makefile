.DEFAULT_GOAL := help
SHELL := /bin/bash
UV ?= uv

.PHONY: help setup lint fmt types test test-live determinism verify ingest study clean

help:  ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup:  ## Install the workspace and its dev dependencies
	$(UV) sync

lint:  ## Static checks that do not execute the code
	$(UV) run ruff check .
	$(UV) run ruff format --check .

fmt:  ## Apply formatting and safe autofixes
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

types:  ## Strict type checking
	$(UV) run mypy packages/engine/src packages/trialkeeper/src

test:  ## Full suite, excluding tests that hit live external APIs
	$(UV) run pytest -m "not live"

test-live:  ## Contract tests against the real APIs. Costs quota; run deliberately.
	$(UV) run pytest -m live -v

determinism:  ## Prove a re-run reproduces byte-identical results
	$(UV) run python scripts/check_determinism.py --self-test

verify: lint types test determinism  ## Everything the done-bar requires

ingest:  ## Pull real data into the warehouse
	$(UV) run aletheia ingest

study:  ## Run the flagship data-vintage study end to end
	$(UV) run python scripts/run_bias_study.py --stage symbols
	$(UV) run python scripts/run_bias_study.py --stage prices
	$(UV) run python scripts/run_bias_study.py --stage study

clean:  ## Remove build and test artefacts (never data/)
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
