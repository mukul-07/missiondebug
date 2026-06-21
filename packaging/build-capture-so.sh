#!/usr/bin/env bash
# Build the C++ capture extension (missiondebug_capture) and print the path to
# the resulting .so. Invoked by build-deb.sh during the agent build so the .deb
# ships a prebuilt extension; the agent imports it automatically (~2x cheaper
# capture). Best-effort: if the ROS 2 / pybind11 build deps are missing, this
# exits non-zero and the caller skips it (the agent falls back to pure Python).
#
# Usage: build-capture-so.sh <python-executable> <out-dir>
#   <python-executable>  the venv python the .so must match (ABI).
#   <out-dir>            where to leave the built .so.
# Prints the absolute .so path on success.

set -euo pipefail

PY="${1:?usage: build-capture-so.sh <python> <out-dir>}"
OUT="${2:?usage: build-capture-so.sh <python> <out-dir>}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$HERE/agent/capture_cpp"

# Need a ROS 2 environment (rclcpp) and cmake. If ROS isn't sourced, bail so the
# caller can skip cleanly.
if [ -z "${ROS_DISTRO:-}" ]; then
    # Try to source a system ROS if present.
    for d in /opt/ros/*/setup.bash; do
        [ -f "$d" ] && { . "$d"; break; }
    done
fi
if [ -z "${ROS_DISTRO:-}" ] || ! command -v cmake >/dev/null 2>&1; then
    echo "build-capture-so: no ROS 2 env or cmake; skipping C++ extension" >&2
    exit 1
fi

BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT

# Configure + build with the SAME python the venv uses, so the extension's ABI
# (cpython-3X-...) matches what the agent imports.
cmake -S "$SRC" -B "$BUILD" \
    -DCMAKE_BUILD_TYPE=Release \
    -DPython3_EXECUTABLE="$PY" \
    >/dev/null 2>"$BUILD/err.log" || {
        echo "build-capture-so: cmake configure failed; skipping C++ extension" >&2
        sed 's/^/  /' "$BUILD/err.log" >&2 || true
        exit 1
    }
cmake --build "$BUILD" --parallel >/dev/null 2>>"$BUILD/err.log" || {
    echo "build-capture-so: compile failed; skipping C++ extension" >&2
    sed 's/^/  /' "$BUILD/err.log" >&2 || true
    exit 1
}

SO="$(find "$BUILD" -name 'missiondebug_capture*.so' | head -1)"
if [ -z "$SO" ]; then
    echo "build-capture-so: no .so produced; skipping" >&2
    exit 1
fi

mkdir -p "$OUT"
cp "$SO" "$OUT/"
echo "$OUT/$(basename "$SO")"
