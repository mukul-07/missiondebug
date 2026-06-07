# Contributing to MissionDebug

Thanks for the interest. This document covers how to set up a dev
environment, run the tests, and submit changes.

## Project shape

Three components, three languages, one repo:

- `agent/` — Python (rclpy) ring buffer + detectors
- `backend/` — Python (FastAPI) session index + retention
- `web/` — TypeScript (React + Vite) MCAP replay UI

New features should fit the existing architecture (agent / backend / web)
or be explicitly scoped to a future version — open an issue to discuss
scope before a large change.

## Local setup

You need:

- Ubuntu 22.04 or 24.04
- ROS 2 Humble or Jazzy (for `make dev` / running on a real robot;
  **not** required for running the test suite)
- Python 3.10+
- Node 20+, pnpm 9+
- `tmux` (used by `scripts/dev.sh`)
- `uv` ([install](https://docs.astral.sh/uv/)) — optional but
  preferred over pip

```bash
make install           # set up agent + backend venvs and web deps
make test              # run pytest in both Python projects
```

The test suite does **not** require ROS 2 to be installed. The agent's
pure-logic code is decoupled from rclpy by design.

## Running locally

```bash
source /opt/ros/humble/setup.bash    # or jazzy
MD_FIXTURES=1 make dev               # tmux session with all three services
```

The fixture mode seeds the backend with `fixtures/sample_drive.mcap` so
the UI has something to render even without a connected robot.

## Submitting changes

- Branch from `main`. Keep PRs focused — one feature or one fix per PR.
- Run `make test` before submitting. The CI workflow runs the same
  matrix (Python 3.10 / 3.11 / 3.12) and must be green before merge.
- Match the existing style. `make fmt` runs ruff (Python) and Biome
  (TypeScript) where applicable.
- Reference the relevant phase or spec section in the commit body
  (e.g. "v1.5 Phase 3: ..." or "fix: ..."). One-line subject, optional
  paragraph body explaining the *why*.
- Avoid scope creep within a PR. If you find unrelated work, file an
  issue or open a separate PR.

## Reporting bugs

[Open an issue](https://github.com/mukul-07/missiondebug/issues) with:

- ROS 2 distro + Ubuntu version
- MissionDebug version (commit SHA or `.deb` version)
- Minimal repro steps
- Relevant log output (`journalctl -u missiondebug-agent -n 100`)

## License

By contributing you agree your contributions will be licensed under the
project's [MIT License](./LICENSE).
