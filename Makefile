.DEFAULT_GOAL := help
SHELL := /bin/bash

BACKEND := backend
FRONTEND := frontend
PY := $(BACKEND)/.venv/bin/python
PIP := $(BACKEND)/.venv/bin/pip
IMAGE ?= kalliope
DATA ?= $(CURDIR)/.data

export DATA_DIR ?= $(DATA)

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ------------------------------------------------------------------- setup

.PHONY: install
install: install-backend install-frontend ## Install backend and frontend dependencies

.PHONY: install-backend
install-backend: ## Create the venv and install the backend
	python3 -m venv $(BACKEND)/.venv
	$(PIP) install -q --upgrade pip
	cd $(BACKEND) && .venv/bin/pip install -q -e ".[dev]"

.PHONY: install-frontend
install-frontend: ## Install the console's dependencies
	cd $(FRONTEND) && npm install

# --------------------------------------------------------------------- run

.PHONY: dev
dev: ## Run the API with reload (console: make dev-ui)
	cd $(BACKEND) && .venv/bin/uvicorn app.main:app --reload --port 8000

.PHONY: dev-ui
dev-ui: ## Run the console dev server against localhost:8000
	cd $(FRONTEND) && npm run dev

.PHONY: user
user: ## Create a user: make user EMAIL=you@example.com ROLE=admin
	cd $(BACKEND) && .venv/bin/python -m app.cli create-user --email "$(EMAIL)" --role "$(or $(ROLE),reviewer)"

.PHONY: doctor
doctor: ## Report whether the environment is usable
	cd $(BACKEND) && .venv/bin/python -m app.cli doctor

# ------------------------------------------------------------------- build

.PHONY: build-ui
build-ui: ## Build the console into frontend/dist
	cd $(FRONTEND) && npm run build

.PHONY: docker
docker: ## Build the container image
	docker build -t $(IMAGE) .

.PHONY: docker-run
docker-run: ## Run the container against a named volume
	docker run --rm -p 8000:8000 -v kalliope-data:/data --env-file .env $(IMAGE)

# -------------------------------------------------------------------- test

.PHONY: test
test: ## Run the backend suite (skips the holdout corpus)
	cd $(BACKEND) && .venv/bin/pytest -q

.PHONY: test-holdout
test-holdout: ## Final acceptance pass, including the holdout corpus (§13.5)
	cd $(BACKEND) && KALLIOPE_HOLDOUT=1 .venv/bin/pytest -q -m "corpus or holdout or not corpus"

.PHONY: test-llm
test-llm: ## Run the tests that need live LLM credentials
	cd $(BACKEND) && KALLIOPE_LLM_TESTS=1 .venv/bin/pytest -q -m llm

.PHONY: test-ui
test-ui: ## Run the console unit tests
	cd $(FRONTEND) && npm test

.PHONY: corpus
corpus: ## Print the corpus coverage summary (§13.1)
	cd $(BACKEND) && .venv/bin/pytest -q tests/test_corpus_summary.py -s

# ------------------------------------------------------------------ checks

.PHONY: lint
lint: ## Ruff and mypy
	cd $(BACKEND) && .venv/bin/ruff check app tests && .venv/bin/ruff format --check app tests
	cd $(BACKEND) && .venv/bin/mypy app

.PHONY: format
format: ## Apply ruff formatting
	cd $(BACKEND) && .venv/bin/ruff format app tests && .venv/bin/ruff check --fix app tests

.PHONY: typecheck-ui
typecheck-ui: ## Type-check the console
	cd $(FRONTEND) && npm run typecheck

.PHONY: check
check: lint test typecheck-ui ## Everything CI runs

# ------------------------------------------------------------------- misc

.PHONY: migration
migration: ## New migration: make migration M="add x"
	cd $(BACKEND) && .venv/bin/alembic revision --autogenerate -m "$(M)"

.PHONY: migrate
migrate: ## Apply migrations
	cd $(BACKEND) && .venv/bin/alembic upgrade head

.PHONY: clean
clean: ## Remove build output and caches
	rm -rf $(FRONTEND)/dist $(BACKEND)/.pytest_cache $(BACKEND)/.mypy_cache
	find $(BACKEND) -name __pycache__ -type d -prune -exec rm -rf {} +
