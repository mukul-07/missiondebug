# MissionDebug Quality Declaration

This document declares the quality level claimed by `missiondebug` against
the criteria in **REP 2004** ([Package Quality Categories][rep-2004]).

[rep-2004]: https://www.ros.org/reps/rep-2004.html

**Claimed level:** **4 (Demonstration)**.

This is an honest claim, not a marketing claim. v1.5 ships and works. It
has not yet been validated by a production deployment running unattended
for a 14-day acceptance window. Until that happens, we will not claim a
higher level.

Re-evaluation will follow the v1.5 field acceptance. If a partner runs
the system unattended for 14 days without data loss or crashes, we will
re-assess against Level 3 criteria.

## How each REP-2004 criterion is met

### 1. Version Policy

- Uses **semantic versioning** (`v0`, `v1`, `v1.5`).
- Each version has a defined scope — what is in scope and what is out of
  scope — described in the README and the release notes.
- Breaking changes between versions are documented in the release notes.

### 2. Change Control Process

- All source under public version control: <https://github.com/mukul-07/missiondebug>
- Changes flow through commits to `main`. PR workflow is not enforced
  for the solo-maintainer phase but is supported.
- Tests on `main` are gated by a [GitHub Actions workflow](.github/workflows/test.yml)
  running pytest on Python 3.10, 3.11, and 3.12.
- Container images are published from `main` and `v*` tags via
  [a separate workflow](.github/workflows/publish-image.yml).

### 3. Documentation

- **Feature list / scope:** the [README](./README.md), the canonical
  description of what MissionDebug does and how to run it.
- **Public API:** the agent's REST control plane (`POST /api/save`)
  and the backend's HTTP API (`GET /api/sessions`, `POST
  /api/admin/sweep`, etc.) are documented in the README and exposed
  via FastAPI's auto-generated OpenAPI spec at `/openapi.json`.
- **Architecture:** the "How it's built" section of the README
  summarises the three-process design (agent / backend / web) with
  links to component sources.
- **License:** [MIT](./LICENSE).

### 4. Testing

- **Unit and integration tests:** ~87 pytest tests across `agent/tests/`
  and `backend/tests/`. Coverage is uploaded to Codecov on every push
  to `main`.
- **Test architecture:** the agent's pure-logic code (ring buffer, rate
  limiter, MCAP writer, detectors, rule engine) is intentionally
  decoupled from rclpy so it can be tested without a ROS install.
  rclpy-coupled code lives in `agent/src/missiondebug_agent/main.py`
  and `ros_bridge.py` and is exercised by end-to-end demos.
- **Fixtures:** `fixtures/sample_drive.mcap` is a recorded 30-second
  synthetic drive that exercises rule firing in the backend's index
  + replay path. `MD_FIXTURES=1 make dev` seeds the backend with it.

### 5. Dependencies

All runtime dependencies are pinned via `uv.lock` files:
- [`agent/uv.lock`](./agent/uv.lock)
- [`backend/uv.lock`](./backend/uv.lock)
- [`pnpm-lock.yaml`](./pnpm-lock.yaml) (web)

Direct dependencies:

| Component | Major deps |
|---|---|
| `agent` | `fastapi`, `uvicorn`, `pydantic`, `pyyaml`, `mcap`, `mcap-ros2-support`, `rclpy` (system, from ROS distro) |
| `backend` | `fastapi`, `uvicorn`, `mcap` |
| `web` | `react`, `vite`, `@foxglove/rosmsg2-serialization`, `@mcap/core`, `pixi.js` |

`rclpy` is supplied by the user's ROS 2 install at runtime; it is not
bundled and is not required to run tests or the backend.

### 6. Platform Support

- **Supported ROS 2 distros:** Humble, Jazzy. Tested on Ubuntu 22.04
  and 24.04. ROS 2 Rolling is unsupported in v1.5; planned for v1.6.
- **Supported architectures:** `linux/amd64`, `linux/arm64` (the
  published container image is multi-arch; the `.deb`s build for
  whichever arch you `make package` on).
- **Python:** `>=3.10`, CI matrix covers 3.10 / 3.11 / 3.12.
- **Browser:** the web UI targets modern evergreen browsers (Chromium,
  Firefox, Safari) released within the last 18 months.

### 7. Security

Network exposure is minimal:

- Agent listens on `127.0.0.1:7000` (loopback only).
- Backend listens on `0.0.0.0:8000` by default for local-network access
  from the engineer's laptop. In single-robot mode there is no
  authentication — it is **not cloud-hosted and not authenticated**;
  it assumes network-level trust. Running the single-robot UI on the
  public internet is out of scope (fleet mode adds an auth gate).
- Vulnerability reporting policy is documented in [`SECURITY.md`](./SECURITY.md).
