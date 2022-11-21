ifeq ($(shell test -e ./env/ && echo yes),yes)
	BIN=env/bin/
$(info Using virtualenv in env)
endif

ifeq ($(shell test -e ./env/ && echo yes),yes)
	BIN3.7=env3.7/bin/
$(info Using virtualenv version 3.7 in env 3.7)
endif

.PHONY: docs lint test

docs:
	rm -rf docs/build
	rm -rf docs/_autosummary
	sphinx-build -b html docs docs/build/ -j auto

lint:
	$(BIN)python -m pylint pylabrobot

test:
	$(BIN)python -m pytest -s -v

env3.7:
	python3.7 -m virtualenv env3.7
	$(BIN3.7)python3 -m pip install -e '.[testing]'

test3.7:
	$(BIN3.7)python3 -m pytest -s -v

typecheck:
	$(BIN)python -m mypy pylabrobot --check-untyped-defs
