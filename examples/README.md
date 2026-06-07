# Example agent configs

Drop one of these into `/etc/missiondebug/config.yaml`, edit the topic
names + types to match your robot, then `sudo systemctl restart
missiondebug-agent`.

| File | When to use |
|---|---|
| [ground-vehicle-config.yaml](./ground-vehicle-config.yaml) | AMRs, delivery bots, indoor service robots, mapping rovers — anything that drives. |
| [drone-config.yaml](./drone-config.yaml) | Quadcopter / fixed-wing UAV running PX4 or ArduPilot via mavros. |
| [manipulator-config.yaml](./manipulator-config.yaml) | Industrial or collaborative arm running MoveIt2 + ros2_control. |
| [forklift-config.yaml](./forklift-config.yaml) | Real-fleet reference. ~30 topics across locomotion / pose / planning / behavior / hardware, with tier-1 / tier-2 / tier-3 commentary. Useful as a "how would I scope this on my own robot" reference. |
| [rule-patterns.yaml](./rule-patterns.yaml) | Cookbook of `anomaly.rules:` recipes. Numeric thresholds, string-equals, boolean flags, actionlib aborts. Copy individual entries into your config. |

## What renders in the replay UI

Capture + detection are topic-agnostic — the agent records and watches
*any* topic. The web replay then renders by message type:

| Data | Renders as |
|---|---|
| `sensor_msgs/CompressedImage` | video track |
| `tf2_msgs/TFMessage`, `nav_msgs/Path` | pose / path track |
| `geometry_msgs/Twist` (`/cmd_vel`-shaped) | velocity charts |
| **`sensor_msgs/JointState`** | **per-joint chart** — one line per joint (position / velocity / effort), with a legend (arm motion for manipulators) |
| any other numeric leaf | auto scalar chart, one per topic |
| every topic | JSON inspector synced to the playhead |

So **ground vehicles, drones, and manipulators all render**: drones get
cmd_vel + scalar charts (IMU, battery, attitude as numbers); manipulators
get the per-joint chart for `JointState` plus scalars for the gripper and
controller feedback.

## Picking topics

The 60-second buffer lives in RAM. Each topic you subscribe to costs
bytes. The general strategy:

1. **Always include**: `/cmd_vel`, `/tf`, `/plan` (or your equivalents),
   plus battery + diagnostics. These are tiny and high-signal.
2. **Sub-sample large streams**: lidar scans, camera frames. Set
   `rate_divisor: 5` (or 6) on those topics so you keep every Nth
   message. The example configs already do this.
3. **Set `max_total_bytes`**: a global cap on the buffer. The agent
   evicts oldest messages from the largest topic when the cap is hit.
4. **Skip what your bag-manager already covers**: full-rate point
   clouds, costmaps, joint_states at 1 kHz, log spam. MissionDebug is
   the focused 60-second debug layer; deep archive is a separate concern.

## Rule schema (`anomaly.rules:`)

```yaml
- name: <human-readable, used as auto-save label>
  topic: <ROS 2 topic>
  field: <dot-path into the message, e.g. "data" or "status.status">
  # Exactly one comparison:
  equals: <value>                      # any: bool, int, str
  not_equals: <value>
  lt: <number>
  gt: <number>
  lte: <number>
  gte: <number>
  duration_seconds: <float>            # how long condition must hold (0 = instant)
  cooldown_seconds: <float>            # min gap between fires (default 30)
```

The `field` dot-path is fed to `getattr` recursively, so it matches what
you'd write in Python: `msg.linear.x`, `msg.status.status`, `msg.pose.position.z`.
Array indexing (`positions[0]`) is **not** currently supported — expose
a scalar topic upstream if you need that.

## Built-in detector blocks

Three built-ins live alongside `rules:` for ergonomic reasons:

```yaml
anomaly:
  stall:                              # zero cmd_vel sustained
    velocity_threshold: 0.01
    duration_seconds: 5.0

  path_deviation:                     # drift from /plan
    threshold_meters: 0.5
    plan_topic: /plan
    pose_topic: /tf
    pose_child_frame: base_link

  battery_low:                        # convenience for sensor_msgs/BatteryState
    topic: /battery
    threshold: 0.20

  topic_dropout:                      # publisher dies silently
    - topic: /scan
      silence_seconds: 3.0
```

`stall` and `path_deviation` are ground-vehicle specific — leave them
out for drones and arms. `battery_low` and `topic_dropout` are universal.

## Verifying your config

After editing:

```bash
sudo systemctl restart missiondebug-agent
journalctl -u missiondebug-agent -n 30 --no-pager
```

Look for:
- `Subscribed to <topic> [<type>]` — one per topic in your config
- `Loaded N config-driven rule(s)` — confirms `rules:` parsed
- `Watching N topic(s) for dropout` — confirms `topic_dropout:` parsed

To trigger a rule manually for verification, publish a synthetic
message that matches the condition:

```bash
ros2 topic pub --once /e_stop std_msgs/Bool '{data: true}'
```

Then `ls /var/lib/missiondebug/sessions/` should show a fresh MCAP.
