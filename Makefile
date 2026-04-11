.PHONY: format lint typecheck imports test check

format:
	uv run ruff format .
	uv run ruff check --select I --fix .

lint:
	uv run ruff check .

typecheck:
	uv run mypy .

imports:
	uv run lint-imports

test:
	uv run pytest -v

check: format lint typecheck imports test
