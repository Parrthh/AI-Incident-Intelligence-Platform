.DEFAULT_GOAL := help
.PHONY: help install lint format typecheck test check run

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

test: ## Run the test suite with coverage
	uv run pytest --cov=iip --cov-report=term-missing

check: lint typecheck test ## Everything CI runs

run: ## Run the API locally with readable logs
	IIP_LOG_JSON=false uv run iip api
