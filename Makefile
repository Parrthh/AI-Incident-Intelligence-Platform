.DEFAULT_GOAL := help
.PHONY: help install lint format typecheck test test-unit check run migrate dev dev-down dev-logs

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

install: ## Install the project and dev tools into .venv
	uv sync

lint: ## Lint and check formatting
	uv run ruff check .
	uv run ruff format --check .

format: ## Auto-fix lint issues and format code
	uv run ruff check --fix .
	uv run ruff format .

typecheck: ## Static type check
	uv run pyright

test: ## Run all tests (integration tests need `make dev` running, otherwise they skip)
	uv run pytest --cov=iip --cov-report=term-missing

test-unit: ## Run only fast unit tests
	uv run pytest tests/unit

check: lint typecheck test ## Everything CI runs

run: ## Run the API on the host with readable logs (uses .env)
	IIP_LOG_JSON=false uv run iip api

migrate: ## Apply database migrations (uses .env)
	uv run iip migrate

COMPOSE = docker compose -f deploy/compose/docker-compose.dev.yml

dev: ## Start API + Postgres + Redis in Docker
	$(COMPOSE) up --build -d
	@echo "API: http://127.0.0.1:8000/readyz   docs: http://127.0.0.1:8000/docs"

dev-down: ## Stop the dev stack (keeps data)
	$(COMPOSE) down

dev-logs: ## Follow logs from the dev stack
	$(COMPOSE) logs -f
