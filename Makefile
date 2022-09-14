ifeq ($(shell test -e ./env/ && echo yes),yes)
	BIN=env/bin/
$(info Using virtualenv in env)
endif

.PHONY: docs lint test

docs:
	sphinx-build -b html docs docs/build/ -j auto

lint:
	$(BIN)python -m pylint pylabrobot

test:
	$(BIN)python -m pytest -s -v
