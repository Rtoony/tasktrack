# TaskTrack developer targets.
.PHONY: help install install-dev test test-cov lint lint-fix smoke run-dev check

PYTHON := /home/rtoony/miniconda3/bin/python3
PIP := /home/rtoony/miniconda3/bin/pip
RUFF := /home/rtoony/miniconda3/bin/ruff
PORT := 5050

help:
	@echo "make install     - install runtime deps"
	@echo "make install-dev - install runtime + dev deps"
	@echo "make test        - run pytest"
	@echo "make test-cov    - run pytest with coverage report"
	@echo "make lint        - run ruff check (read-only)"
	@echo "make lint-fix    - run ruff check --fix + ruff format"
	@echo "make check       - lint + test (used by CI)"
	@echo "make smoke       - run scripts/smoke.sh against the running service"
	@echo "make run-dev     - run the app via Flask dev server on port $(PORT)"

install:
	$(PIP) install -r requirements.txt

install-dev:
	$(PIP) install -r requirements-dev.txt

test:
	$(PYTHON) -m pytest tests/ -v

test-cov:
	$(PYTHON) -m pytest tests/ --cov=app --cov-report=term-missing --cov-report=html

lint:
	$(RUFF) check app/ tests/

lint-fix:
	$(RUFF) check --fix app/ tests/
	$(RUFF) format app/ tests/

check: lint test

smoke:
	./scripts/smoke.sh

run-dev:
	FLASK_APP=app.py $(PYTHON) -m flask run --host=0.0.0.0 --port=$(PORT)
