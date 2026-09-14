.PHONY: help env install test unit e2e reliability counterfactual lint migrate revision demo \
        web web-install web-build web-lint check up down clean

API := apps/api
WEB := apps/web

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "\033[36m%-16s\033[0m %s\n",$$1,$$2}'

env: ## Create .env from the template if it does not exist
	@test -f .env || (cp .env.example .env && echo "created .env from .env.example")

install: ## Install backend and frontend dependencies
	cd $(API) && pip install -r requirements-dev.txt
	cd $(WEB) && npm install

# ---- backend ----------------------------------------------------------------
dev: ## Run the API with autoreload
	cd $(API) && PYTHONPATH=. uvicorn app.main:app --reload --port 8000

test: ## Backend test suite
	cd $(API) && PYTHONPATH=. pytest -q

unit: ## Unit tests only
	cd $(API) && PYTHONPATH=. pytest tests/unit -q

e2e: ## Counterfactual + end-to-end tests
	cd $(API) && PYTHONPATH=. pytest tests/e2e -q

counterfactual: ## Prove the second call is not hard-coded
	cd $(API) && PYTHONPATH=. python scripts/counterfactual.py

reliability: ## 5-run reliability gate (run before recording the demo)
	cd $(API) && PYTHONPATH=. python scripts/reliability_gate.py

demo: ## Run the flagship mission and print the causal chain
	cd $(API) && PYTHONPATH=. python scripts/run_flagship.py

lint: ## Ruff
	cd $(API) && ruff check app tests scripts && ruff format --check app tests scripts

migrate: ## Apply migrations
	cd $(API) && alembic upgrade head

revision: ## Autogenerate a migration: make revision m="message"
	cd $(API) && alembic revision --autogenerate -m "$(m)"

# ---- frontend ---------------------------------------------------------------
web: ## Run Mission Control (expects the API on :8000)
	cd $(WEB) && npm run dev

web-install:
	cd $(WEB) && npm install

web-build: ## Production build
	cd $(WEB) && npm run build

web-lint: ## ESLint + TypeScript
	cd $(WEB) && npm run lint && npm run typecheck

# ---- everything -------------------------------------------------------------
check: lint test web-lint web-build ## Everything CI runs

up: env ## Start the full stack (postgres, redis, api, web)
	docker compose up --build

down:
	docker compose down -v

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} + ; \
	rm -f $(API)/*.db ; rm -rf $(WEB)/.next $(WEB)/*.tsbuildinfo
