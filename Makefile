SHELL := /bin/sh

PYTHON ?= python3
VENV_PYTHON := .venv/bin/python

.PHONY: install format format-check lint typecheck test check infra-up infra-down infra-validate \
 db-upgrade db-downgrade db-current seed-leads events-inspect test-events dashboard-check dashboard-dev evaluate demo

install: .venv
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -e '.[dev]'

.venv:
	$(PYTHON) -m venv .venv

format:
	$(VENV_PYTHON) -m ruff format .

format-check:
	$(VENV_PYTHON) -m ruff format --check .

lint:
	$(VENV_PYTHON) -m ruff check .

typecheck:
	$(VENV_PYTHON) -m mypy packages services/lead-service/src services/conversation-service/src apps/api-gateway/src workers tests scripts infrastructure/postgres/alembic

test:
	$(VENV_PYTHON) -m pytest

check: format-check lint typecheck test infra-validate

infra-up:
	docker compose up -d postgres redis

infra-down:
	docker compose down

infra-validate:
	docker compose config --quiet

db-upgrade:
	@test -n "$(DATABASE_URL)" || (echo "DATABASE_URL is required" && exit 1)
	DATABASE_URL="$(DATABASE_URL)" $(VENV_PYTHON) -m alembic upgrade head

db-downgrade:
	@test -n "$(DATABASE_URL)" || (echo "DATABASE_URL is required" && exit 1)
	DATABASE_URL="$(DATABASE_URL)" $(VENV_PYTHON) -m alembic downgrade -1

db-current:
	@test -n "$(DATABASE_URL)" || (echo "DATABASE_URL is required" && exit 1)
	DATABASE_URL="$(DATABASE_URL)" $(VENV_PYTHON) -m alembic current

seed-leads:
	@test -n "$(DATABASE_URL)" || (echo "DATABASE_URL is required" && exit 1)
	PYTHONPATH="services/lead-service/src:packages/configuration:packages/database:packages/contracts" \
	DATABASE_URL="$(DATABASE_URL)" $(VENV_PYTHON) -m lead_service.seed

events-inspect:
	docker compose exec event-relay python -m event_relay inspect

test-events:
	$(VENV_PYTHON) -m pytest tests/unit/test_event_contracts.py tests/integration/test_events.py

dashboard-check:
	npm run dashboard:check
	npm run dashboard:build

dashboard-dev:
	npm run dashboard:dev

EVALUATION_SUITE ?= core
evaluate:
	$(VENV_PYTHON) -m scripts.evaluate --suite $(EVALUATION_SUITE)

demo:
	$(VENV_PYTHON) -m scripts.demo
