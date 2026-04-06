.PHONY: format lint typecheck test check

format:
	uv run ruff format .
	uv run ruff check --select I --fix .

lint:
	uv run ruff check .

typecheck:
	uv run mypy .

test:
	uv run pytest -v

check: format lint typecheck test
