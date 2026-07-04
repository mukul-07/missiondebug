# Rolling MissionDebug out with Ansible

Installs the agent on every robot in an inventory, writes a per-robot
config (unique `robot_id`, your hub URL, the topics you choose), and
keeps the service running. Re-runnable; the agent only restarts when its
config actually changed.

```bash
cp inventory.example.ini inventory.ini   # edit: your robots, your hub
ansible-playbook -i inventory.ini playbook.yml
```

Within a minute every robot appears on the hub's **Agents** page
(`http://<hub>:8000/fleet/agents`) — expand a row to browse the live
topics it can capture.

Notes:

- `robot_id` is the inventory hostname — keep hostnames unique or two
  robots will collide into one entry on the hub.
- `md_topics` is deliberately per-group: robots in the same class share
  a capture config. Override it in `[group:vars]` for drones/arms — see
  `examples/*-config.yaml` for per-archetype starting points.
- The template binds the agent to `0.0.0.0` and sets `agent_url` from
  the robot's primary address, so hub-side replay and topic discovery
  work out of the box.
- Single machines or one-off installs don't need Ansible — use the
  one-line installer instead (`scripts/install.sh`).
