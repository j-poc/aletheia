.DEFAULT_GOAL := help
SHELL := /bin/bash
UV ?= uv

.PHONY: help setup lint fmt types types-web test test-live test-web determinism mutants mutants-web verify ingest stats demo api web web-build web-deps study study-returns clean

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

test-web: web-deps  ## Render every page server-side and check what it claims
	cd apps/web && pnpm exec vitest run

determinism:  ## Prove a re-run reproduces byte-identical results
	$(UV) run python scripts/check_determinism.py --self-test

mutants:  ## Break each shipped fix on purpose; a test must catch every one
	$(UV) run python scripts/mutation_gate.py

mutants-web: web-deps  ## The same, for the pages and their harness; every mutant must be caught
	node scripts/web_mutation_gate.mjs

# The web targets are in the done-bar, not beside it. Leaving them out meant
# `make verify` printed a clean bill of health for a repository whose entire
# front end had no tests at all -- and the reader's only view of the warehouse
# is that front end.
verify: lint types test determinism mutants types-web test-web mutants-web  ## Everything the done-bar requires

ingest:  ## Pull real data into the warehouse
	$(UV) run aletheia ingest

stats:  ## Recompute every corpus number the README quotes
	$(UV) run python scripts/corpus_stats.py

demo:  ## Build a small 25-filer warehouse from scratch and print the proof
	$(UV) sync
	$(UV) run python scripts/demo.py

api:  ## Serve the read-only HTTP API on :8000
	$(UV) run uvicorn aletheia.api.app:app --host 127.0.0.1 --port 8000 --reload

web-deps:  ## Install the frontend's dependencies, once, from the lockfile
	@command -v pnpm >/dev/null || { \
	  echo "pnpm is required for the web targets (make test-web, mutants-web, verify)."; \
	  echo "Install it with: npm i -g pnpm@9   --   or run the Python-only bar:"; \
	  echo "    make lint types test determinism mutants"; \
	  exit 1; }
	cd apps/web && pnpm install --frozen-lockfile --prefer-offline

types-web: web-deps  ## Strict type checking for the frontend, tests included
	cd apps/web && pnpm exec tsc --noEmit

web: web-deps  ## Run the Next.js frontend on :3000 (needs `make api` in another shell)
	cd apps/web && pnpm dev

web-build: web-deps  ## Type-check and build the frontend
	cd apps/web && pnpm build

study:  ## Run the flagship study (S002, fundamentals-only — needs `make ingest` first)
	$(UV) run python scripts/run_contamination_study.py

study-returns:  ## S001, the return-predictive study. BLOCKED: needs a price entitlement (D9).
	$(UV) run python scripts/run_bias_study.py --stage symbols
	$(UV) run python scripts/run_bias_study.py --stage prices
	$(UV) run python scripts/run_bias_study.py --stage study

clean:  ## Remove build and test artefacts (never data/)
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
