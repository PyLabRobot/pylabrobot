ifeq ($(shell test -e ./env/ && echo yes),yes)
	BIN=env/bin/
$(info Using virtualenv in env)
endif

.PHONY: docs lint test

docs:
	rm -rf docs/build
	rm -rf docs/_autosummary
	sphinx-build -b html docs docs/build/ -j auto -W

lint:
	$(BIN)python -m pylint pylabrobot

test:
	$(BIN)python -m pytest -s -v

typecheck:
	$(BIN)python -m mypy pylabrobot --check-untyped-defs

clear-pyc:
	find . -name "*.pyc" | xargs rm
	find . -name "*__pycache__" | xargs rm -r

