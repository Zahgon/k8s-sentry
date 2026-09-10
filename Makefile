PY ?= python3
VENV := .venv
BIN := $(VENV)/bin

.PHONY: venv test lint typecheck compile truth coverage check clean

venv:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e ".[dev]"

test:
	PYTHONPATH=src $(BIN)/python -m pytest tests -q

lint:
	$(BIN)/ruff check src tests verification
	$(BIN)/ruff format --check src tests verification

typecheck:
	$(BIN)/mypy

compile:
	$(BIN)/python -m compileall -q src

# Re-records go-truth.json from the real Go program. Needs Go on PATH; refuses
# to run if it would leave the read-only Go checkout dirty.
truth:
	$(BIN)/python verification/truth_inputs.py
	./verification/gen-go-truth.sh

coverage:
	PYTHONPATH=src $(BIN)/python -m pytest tests -q --cov=k8ssentry --cov-report=term-missing

check: compile lint typecheck test

clean:
	rm -rf $(VENV) .pytest_cache .mypy_cache .ruff_cache verification/py-truth.json
	find . -name __pycache__ -type d -exec rm -rf {} +
