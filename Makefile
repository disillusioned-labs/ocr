.PHONY: sync generate lint fmt typecheck test test-integration migrate-up migrate-down run-api run-worker run-outbox help

sync: ## Install/sync all dependencies into .venv
	uv sync --all-groups

generate: ## Generate Python gRPC stubs from proto/ (run before test/run)
	uv run python scripts/generate_proto.py

lint: ## ruff check
	uv run ruff check src tests

fmt: ## ruff format + fix
	uv run ruff format src tests
	uv run ruff check --fix src tests

typecheck: ## mypy strict
	uv run mypy src

test: ## unit tests (no infra, no Baidu calls)
	uv run pytest -m "not integration" -q

test-integration: ## full suite incl. testcontainers/live infra
	uv run pytest -q

migrate-up: ## alembic upgrade head (needs OCR_DATABASE_DSN)
	uv run alembic upgrade head

migrate-down: ## alembic downgrade -1
	uv run alembic downgrade -1

run-api: ## run the gRPC api process
	uv run ocr-api

run-worker: ## run the SAQ worker
	uv run ocr-worker

run-outbox: ## run the outbox publisher
	uv run ocr-outbox
