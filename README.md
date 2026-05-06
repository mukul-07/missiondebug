# MissionDebug

Local-first debugger for ROS 2 robots. Record → detect → replay, on one laptop.

- **v0** ([SPEC.md](./SPEC.md)) — single robot, manual save + stall detector, replay UI.
- **v1** ([v1-SPEC.md](./v1-SPEC.md)) — design-partner ready: path-deviation detector, per-robot identity, annotations, shareable URLs, `.deb` package.

---

## Quick start (developer mode)

Run all three services from source in a single tmux window:

### Prerequisites
- Ubuntu 22.04 with **ROS 2 Humble** sourced (or 24.04 + Jazzy)
- Python 3.10+
- Node 20+, pnpm 9+
- `tmux`

### Steps
```bash
# Clone
git clone https://github.com/<you>/missiondebug.git
cd missiondebug

# Install deps for agent + backend + web
make install

# Source ROS 2 (humble OR jazzy — whichever you have)
source /opt/ros/humble/setup.bash

# Bring up agent (:7000), backend (:8000), web (:5173) in one tmux session
make dev

# Open the UI
xdg-open http://localhost:5173
```

In normal use the robot's own ROS nodes publish your topics. To test without a robot, use `ros2 topic pub` from another terminal.

---

## Production install (agent only)

For a customer running MissionDebug on a real robot, build the `.deb` and install it:

```bash
# On the build machine (Ubuntu — same arch as the target):
sudo apt install fakeroot dpkg-dev python3-pip python3-venv
make package
# Produces dist/missiondebug-agent_1.0.0_<arch>.deb

# On the target robot:
sudo dpkg -i missiondebug-agent_1.0.0_<arch>.deb
sudo nano /etc/missiondebug/config.yaml      # set robot_id + topics
sudo systemctl restart missiondebug-agent
```

What the package does:
- Installs the agent + Python venv to `/opt/missiondebug/`
- Creates a `missiondebug` system user
- Writes default config to `/etc/missiondebug/config.yaml`
- Sessions stored at `/var/lib/missiondebug/sessions/`
- Registers and starts `missiondebug-agent.service` (auto-starts at boot)
- Control API listens on `127.0.0.1:7000`

Inspect:
```bash
sudo systemctl status missiondebug-agent
sudo journalctl -u missiondebug-agent -f
ls /var/lib/missiondebug/sessions/
curl http://localhost:7000/healthz
```

Uninstall:
```bash
sudo dpkg -r missiondebug-agent       # remove package, keep config + sessions
sudo dpkg -P missiondebug-agent       # purge: also drops config dir + user
                                       # (sessions in /var/lib/missiondebug stay
                                       # — delete manually if you want them gone)
```

**Note for v1:** the `.deb` packages the **agent only**. Backend + web still run from source via `make dev`. v1.5 will package those too. For a design partner, run `make dev` from the source tree on a workstation pointed at the agent's sessions dir (or a shared mount).

---

## Tests
```bash
make test
```

## Layout
- `agent/` — Python ROS 2 agent (ring buffer, MCAP writer, detectors, control API on :7000)
- `backend/` — FastAPI session index + MCAP file server on :8000
- `web/` — Vite + React + PixiJS timeline scrubber on :5173
- `packaging/` — Debian `.deb` build files (control, postinst, systemd unit, wrapper)
