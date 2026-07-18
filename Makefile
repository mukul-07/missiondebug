.PHONY: dev test fmt lint install clean-web package package-agent package-backend package-web

install: clean-web
	cd agent && uv sync || pip install -e ".[dev]"
	cd backend && uv sync || pip install -e ".[dev]"
	# Backend tests reuse the agent's mcap_writer to seed sessions; install
	# the agent into backend's venv as an editable dep so tests can import it.
	cd backend && .venv/bin/python -m pip install -e ../agent || true
	pnpm -C web install || npm --prefix web install

# Remove stale compiled .js artifacts under web/src/ that can shadow .tsx
# sources in Vite's module resolution (Vite prefers .js over .tsx when both
# exist for `import "./Foo"`). Workers are excluded — those .js files are
# real source, not build output. Gitignored since 2026-05, but any tree that
# was populated before that rule landed still has them locally.
clean-web:
	find web/src -name "*.js" -not -path "*/workers/*" -delete 2>/dev/null || true

dev:
	bash scripts/dev.sh

test:
	# Unset PYTHONPATH so each project's venv is isolated from a sourced
	# ROS environment that would otherwise leak in pytest plugins (e.g. ROS's
	# launch_testing pulls 'lark' which our backend venv doesn't ship).
	cd agent   && env -u PYTHONPATH .venv/bin/pytest -q
	cd backend && env -u PYTHONPATH .venv/bin/pytest -q

fmt:
	cd agent && ruff format .
	cd backend && ruff format .
	pnpm -w exec biome format --write web || true

lint:
	cd agent && ruff check .
	cd backend && ruff check .
	pnpm -w exec biome check web || true

# Build all .debs (Linux only). Override version: make package MD_VERSION=1.0.1
# Use package-agent / package-backend / package-web for individual targets.
package:
	bash packaging/build-deb.sh all

package-agent:
	bash packaging/build-deb.sh agent

package-backend:
	bash packaging/build-deb.sh backend

package-web:
	bash packaging/build-deb.sh web
