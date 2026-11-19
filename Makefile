.PHONY: install dev test test-unit lint fmt typecheck migrate dashboard seed bench up down

install:
	uv sync
	cd dashboard && pnpm install

dev:
	SWITCHBOARD_AUTH_ENABLED=false uv run switchboard serve

test:
	uv run pytest -q

test-unit:
	uv run pytest tests/unit -q

lint:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy
	cd dashboard && pnpm lint && pnpm typecheck

fmt:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy

migrate:
	uv run switchboard db upgrade

dashboard:
	cd dashboard && pnpm build

seed:
	uv run python scripts/seed_demo.py

bench:
	bash benchmarks/run.sh

up:
	docker compose -f deploy/docker-compose.yml up --build

down:
	docker compose -f deploy/docker-compose.yml down
