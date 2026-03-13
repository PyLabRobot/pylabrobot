ifeq ($(shell test -e ./env/ && echo yes),yes)
	BIN=env/bin/
$(info Using virtualenv in env)
endif

.PHONY: docs lint test

docs:
	sphinx-build -b html docs docs/build/ -j 16 -W

docs-fast:
	echo "building docs without api for speed"
	sphinx-build -t no-api -b html docs docs/build/ -j 16 -W

docs-check:
	sphinx-build -b dummy docs docs/build/ -j 16 -W

clean-docs:
	rm -rf docs/build
	rm -rf docs/_autosummary
	rm -rf docs/api/_autosummary
	rm -rf docs/jupyter_execute
	rm -rf docs/user_guide/jupyter_execute

TRACKED_PY = $(shell git ls-files 'pylabrobot/*.py' 'pylabrobot/*.ipynb')

lint:
	$(BIN)python -m ruff check $(TRACKED_PY)

format:
	$(BIN)python -m ruff format $(TRACKED_PY)
	$(BIN)python -m ruff check --fix $(TRACKED_PY) --select I

format-check:
	$(BIN)python -m ruff format --check $(TRACKED_PY)
	$(BIN)python -m ruff check $(TRACKED_PY) --select I

test:
	$(BIN)python -m pytest -s -v

typecheck:
	$(BIN)python -m mypy pylabrobot --check-untyped-defs

clear-pyc:
	find . -name "*.pyc" | xargs rm
	find . -name "*__pycache__" | xargs rm -r

llm-docs:
	./docs/combine.sh
