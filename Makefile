.PHONY: dev test fmt lint install package fixture

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

# Build the agent .deb (Linux only). Override version with: make package MD_VERSION=1.0.1
package:
	bash packaging/build-deb.sh

# Generate fixtures/sample_drive.mcap. Requires ROS 2 sourced.
# Run once, commit the result; fresh clones reuse it.
fixture:
	cd agent && .venv/bin/python ../scripts/seed-fixture.py
