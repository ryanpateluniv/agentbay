VENV ?= .venv
PY := $(abspath $(VENV))/bin/python
PORT ?= 8000

.PHONY: setup seed dev test contracts demo clean

$(PY):
	python3 -m venv $(VENV)
	$(PY) -m pip install -q -r backend/requirements.txt

setup: $(PY)
	@test -f .env || cp .env.example .env

seed: setup
	cd backend && $(PY) -m app.seed --reset

# One command: install, seed a fresh DB, run the API on :8000
dev: seed
	cd backend && PUBLIC_BASE_URL=$${PUBLIC_BASE_URL:-http://127.0.0.1:$(PORT)} $(PY) -m uvicorn app.main:app --host 0.0.0.0 --port $(PORT)

test: setup
	cd backend && $(PY) -m pytest -q tests

contracts: setup
	cd backend && $(PY) -c "import yaml, json; from app.main import app; open('../contracts/openapi.yaml','w').write(yaml.safe_dump(json.loads(json.dumps(app.openapi(), default=str)), sort_keys=False))"

# Scripted buyer + creator demo against a running `make dev`
demo: setup
	cd backend && $(PY) scripts/demo.py http://127.0.0.1:$(PORT)

clean:
	rm -rf backend/data
