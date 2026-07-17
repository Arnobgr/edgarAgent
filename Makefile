.PHONY: lint typecheck test check run langfuse-up langfuse-down

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

typecheck:
	uv run mypy

test:
	uv run pytest

check: lint typecheck test

run:
	uv run fie ask "What were Apple's main revenue segments last fiscal year?"

langfuse-up:
	docker compose -f docker-compose.dev.yml up -d

langfuse-down:
	docker compose -f docker-compose.dev.yml down
