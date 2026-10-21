PYTHON ?= $(if $(wildcard ../../.venv/bin/python),../../.venv/bin/python,python3)
SOURCE ?= ../omdb-old/www-omdb-data
DATABASE ?= data/omdb
HOST ?= 127.0.0.1
PORT ?= 8080

.PHONY: install build_database serve ci format format-check lint test test-js test-browser test-real-data
install:
	$(PYTHON) -m pip install --no-deps -e .
build_database:
	$(PYTHON) -m omdb.migrate --source "$(SOURCE)" --output "$(DATABASE)" $(BUILD_ARGS)
serve:
	$(PYTHON) -m omdb.cli --database "$(DATABASE)" --host "$(HOST)" --port "$(PORT)"
format:
	$(PYTHON) -m ruff check omdb site tests --fix
	$(PYTHON) -m ruff format omdb site tests
format-check:
	$(PYTHON) -m ruff format --check omdb site tests
lint:
	$(PYTHON) -m ruff check omdb site tests
test:
	$(PYTHON) -m pytest -q
test-js:
	node --test tests-js/
test-browser:
	$(PYTHON) -m pytest -q -m browser --override-ini addopts=
test-real-data:
	$(PYTHON) -m pytest -q -m real_data --override-ini addopts=
ci: format-check lint test test-js
