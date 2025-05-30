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

clean-docs:
	rm -rf docs/build
	rm -rf docs/_autosummary
	rm -rf docs/api/_autosummary
	rm -rf docs/jupyter_execute
	rm -rf docs/user_guide/jupyter_execute

lint:
	$(BIN)python -m ruff check pylabrobot

format:
	$(BIN)python -m ruff format pylabrobot
	$(BIN)python -m ruff check --fix pylabrobot --select I

format-check:
	$(BIN)python -m ruff format --check pylabrobot
	$(BIN)python -m ruff check pylabrobot --select I

test:
	$(BIN)python -m pytest -s -v

typecheck:
	$(BIN)python -m mypy pylabrobot --check-untyped-defs

clear-pyc:
	find . -name "*.pyc" | xargs rm
	find . -name "*__pycache__" | xargs rm -r

llm-docs:
	./docs/combine.sh
