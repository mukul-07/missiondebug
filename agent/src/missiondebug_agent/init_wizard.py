"""`missiondebug-agent init` — interactive first-run setup on the robot.

Scans the live ROS graph (same discovery + classification the hub's topics
panel uses), proposes a capture list with the recommended topics pre-selected,
asks the few questions that matter (robot id, hub URL), and writes the
config file itself. The agent configuring itself on its own robot keeps
Hard Rule 22 intact — the hub never writes config; the operator, already
SSH'd in from the install, does.

Non-interactive mode (`--yes`) accepts every recommendation, for scripted
rollouts:  missiondebug-agent init --yes --hub-url http://hub:8000

The .deb wrapper sources ROS (and auto-sources built workspaces) before this
runs, so discovery sees the graph and custom message types resolve exactly
as they will for the running agent.
"""

from __future__ import annotations

import argparse
import datetime
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

# Minimal per-archetype presets — trimmed versions of examples/*.yaml (the
# full, commented versions live in the repo's examples/ directory). Structured
# rather than literal YAML so the rendered anomaly block includes ONLY rules
# whose topics were actually selected: a rule on an unsubscribed topic never
# fires and never errors, which is exactly the silent-dead-detector failure
# this wizard exists to prevent.
ARCHETYPES: dict[str, dict] = {
    "ground-vehicle": {
        "topics": [
            ("/tf", "tf2_msgs/msg/TFMessage"),
            ("/cmd_vel", "geometry_msgs/msg/Twist"),
            ("/odom", "nav_msgs/msg/Odometry"),
            ("/scan", "sensor_msgs/msg/LaserScan"),
            ("/battery", "sensor_msgs/msg/BatteryState"),
        ],
        # stall needs both the command and the motion topic to be captured
        "stall": {"needs": ["/cmd_vel", "/odom"],
                  "yaml": "  stall:\n    velocity_threshold: 0.01\n    duration_seconds: 5.0\n    cooldown_seconds: 30.0"},
        "battery_low": {"topic": "/battery",
                        "yaml": '  battery_low:\n    topic: "/battery"\n    threshold: 0.2\n    duration_seconds: 5.0\n    cooldown_seconds: 600.0'},
        "dropout": [
            {"topic": "/scan", "yaml": '    - { topic: "/scan", silence_seconds: 3.0, cooldown_seconds: 60.0 }'},
        ],
        "rules": [],
    },
    "drone": {
        "topics": [
            ("/mavros/state", "mavros_msgs/msg/State"),
            ("/mavros/local_position/pose", "geometry_msgs/msg/PoseStamped"),
            ("/mavros/imu/data", "sensor_msgs/msg/Imu"),
            ("/mavros/battery", "sensor_msgs/msg/BatteryState"),
        ],
        "stall": None,
        "battery_low": {"topic": "/mavros/battery",
                        "yaml": '  battery_low:\n    topic: "/mavros/battery"\n    threshold: 0.25\n    duration_seconds: 2.0\n    cooldown_seconds: 300.0'},
        "dropout": [
            {"topic": "/mavros/state", "yaml": '    - { topic: "/mavros/state", silence_seconds: 2.0, cooldown_seconds: 60.0, name: "mavlink-heartbeat-lost" }'},
            {"topic": "/mavros/imu/data", "yaml": '    - { topic: "/mavros/imu/data", silence_seconds: 1.0, cooldown_seconds: 60.0 }'},
        ],
        "rules": [],
    },
    "manipulator": {
        "topics": [
            ("/joint_states", "sensor_msgs/msg/JointState"),
            ("/tf", "tf2_msgs/msg/TFMessage"),
            ("/wrench", "geometry_msgs/msg/WrenchStamped"),
        ],
        "stall": None,
        "battery_low": None,
        "dropout": [
            {"topic": "/joint_states", "yaml": '    - { topic: "/joint_states", silence_seconds: 1.0, cooldown_seconds: 60.0 }'},
        ],
        "rules": [
            {"topic": "/wrench",
             "yaml": "    - name: force-spike\n      topic: /wrench\n      field: wrench.force.z\n      gt: 50\n      duration_seconds: 0.2\n      cooldown_seconds: 10"},
        ],
    },
}


def build_anomaly_block(archetype: str, chosen_names: set[str]) -> str:
    """Render the anomaly: block with only the preset rules whose topics were
    actually selected. Empty result = no anomaly key (config default)."""
    preset = ARCHETYPES[archetype]
    parts: list[str] = []
    stall = preset.get("stall")
    if stall and all(n in chosen_names for n in stall["needs"]):
        parts.append(stall["yaml"])
    batt = preset.get("battery_low")
    if batt and batt["topic"] in chosen_names:
        parts.append(batt["yaml"])
    dropouts = [d["yaml"] for d in preset.get("dropout", []) if d["topic"] in chosen_names]
    if dropouts:
        parts.append("  topic_dropout:\n" + "\n".join(dropouts))
    rules = [r["yaml"] for r in preset.get("rules", []) if r["topic"] in chosen_names]
    if rules:
        parts.append("  rules:\n" + "\n".join(rules))
    if not parts:
        return ""
    return "# Anomaly detectors (from the {} preset; see examples/ for more).\nanomaly:\n{}".format(
        archetype, "\n".join(parts)
    )


# ---- pure helpers (unit-tested without ROS) --------------------------------


def propose_selection(topics: list[dict]) -> set[str]:
    """Which discovered topics start selected: recommended by kind AND with
    a resolvable message type (an unbuilt type would capture nothing)."""
    return {
        t["name"]
        for t in topics
        if t.get("recommended") and t.get("resolvable", False)
    }


def parse_toggle(reply: str, count: int) -> list[int] | str | None:
    """Parse the toggle prompt: '' -> accept (None), 'a'/'n' -> keywords,
    '1,3 5' -> zero-based indices. Out-of-range entries are dropped."""
    reply = reply.strip().lower()
    if reply == "":
        return None
    if reply in ("a", "all", "n", "none"):
        return reply[0]
    out = []
    for tok in reply.replace(",", " ").split():
        if tok.isdigit() and 1 <= int(tok) <= count:
            out.append(int(tok) - 1)
    return out


def badge(t: dict) -> str:
    """The same warnings the hub panel shows, as terminal text."""
    parts = []
    if not t.get("resolvable", True):
        parts.append("TYPE NOT BUILT — would capture nothing")
    if t.get("publishers") == 0:
        parts.append("no publishers")
    if t.get("large"):
        parts.append("large")
    return f"  [{'; '.join(parts)}]" if parts else ""


def render_config(
    *,
    robot_id: str,
    topics: list[tuple[str, str]],
    output_dir: str,
    hub_url: str | None,
    agent_url: str | None,
    subsystem: str | None,
    anomaly_block: str,
) -> str:
    lines = [
        "# MissionDebug agent configuration — generated by `missiondebug-agent init`.",
        "# Edit freely; restart the agent afterwards:",
        "#   sudo systemctl restart missiondebug-agent",
        "",
        f'robot_id: "{robot_id}"',
        "buffer_seconds: 60",
        "",
        "topics:",
    ]
    for name, type_ in topics:
        lines.append(f'  - {{ name: "{name}", type: "{type_}" }}')
    lines += [
        "",
        f'output_dir: "{output_dir}"',
        "",
    ]
    if hub_url:
        lines += [
            "# Bind beyond loopback so the hub can fetch recordings and scan topics.",
            'http_host: "0.0.0.0"',
            "http_port: 7000",
            "",
            "hub:",
            f'  url: "{hub_url}"',
        ]
        if agent_url:
            lines.append(f'  agent_url: "{agent_url}"')
        else:
            lines.append('  # agent_url: "http://<this-robot-ip>:7000"  # set me: hub->robot callback')
        if subsystem:
            lines.append(f'  subsystem: "{subsystem}"')
        lines.append("")
    else:
        lines += [
            'http_host: "127.0.0.1"',
            "http_port: 7000",
            "",
        ]
    if anomaly_block:
        lines.append(anomaly_block)
    lines.append("")
    return "\n".join(lines)


def primary_ip() -> str | None:
    """This machine's outbound-facing address (no traffic is sent)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None


# ---- the wizard -------------------------------------------------------------


def _scan(tries: int = 4) -> tuple[list[dict], bool]:
    """Run discovery, retrying briefly: the first scan of a fresh node always
    reports unsettled while DDS discovery trickles in."""
    from .discovery import discover_topics

    result: dict = {"topics": [], "settled": False}
    for i in range(tries):
        result = discover_topics()
        if result.get("settled") and result.get("topics"):
            break
        if i < tries - 1:
            time.sleep(1.5)
    return list(result.get("topics") or []), bool(result.get("settled"))


def main(argv: list[str] | None = None, *, input_fn=input, print_fn=print) -> int:
    parser = argparse.ArgumentParser(
        prog="missiondebug-agent init",
        description="Interactive setup: scan the ROS graph, pick topics, write the config.",
    )
    parser.add_argument("--config", default="/etc/missiondebug/config.yaml",
                        help="config file to write (default: /etc/missiondebug/config.yaml)")
    parser.add_argument("--hub-url", default=None,
                        help="MissionDebug hub this robot reports to (e.g. http://192.168.1.50:8000)")
    parser.add_argument("--subsystem", default=None, help="optional free-form tag (e.g. navigation)")
    parser.add_argument("--archetype", choices=sorted(ARCHETYPES), default=None,
                        help="anomaly-rule preset; also the topic fallback when no ROS graph is visible")
    parser.add_argument("--yes", action="store_true",
                        help="non-interactive: accept every recommendation")
    args = parser.parse_args(argv)

    ask = (lambda *_a, **_k: "") if args.yes else input_fn

    print_fn("MissionDebug agent setup")
    print_fn("========================")

    # 1. robot id ------------------------------------------------------------
    default_id = socket.gethostname() or "robot-001"
    reply = ask(f"Robot id (unique per robot) [{default_id}]: ").strip()
    robot_id = reply or default_id

    # 2. scan the graph --------------------------------------------------------
    print_fn("\nScanning the ROS graph…")
    topics, settled = _scan()
    archetype = args.archetype
    if not topics:
        print_fn("No ROS topics visible (is ROS running?).")
        if archetype is None and not args.yes:
            reply = ask(f"Fall back to an archetype preset {sorted(ARCHETYPES)} [ground-vehicle]: ").strip()
            archetype = reply or "ground-vehicle"
        archetype = archetype or "ground-vehicle"
        chosen = list(ARCHETYPES[archetype]["topics"])
        print_fn(f"Using the {archetype} starting set ({len(chosen)} topics).")
    else:
        if not settled:
            print_fn("(scan did not fully settle — the list may be missing topics; rerun to refresh)")
        selected = propose_selection(topics)
        while True:
            print_fn(f"\nFound {len(topics)} topics — capture the checked ones:")
            for i, t in enumerate(topics, 1):
                mark = "x" if t["name"] in selected else " "
                print_fn(f"  [{mark}] {i:2d}. {t['name']}  ({t.get('type') or 'type unknown'}){badge(t)}")
            if args.yes:
                break
            reply = ask("Toggle by number (e.g. 1,3), a=all, n=none, Enter=accept: ")
            parsed = parse_toggle(reply, len(topics))
            if parsed is None:
                break
            if parsed == "a":
                selected = {t["name"] for t in topics}
            elif parsed == "n":
                selected = set()
            else:
                for idx in parsed:
                    name = topics[idx]["name"]
                    if name in selected:
                        selected.discard(name)
                    else:
                        selected.add(name)
        chosen = [(t["name"], t.get("type") or "") for t in topics if t["name"] in selected]
        unresolvable = [t["name"] for t in topics if t["name"] in selected and not t.get("resolvable", True)]
        if unresolvable:
            print_fn(
                "\nwarning: selected topics with unbuilt message types (they will be "
                f"skipped until the workspace is built/sourced): {', '.join(unresolvable)}"
            )
    if not chosen:
        print_fn("Nothing selected — aborting without writing anything.")
        return 1

    # 3. hub ---------------------------------------------------------------
    hub_url = args.hub_url
    if hub_url is None and not args.yes:
        reply = ask("\nHub URL (Enter to skip — standalone robot) []: ").strip()
        hub_url = reply or None
    agent_url = None
    if hub_url:
        ip = primary_ip()
        agent_url = f"http://{ip}:7000" if ip else None

    # 4. write -------------------------------------------------------------
    cfg_path = Path(args.config)
    existing_output_dir = "/var/lib/missiondebug/sessions"
    chosen_names = {name for name, _ in chosen}
    anomaly_block = build_anomaly_block(archetype or "ground-vehicle", chosen_names)
    content = render_config(
        robot_id=robot_id,
        topics=chosen,
        output_dir=existing_output_dir,
        hub_url=hub_url,
        agent_url=agent_url,
        subsystem=args.subsystem,
        anomaly_block=anomaly_block,
    )

    print_fn(f"\n--- {cfg_path} ---\n{content}--- end ---")
    if not args.yes:
        reply = ask("Write this config? [Y/n]: ").strip().lower()
        if reply not in ("", "y", "yes"):
            print_fn("Aborted — nothing written.")
            return 1

    try:
        if cfg_path.exists():
            stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
            backup = cfg_path.with_suffix(f".yaml.bak-{stamp}")
            shutil.copy2(cfg_path, backup)
            print_fn(f"(previous config backed up to {backup})")
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(content)
    except PermissionError:
        print_fn(f"error: cannot write {cfg_path} — rerun with sudo.", file=sys.stderr)
        return 1

    print_fn(f"\nWrote {cfg_path} ({len(chosen)} topics).")

    # 5. restart -------------------------------------------------------------
    if shutil.which("systemctl") and Path("/run/systemd/system").is_dir():
        do_restart = args.yes
        if not args.yes:
            reply = ask("Restart missiondebug-agent now? [Y/n]: ").strip().lower()
            do_restart = reply in ("", "y", "yes")
        if do_restart:
            rc = subprocess.call(["systemctl", "restart", "missiondebug-agent"])
            if rc == 0:
                print_fn("Agent restarted. Check: journalctl -u missiondebug-agent -n 20 --no-pager")
            else:
                print_fn("Could not restart (not root?): sudo systemctl restart missiondebug-agent")
    else:
        print_fn("Restart the agent to apply the new config.")
    if hub_url:
        print_fn(f"Within ~60s this robot appears at {hub_url}/fleet/agents")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
