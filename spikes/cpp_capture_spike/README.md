# C++ capture hot-path CPU spike (throwaway)

One question: **does a C++ rclcpp subscriber use materially less CPU than the
Python rclpy agent**, doing the same per-message work (receive a serialized
message, copy the bytes into a time-windowed ring buffer), under an identical
publisher load?

If yes (e.g. C++ ~1-3% vs Python ~7-8% on a 30Hz/300KB camera), the real
pybind11 C++ capture module is justified. If marginal, we stop.

This is NOT the real capture module. It only measures.

## Build (on the VM, has ROS 2 Humble + colcon)

```bash
# from the repo on the VM:
cd ~/missiondebug/spikes/cpp_capture_spike
source /opt/ros/humble/setup.bash
colcon build
source install/setup.bash
```

## Measure

In terminal A, drive the same publisher used for the Python A/B (RELIABLE so it
is comparable; the spike subscribes best_effort which a RELIABLE publisher can
serve):

```bash
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp; export ROS_LOCALHOST_ONLY=0; unset ROS_DOMAIN_ID
source /opt/ros/humble/setup.bash
python3 /tmp/campub.py     # the 30Hz/300KB CompressedImage publisher
```

In terminal B, run the spike and measure its CPU:

```bash
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp; export ROS_LOCALHOST_ONLY=0; unset ROS_DOMAIN_ID
source /opt/ros/humble/setup.bash
cd ~/missiondebug/spikes/cpp_capture_spike && source install/setup.bash
ros2 run cpp_capture_spike capture_spike >/tmp/spike.log 2>&1 &
SPIKE_PID=$!
sleep 6   # warm up; check /tmp/spike.log shows "buffer: N msgs" climbing
echo "=== C++ spike CPU over 20s ==="
top -b -d 1 -n 20 -p "$SPIKE_PID" | awk -v pid="$SPIKE_PID" '$1==pid {sum+=$9; n++} END {if(n) printf "avg %%CPU: %.1f%% (%d samples)\n", sum/n, n}'
kill $SPIKE_PID
```

Compare against the Python agent's number under the same load (~7-8% from the
2026-06-20 A/B). Confirm the spike's buffer was actually climbing (`/tmp/spike.log`)
so we know it received frames, an idle process would read low and lie.
