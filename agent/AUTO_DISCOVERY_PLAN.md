# Tier 1: auto-source built message workspaces (zero-config custom messages)

Goal (Shane's bar): install the cap and it just works. No per-robot
`rosSetupFiles`, no per-robot config. If a message package (px4_msgs or any
custom package) is *built* anywhere on the robot, the agent finds and sources it
automatically so `import px4_msgs.msg` / `create_generic_subscription` resolve.

This does NOT change the physical constraint: a recorder must subscribe, which
needs the package BUILT (compiled Python module + typesupport .so), not just
`.msg` text under `share/`. Auto-discovery finds *built* workspaces; it cannot
conjure bindings that were never generated. That residual is Tier 3 (surface it
honestly), out of scope here.

## Where the change goes: the WRAPPER (packaging/missiondebug-agent)

The wrapper is the only bash context that sources into the environment the
`python -m missiondebug_agent.main` interpreter inherits. Sourcing must happen
BEFORE the interpreter starts (AMENT_PREFIX_PATH + the rclpy C-extension paths
are read at import time), so discovery cannot live in Python. It slots in right
after the existing `MD_ROS_SETUP_FILES` block (wrapper lines 45-56).

Order of sourcing (later overrides earlier on AMENT_PREFIX_PATH, which is
correct: base < auto-found overlays < explicit operator overlays):
1. base ROS (`/opt/ros/{humble,jazzy}/setup.bash`) - unchanged
2. NEW: auto-discovered built workspaces (this plan)
3. explicit `MD_ROS_SETUP_FILES` overlays - unchanged (operator override still wins)

## What counts as a "built workspace" to source

A colcon/ament install prefix that has:
- a top-level `local_setup.bash` (prefer) or `setup.bash`, AND
- an ament index marker `share/ament_index/resource_index/packages` in EITHER of
  the two colcon layouts:
  - TOP-LEVEL `<prefix>/share/ament_index/...` (colcon `--merge-install`), OR
  - PER-PACKAGE `<prefix>/*/share/ament_index/...` (colcon's DEFAULT ISOLATED
    install, which is how the PX4 docs build px4_msgs).

CRITICAL (caught in adversarial review 2026-07-01): the FIRST cut only accepted the
top-level index, which silently MISSED the DEFAULT isolated layout -> px4_ws built
the documented `colcon build` way was rejected, defeating the whole zero-config
goal. The top-level `local_setup.bash` of an isolated workspace IS functional (it
sources the per-package prefixes), so we accept EITHER index layout. A bare dir
with a setup file but NO ament index anywhere (e.g. only `.msg` text) is rejected -
not built, a recorder cannot subscribe.

We source `local_setup.bash` when present (it adds ONLY that prefix to the env,
no re-sourcing of the base underlay -> no duplication / no clobber), else
`setup.bash`.

## set -e safety (caught in testing 2026-07-01)

The wrapper runs `set -e`. `set -e` stays ACTIVE inside a sourced file, and a benign
non-zero command inside a workspace's setup.bash (common in ROS setup files) would
ABORT the wrapper and kill the agent - violating "never fatal". FIX: wrap every
`source` (base ROS, auto-source, AND the pre-existing MD_ROS_SETUP_FILES block,
which had the same latent bug) with `set +e` ... `set -e`. Proven: a setup.bash
containing `false; grep miss` no longer aborts; the agent still starts.

### Search roots (bounded, cheap, no full-FS walk)

Scan a fixed, ordered list of likely locations, shallow (maxdepth so we do not
walk gigantic trees). Env override `MD_ROS_WS_SEARCH` (colon-separated) replaces
the defaults for odd layouts. Default roots:
- `$AMENT_PREFIX_PATH` entries' parents already sourced by base ROS - SKIP (they
  are the underlay; do not re-add).
- `$COLCON_PREFIX_PATH` (colcon sets this when a ws is active) - each entry.
- `$HOME` : `*/install`, `*_ws/install`, `ros2_ws/install`, `colcon_ws/install`
  (maxdepth 2 under $HOME).
- `/opt/*/install`, `/opt/*_ws/install` (maxdepth 2 under /opt) - covers vendor
  installs; NOT /opt/ros/* (that is the base, already sourced).
- `/workspaces/*/install`, `/root/*/install` (common container/devcontainer spots).
- The sandbox durable dir `/home/transitive/*/install` (in case a ws is bundled).

Dedup discovered prefixes; skip any prefix already on AMENT_PREFIX_PATH (do not
re-source the base). Cap the number sourced (e.g. 20) with a warning if exceeded,
so a pathological tree cannot hang startup.

### Why this is safe

- Read-only: we only `source` setup files, never write.
- Idempotent-ish: sourcing the same prefix twice is harmless (ament dedups its
  own path entries); we dedup anyway.
- Never fatal: a bad/partial setup file is skipped with a warning (mirror the
  existing MD_ROS_SETUP_FILES loop). Startup never aborts on discovery.
- Bounded: shallow maxdepth + a hard cap on count -> no full-FS walk, no hang.
- Off-switch: `MD_ROS_AUTOSOURCE=0` disables it entirely (belt-and-suspenders).

## Wrapper block (drop-in, after the MD_ROS_SETUP_FILES loop)

```bash
# Auto-source built ROS 2 message workspaces found on the robot, so custom
# message packages (px4_msgs and the like) resolve with NO per-robot config.
# We only source ament INSTALL PREFIXES that were actually built (they have an
# ament index + a local_setup.bash); `.msg` text alone is not enough for a
# recorder. Read-only, never fatal, bounded (shallow scan + a hard cap), and
# disabled by MD_ROS_AUTOSOURCE=0. Base ROS is already sourced above; explicit
# MD_ROS_SETUP_FILES overlays are sourced after this, so an operator override
# still wins.
if [ "${MD_ROS_AUTOSOURCE:-1}" != "0" ]; then
    _md_roots="${MD_ROS_WS_SEARCH:-}"
    if [ -z "$_md_roots" ]; then
        _md_roots="${COLCON_PREFIX_PATH:-}"
        for _g in "$HOME"/*/install "$HOME"/*_ws/install "$HOME"/ros2_ws/install \
                  /opt/*/install /opt/*_ws/install /workspaces/*/install \
                  /home/transitive/*/install; do
            [ -d "$_g" ] && _md_roots="${_md_roots}:${_g}"
        done
    fi
    _md_count=0
    _md_seen=":${AMENT_PREFIX_PATH:-}:"   # prefixes already on the underlay
    IFS=':' read -ra _MD_ROOTS <<< "$_md_roots"
    for _p in "${_MD_ROOTS[@]}"; do
        [ -z "$_p" ] && continue
        [ -d "$_p" ] || continue
        # must be a real built prefix (ament index present)
        [ -d "$_p/share/ament_index/resource_index/packages" ] || continue
        case "$_md_seen" in *":$_p:"*) continue ;; esac   # already sourced (underlay/dedup)
        _setup="$_p/local_setup.bash"; [ -f "$_setup" ] || _setup="$_p/setup.bash"
        [ -f "$_setup" ] || continue
        if [ "$_md_count" -ge "${MD_ROS_AUTOSOURCE_MAX:-20}" ]; then
            echo "missiondebug-agent: auto-source cap reached; skipping the rest" >&2
            break
        fi
        # shellcheck disable=SC1090
        source "$_setup" && {
            _md_seen="${_md_seen}${_p}:"
            _md_count=$((_md_count+1))
            echo "missiondebug-agent: auto-sourced built workspace: $_p" >&2
        }
    done
fi
```

(Uses only bash 3.2-safe constructs so it runs on the VM's bash and the sandbox
alike; the file is bash, sandbox runs it via the .deb.)

## Shim change (capability, robot/main.js) - SMALL

- The shim already exposes MD_ROS_SETUP_FILES via agentSpawnEnv(). Add nothing
  required - auto-source is ON by default in the wrapper. Optional: let the
  operator DISABLE it or set custom search roots from per-device config
  (`autoSourceWorkspaces: false` -> MD_ROS_AUTOSOURCE=0;
  `rosWorkspaceSearch: [..]` -> MD_ROS_WS_SEARCH). Nice-to-have, not needed for
  the core win. Keep the explicit `rosSetupFiles` path too (it wins, for the odd
  case auto-discovery misses).

## Config surface (config.py) - none required

`ros_setup_files` stays as the explicit override. No new REQUIRED field. If we
add the opt-out/custom-roots, mirror as `auto_source_workspaces: bool = True` +
`ros_workspace_search: list[str] = []` for standalone .deb users.

## Verification (VM, real px4_msgs - we already have ~/px4_ws built)

The exact test that PROVES zero-config, reusing today's px4_ws:
1. Config with the px4 topic but NO rosSetupFiles and NO MD_ROS_SETUP_FILES:
   ```
   topics: [{name: /fmu/out/vehicle_odometry, type: px4_msgs/msg/VehicleOdometry}]
   ```
2. Run the NEW wrapper agent with NOTHING set (no MD_ROS_SETUP_FILES):
   - expect the log line `auto-sourced built workspace: /home/md/px4_ws/install`
   - publish a real VehicleOdometry, POST /sessions/save -> real MCAP (NOT 409)
3. Negative: `MD_ROS_AUTOSOURCE=0` + no overlay -> back to the 409 (proves the
   auto-source is what did it, and the off-switch works).
4. Regression: the dev harness (MD_AGENT_CMD=python3 path) and the existing
   rosSetupFiles path both still work (auto-source is additive).
Before/after is the same shape as the 0.7.3 proof, minus the manual step.

## Release

Agent-only change (wrapper). Bump AGENT_VERSION + API 0.7.3 -> 0.7.4 (or 0.8.0 if
we also do the opt-out config). Tag -> CI release-debs.yml (per distro/arch, C++
extension still bundled). Then bump the capability DEFAULT_AGENT_VERSION so robots
self-download it. Backward compatible: no config change needed; existing
rosSetupFiles users unaffected (their overlay still sources, after auto-source).

## Scope boundary

- IN: auto-source built workspaces (this doc) = Tier 1.
- NOT here: discovered-list UI on the card (Tier 2, capability+widget) and the
  "type not built" honest card state (Tier 3). Tier 2 leans on the existing GET
  /topics (name+type+resolvable). Sequence: ship Tier 1 (kills the manual step),
  then Tier 2 (removes hand-typing types), then Tier 3 (honest residual).
