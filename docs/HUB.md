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
hub address. The robot **POSTs** its capture metadata to the hub, so:

- The robot must be able to reach the hub URL. On the **same LAN** this is
  automatic. Across **different sites**, put the hub somewhere the robots can
  reach (a public IP or a VPN); a private `192.168.x.x` address is only
  reachable from that LAN.
- Set `hub.url` in the agent config to your hub address:

  ```yaml
  hub:
    url: "http://<that-machine>:8000"
  ```

  For a standalone `.deb` install this is `/etc/missiondebug/config.yaml`;
  restart the agent after editing (`sudo systemctl restart missiondebug-agent`).

The agent then posts each capture's metadata to the hub as it happens, and the
hub's dashboard shows your fleet's incidents in one place. Open any capture to
scrub its timeline, including the camera frames captured around the incident:

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
