#!/usr/bin/env bash
# Build a missiondebug-agent .deb package.
#
# Approach: stage all files under build/deb/ matching the target filesystem
# layout, then `dpkg-deb --build`. We deliberately avoid dh_python3 + the
# debhelper toolchain — the staging-dir approach is simpler and explicit.
#
# Required tools: dpkg-deb, python3.10 (or python3 >=3.10), pip, fakeroot.
# Must run on Linux (we ship a venv full of Linux wheels).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PKG="$ROOT/packaging"
BUILD="$ROOT/build/deb"
DIST="$ROOT/dist"
VERSION="${MD_VERSION:-1.0.0}"
ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"

# Sanity checks.
if [ "$(uname -s)" != "Linux" ]; then
    echo "build-deb.sh: must be run on Linux (we package Linux wheels)" >&2
    exit 1
fi
for tool in dpkg-deb python3 pip3 fakeroot; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "build-deb.sh: missing required tool: $tool" >&2
        echo "  sudo apt install fakeroot dpkg-dev python3-pip" >&2
        exit 1
    fi
done

PY="$(command -v python3.10 || command -v python3)"
echo "[deb] using python: $PY ($($PY --version))"
echo "[deb] arch:    $ARCH"
echo "[deb] version: $VERSION"

# Clean previous build.
rm -rf "$BUILD"
mkdir -p "$BUILD" "$DIST"

# ---- Filesystem layout under $BUILD ----
INSTALL_PREFIX="$BUILD/opt/missiondebug"
mkdir -p "$INSTALL_PREFIX/bin"
mkdir -p "$BUILD/etc/missiondebug"
mkdir -p "$BUILD/lib/systemd/system"
mkdir -p "$BUILD/DEBIAN"

# ---- Build the venv inside the staging dir ----
echo "[deb] creating venv at $INSTALL_PREFIX/venv"
"$PY" -m venv --system-site-packages "$INSTALL_PREFIX/venv"
"$INSTALL_PREFIX/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_PREFIX/venv/bin/pip" install --quiet "$ROOT/agent"

# Strip pip cache + bytecode + tests from site-packages to shrink the .deb.
SITE="$INSTALL_PREFIX/venv/lib"
find "$SITE" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$SITE" -name "*.dist-info" -type d -exec rm -rf {}/RECORD {}/INSTALLER 2>/dev/null \; || true

# ---- Wrapper script ----
install -m 0755 "$PKG/missiondebug-agent" "$INSTALL_PREFIX/bin/missiondebug-agent"

# ---- Default config (postinst will copy to config.yaml on first install) ----
install -m 0644 "$PKG/default-config.yaml" "$BUILD/etc/missiondebug/config.yaml.default"

# ---- systemd unit ----
install -m 0644 "$PKG/missiondebug-agent.service" "$BUILD/lib/systemd/system/missiondebug-agent.service"

# ---- DEBIAN control files ----
sed -e "s/__VERSION__/$VERSION/g" -e "s/__ARCH__/$ARCH/g" \
    "$PKG/debian/control.template" > "$BUILD/DEBIAN/control"

install -m 0755 "$PKG/debian/postinst" "$BUILD/DEBIAN/postinst"
install -m 0755 "$PKG/debian/prerm"    "$BUILD/DEBIAN/prerm"
install -m 0755 "$PKG/debian/postrm"   "$BUILD/DEBIAN/postrm"

# Mark conffiles so dpkg manages user-edited config sanely.
cat > "$BUILD/DEBIAN/conffiles" <<EOF
/etc/missiondebug/config.yaml.default
EOF

# ---- Build it ----
OUT="$DIST/missiondebug-agent_${VERSION}_${ARCH}.deb"
echo "[deb] building $OUT"
fakeroot dpkg-deb --build --root-owner-group "$BUILD" "$OUT"

echo
echo "Built: $OUT"
echo "Install with:  sudo dpkg -i $OUT"
echo "Reload with:   sudo systemctl restart missiondebug-agent"
echo "Logs:          sudo journalctl -u missiondebug-agent -f"
