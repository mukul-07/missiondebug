# syntax=docker/dockerfile:1.7
#
# MissionDebug demo image: FastAPI backend + bundled web UI in one container.
#
# Build context is the repo root.
#   docker build -t missiondebug:latest .
#
# Published to ghcr.io/mukul-07/missiondebug from CI on push to main.
# Pulled by missiondebug-demos for a zero-build quickstart.

# ---------- Stage 1: build the web bundle ----------
FROM node:20-slim AS web-builder

RUN corepack enable && corepack prepare pnpm@9 --activate

WORKDIR /repo

# Install workspace deps first against just the manifests so this layer
# caches across unrelated source changes.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY web/package.json ./web/package.json
RUN pnpm install --frozen-lockfile

# Then bring the rest of the web source and build.
COPY web ./web
RUN pnpm -C web build

# ---------- Stage 2: runtime ----------
FROM python:3.11-slim AS runtime

WORKDIR /app

# Install backend from local source. Copy manifests first for layer caching.
# Include the [otel] + [license] extras so the image can export to an
# OpenTelemetry collector (MD_OTEL_ENDPOINT) and verify paid-tier license keys
# (MD_LICENSE_KEY) when those are set — both still opt-in at runtime, just
# batteries-included in the published image. No effect when unset.
COPY backend/pyproject.toml /app/backend/
COPY backend/src /app/backend/src
RUN pip install --no-cache-dir "/app/backend[otel,license]"

# Bring in the built web bundle from stage 1.
COPY --from=web-builder /repo/web/dist /web

RUN mkdir -p /sessions /fixtures
VOLUME ["/fixtures", "/sessions"]

ENV MD_FIXTURES=1 \
    MD_FIXTURES_DIR=/fixtures \
    MD_SESSIONS_DIR=/sessions \
    MD_WEB_DIR=/web \
    MD_DB=/sessions/missiondebug.sqlite3

EXPOSE 8000

CMD ["sh", "-c", "missiondebug-backend --host 0.0.0.0 --port 8000 --sessions-dir $MD_SESSIONS_DIR --fixtures-dir $MD_FIXTURES_DIR --web-dir $MD_WEB_DIR --db $MD_DB"]
