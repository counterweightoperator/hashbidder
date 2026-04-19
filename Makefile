.PHONY: format lint typecheck imports test check

DIRS := hashbidder tests

format:
	uv run ruff format $(DIRS)
	uv run ruff check --select I --fix $(DIRS)

lint:
	uv run ruff check $(DIRS)

typecheck:
	uv run mypy $(DIRS)

imports:
	uv run lint-imports

test:
	uv run pytest -v

check: format lint typecheck imports test
