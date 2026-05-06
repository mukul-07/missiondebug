# MissionDebug

> Local-first debugger for ROS 2 robots. Record, detect, replay.

When a robot misbehaves, you want to know what it was seeing 60 seconds before. MissionDebug runs alongside your ROS 2 stack, keeps a rolling 60-second buffer in RAM, and snapshots it to disk when something goes wrong — manually, or automatically when a detector fires (stall, path deviation). Open the web UI, click a session, scrub the timeline.

No cloud. No login. Single robot. Localhost.

---

## Try it locally

You need Ubuntu 22.04 (or 24.04), ROS 2 Humble (or Jazzy), Python 3.10+, Node 20+, pnpm 9+, and `tmux`.

```bash
git clone https://github.com/mukul-07/missiondebug.git
cd missiondebug
make install

source /opt/ros/humble/setup.bash
MD_FIXTURES=1 make dev
```

Open <http://localhost:5173>. The session list will already contain a `sample_drive` fixture — click it, scrub the timeline.

The fixture is 30 seconds long with a deliberate stall (8–14s) and a 0.8m path deviation (14–22s). Watch the velocity chart drop, the orange dot freeze, then drift off the green line.

---

## Install on a real robot

Build the agent `.deb` (Linux only):

```bash
sudo apt install fakeroot dpkg-dev python3-pip python3-venv
make package
```

Install on the target robot:

```bash
sudo dpkg -i missiondebug-agent_1.0.0_<arch>.deb
sudo nano /etc/missiondebug/config.yaml      # set robot_id + topics
sudo systemctl restart missiondebug-agent
```

The agent runs as a system service, starts at boot, exposes its API on `127.0.0.1:7000`, writes MCAP files to `/var/lib/missiondebug/sessions/`. Backend + web still run from source via `make dev` for v1; v1.5 packages those too.

```bash
sudo systemctl status missiondebug-agent     # running?
sudo journalctl -u missiondebug-agent -f     # tail logs
```

---

## How it's built

- **Agent** (Python, `agent/`) — rclpy subscribers → 60s ring buffer in RAM → MCAP writer → control HTTP API on `:7000`. Detectors (stall, path-deviation) fire on the same events; both produce labeled sessions.
- **Backend** (FastAPI + SQLite, `backend/`) — auto-rescans the sessions directory every 5s, indexes MCAP metadata, serves files with HTTP range support so the browser can stream the timeline.
- **Web** (React + Vite + PixiJS, `web/`) — Web Worker decodes the MCAP using `@foxglove/rosmsg2-serialization`, renders synchronized video / chart / pose tracks. Annotations stored server-side; URLs are deep-linkable with `?t=23.4`.

Specs:
- [SPEC.md](./SPEC.md) — v0 (record + replay loop, single robot, localhost)
- [v1-SPEC.md](./v1-SPEC.md) — v1 (path-deviation, annotations, share links, `.deb`, fixture)

## Tests

```bash
make test                    # 34 tests, ~0.5s
```

## License

MIT — see [LICENSE](./LICENSE).
