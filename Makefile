.PHONY: sample-data install lint format typecheck test test-unit test-integration check clean opencode-install

PY ?= .venv/bin/python
ifeq ($(OS),Windows_NT)
PY = .venv/Scripts/python
endif

install:
	uv venv --python 3.12 .venv
	uv pip install --python $(PY) -e ".[dev]"
	$(PY) -m pre_commit install

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

format:
	$(PY) -m ruff format .
	$(PY) -m ruff check --fix .

typecheck:
	$(PY) -m mypy

test:
	$(PY) -m pytest

test-unit:
	$(PY) -m pytest tests/unit

test-integration:
	$(PY) -m pytest -m integration

check: lint typecheck test

sample-data:
	$(PY) examples/sales/generate_data.py

# Copy skills to the directory OpenCode discovers them from. Agents are referenced
# directly by opencode/config/opencode.json via {file:...}.
opencode-install:
	mkdir -p .opencode/skill
	cp -r opencode/skills/* .opencode/skill/

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache build dist htmlcov .coverage
