PYTHON ?= .venv/bin/python

.PHONY: test lint fmt format typecheck check pre-commit-install pre-commit-run

test:
	.venv/bin/pytest

lint:
	.venv/bin/ruff check .

fmt:
	.venv/bin/ruff format .

format: fmt

typecheck:
	.venv/bin/mypy --ignore-missing-imports --follow-imports=silent --show-column-numbers --warn-return-any --warn-unused-configs --disallow-untyped-defs --disallow-untyped-calls --no-implicit-optional --check-untyped-defs --pretty src scripts tests

check:
	.venv/bin/ruff check .
	.venv/bin/mypy --warn-return-any --warn-unused-configs --disallow-untyped-defs --no-implicit-optional --check-untyped-defs --pretty src scripts tests
	.venv/bin/pytest

pre-commit-install:
	.venv/bin/pre-commit install

pre-commit-run:
	.venv/bin/pre-commit run --all-files
