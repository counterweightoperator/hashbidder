.PHONY: format lint typecheck check

format:
	uv run ruff format .
	uv run ruff check --select I --fix .

lint:
	uv run ruff check .

typecheck:
	uv run mypy .

check: format lint typecheck
