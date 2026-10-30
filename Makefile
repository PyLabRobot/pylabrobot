ifeq ($(shell test -e ./env/ && echo yes),yes)
	BIN=env/bin/
$(info Using virtualenv in env)
endif

.PHONY: docs docs-fast docs-check docs-linkcheck clean-docs lint lint-js format-js test generate-rpc

docs:
	sphinx-build -b html docs docs/build/ -j 16 -W

docs-fast:
	echo "building docs without api for speed"
	sphinx-build -t no-api -b html docs docs/build/ -j 16 -W

docs-check:
	sphinx-build -b dummy docs docs/build/ -j 16 -W

docs-linkcheck:
	sphinx-build -b linkcheck docs docs/build/linkcheck -j 16 -W

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

# The 3D viewer's JavaScript and CSS, with Biome (https://biomejs.dev), configured in
# pylabrobot/visualizer3D/biome.json. Needs the `biome` binary, e.g. `brew install biome`.
lint-js:
	cd pylabrobot/visualizer3D && biome ci .

format-js:
	cd pylabrobot/visualizer3D && biome check --write .

# The browser tests draw in software on CI, against timeouts set for a runner of their own, so
# they run after the rest, alone. One OpenMP thread per tesseract keeps OCR within its timeout.
test:
	OMP_THREAD_LIMIT=1 $(BIN)python -m pytest -n auto -v --ignore=pylabrobot/visualizer3D/browser_tests.py
	$(BIN)python -m pytest -v pylabrobot/visualizer3D/browser_tests.py

typecheck:
	$(BIN)python -m mypy pylabrobot

generate-rpc:
	$(BIN)python -m grpc_tools.protoc -I. --python_out=. --pyi_out=. --grpc_python_out=. --mypy_grpc_out=. \
		pylabrobot/rpc/v1/device.proto pylabrobot/rpc/v1/jobs.proto \
		pylabrobot/rpc/v1/images.proto pylabrobot/revvity/celigo/grpc/v1/service.proto \
		pylabrobot/revvity/celigo/grpc/v1/storage.proto \
		pylabrobot/agilent/vspin/grpc/v1/service.proto

clear-pyc:
	find . -name "*.pyc" | xargs rm
	find . -name "*__pycache__" | xargs rm -r
