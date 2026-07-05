# Setting up a MissionDebug hub

The **hub** is an optional, self-hosted MissionDebug dashboard for browsing
incidents across a fleet: replay, structured summaries, similarity search ("has
this happened before?"), and plain-English queries over your incident history.

![The hub's fleet incident dashboard: captures, resolution rate, MTTR, recurrence rate, captures per day, top recurring patterns, and captures by robot](screenshot-incidents.png)

You do **not** need a hub to use MissionDebug. A **single robot** works fully
without one: captures are saved on the robot as standard `.mcap` files; copy one
off and open it in [Foxglove](https://app.foxglove.dev) to replay. Set up a hub
when you want a fleet-wide incident dashboard in one place.

(If you run MissionDebug through the
[Transitive Robotics capability](https://github.com/mukul-07/missiondebug-transitive),
the portal's fleet view already shows every robot's status and most recent
capture with no hub and no setup; the hub is still optional there, for the
deeper incident dashboard.)

There is **no hosted hub and no sign-up**. The hub is software you install on a
machine you control.

## 1. Pick a machine to be the hub

Any always-on Linux machine your robots can reach over the network: a server, a
fleet PC, a NUC, or a cloud VM. It does not have to be a robot.

## 2. Install the hub packages

The hub is two packages: `missiondebug-backend` (indexes captures, serves the
API and dashboard) and `missiondebug-web` (the UI). On that machine:

```bash
# Add the MissionDebug apt repository (one time)
sudo install -d /etc/apt/keyrings
curl -fsSL https://mukul-07.github.io/missiondebug/missiondebug-archive-key.asc \
  | sudo gpg --dearmor -o /etc/apt/keyrings/missiondebug.gpg
echo "deb [signed-by=/etc/apt/keyrings/missiondebug.gpg] https://mukul-07.github.io/missiondebug $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/missiondebug.list
sudo apt update

# Install the hub
sudo apt install missiondebug-backend missiondebug-web
```

The backend starts automatically and serves the dashboard on **port 8000**.

## 3. Your hub URL

Your hub's web address is:

```
http://<that-machine>:8000
```

Use the machine's IP or hostname, for example:

- `http://192.168.1.50:8000`
- `http://fleet-server.local:8000`
- `http://hub.yourcompany.com:8000` (if you put it behind DNS)

Open that address in a browser to confirm the dashboard loads. **That is the
"MissionDebug hub web address"** you paste into the Hub URL field on a robot's
status card, which turns each capture into a clickable link to its session in
the hub.

## 4. Send captures to the hub

For captures to actually appear in the hub, the robot's agent has to know the
hub address — and the hub has to be able to reach the agent back (that's how
replay streams the recording off the robot, and how live topic discovery
works). Three config keys on each robot, all in
`/etc/missiondebug/config.yaml`:

```yaml
http_host: "0.0.0.0"    # bind beyond loopback, or the hub can't reach back

hub:
  url: "http://<hub-machine>:8000"          # where this robot reports
  agent_url: "http://<this-robot-ip>:7000"  # how the hub calls back
```

Restart the agent after editing: `sudo systemctl restart missiondebug-agent`.
(The one-line installer's `--hub-url` flag and `missiondebug-agent init`
both write all three for you.)

Network notes:

- The robot must reach the hub URL, and the hub must reach the robot's
  port `7000` (open it in the robot's firewall if you run one). On the
  **same LAN** this is automatic. Across **different sites**, put the hub
  somewhere the robots can reach (a public IP or a VPN); a private
  `192.168.x.x` address is only reachable from that LAN.
- Without `agent_url` (or with the default loopback bind), capture
  *metadata* still arrives — the dashboard, summaries and similarity all
  work — but opening a recording shows "recording unavailable" and the
  topics panel can't scan the robot.

## 5. Verify it worked

Within ~60 seconds of the restart, the robot appears on the hub's
**Agents** page (`http://<hub>:8000/fleet/agents`) with a green heartbeat.
Expand its **▸ topics** row: you should see the robot's live ROS graph,
with warnings on anything that can't capture (type not built, no
publishers). If a configured topic later breaks, a **⚠ badge** appears on
the robot's row automatically. Trigger a test capture
(`curl -X POST http://<robot>:7000/sessions/save`) and it lands at the top
of the Sessions page.

## 6. Protect the hub (fleet mode)

A hub that aggregates a fleet should require a password. Set two
environment variables where the backend runs (for a `.deb` install:
`/etc/missiondebug/backend.env`, then
`sudo systemctl restart missiondebug-backend`):

```bash
MD_MODE=fleet
MD_HUB_AUTH_PASSWORD=<pick-a-strong-one>
```

In fleet mode the backend **refuses to start without a password** (Hard
Rule: auth defaults on for fleets). Agents authenticate with the same
secret via `hub.auth_token` in their config; browsers get a login prompt.
Single-robot installs can stay open (`MD_MODE=single`, the default).

From then on the agent posts each capture's metadata to the hub as it
happens, and the hub's dashboard shows your fleet's incidents in one
place. Open any capture to scrub its timeline, including the camera
frames captured around the incident:

![A captured session in the hub: synchronized camera feeds, pose track, timeline scrubber, and a message inspector at the playhead](screenshot-detail.png)

## Which view should I use?

| | On-robot `.mcap` + Foxglove | MissionDebug hub |
|---|---|---|
| Setup | None | Install backend + web on a reachable machine |
| Network | None (the file is on the robot) | Robots must be able to reach the hub |
| Shows | One capture, replayed | Full incident dashboard across a fleet: replay, summaries, similarity, AI |
| Best for | Reviewing a single capture | Deep, cross-fleet incident review |

For a single robot, copying a capture off and opening it in Foxglove is the
whole story. Stand up the hub when you want the fleet-wide incident dashboard.

If you run MissionDebug through the
[Transitive Robotics capability](https://github.com/mukul-07/missiondebug-transitive),
there is also a third view: the portal's fleet view shows every robot's status
and most recent capture with no setup, which covers "is my fleet OK right now?"
without a hub.
