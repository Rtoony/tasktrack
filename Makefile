# TaskTrack developer targets.
.PHONY: help install install-dev test smoke run-dev

PYTHON := /home/rtoony/miniconda3/bin/python3
PIP := /home/rtoony/miniconda3/bin/pip
PORT := 5050

help:
	@echo "make install     - install runtime deps"
	@echo "make install-dev - install runtime + dev deps"
	@echo "make test        - run pytest"
	@echo "make smoke       - run scripts/smoke.sh against the running service"
	@echo "make run-dev     - run the app via Flask dev server on port $(PORT)"

install:
	$(PIP) install -r requirements.txt

install-dev:
	$(PIP) install -r requirements-dev.txt

test:
	$(PYTHON) -m pytest tests/ -v

smoke:
	./scripts/smoke.sh

run-dev:
	FLASK_APP=app.py $(PYTHON) -m flask run --host=0.0.0.0 --port=$(PORT)
