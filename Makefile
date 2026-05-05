.PHONY: dev test fmt lint install

install:
	cd agent && uv sync || pip install -e ".[dev]"
	cd backend && uv sync || pip install -e ".[dev]"
	pnpm -C web install || npm --prefix web install

dev:
	bash scripts/dev.sh

test:
	cd agent && pytest -q
	cd backend && pytest -q

fmt:
	cd agent && ruff format .
	cd backend && ruff format .
	pnpm -w exec biome format --write web || true

lint:
	cd agent && ruff check .
	cd backend && ruff check .
	pnpm -w exec biome check web || true
