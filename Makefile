ifeq (,$(wildcard /env/bin)) # check if virtualenv exists
	BIN=env/bin/
$(info Using virtualenv in env)
endif

docs:
	$(BIN)python sphinx-build -b html docs docs/build/ -j auto

lint:
	$(BIN)python -m pylint pylabrobot

test:
	$(BIN)python -m pytest -s -v
