#!/usr/bin/env bash
# Build MissionDebug .deb packages.
#
# Usage:
#   ./build-deb.sh                    # builds agent (back-compat default)
#   ./build-deb.sh agent
#   ./build-deb.sh backend
#   ./build-deb.sh web
#   ./build-deb.sh all                # builds all three
#
# Stages files under build/deb/<target>/ matching the target filesystem
# layout, then `dpkg-deb --build`. We deliberately avoid debhelper — the
# staging-dir approach is simpler and explicit.
#
# Required: dpkg-deb, fakeroot. agent/backend also need python3, pip.
# web also needs pnpm (or npm). Must run on Linux (Linux wheels go in
# the venvs).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PKG="$ROOT/packaging"
BUILD="$ROOT/build/deb"
DIST="$ROOT/dist"
VERSION="${MD_VERSION:-1.0.0}"
ARCH_NATIVE="$(dpkg --print-architecture 2>/dev/null || uname -m)"

TARGET="${1:-agent}"

if [ "$(uname -s)" != "Linux" ]; then
    echo "build-deb.sh: must be run on Linux" >&2
    exit 1
fi
for tool in dpkg-deb fakeroot; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "build-deb.sh: missing required tool: $tool" >&2
        echo "  sudo apt install fakeroot dpkg-dev" >&2
        exit 1
    }
done

mkdir -p "$DIST"

# ---- helpers --------------------------------------------------------------

write_control() {
    sed -e "s/__VERSION__/$VERSION/g" -e "s/__ARCH__/$ARCH_NATIVE/g" \
        "$1" > "$2"
}

# pip writes absolute shebangs based on where the venv was created. On
# install, the venv path will be different (no /home/.../build/deb/...
# prefix), so any entry-point script execs nonexistent python and dies
# with status=203/EXEC. Rewrite shebangs from the staging path to the
# final install path.
relocate_venv_shebangs() {
    # relocate_venv_shebangs <staging-prefix> <install-prefix>
    local from="$1" to="$2"
    # Walk every script in bin/ that has a shebang pointing into the
    # staging path; sed-replace in place.
    grep -rIl --include='*' "^#!$from" "$from/bin" 2>/dev/null \
        | while read -r f; do
            sed -i "1s|^#!$from|#!$to|" "$f"
        done
}

# ---- agent ----------------------------------------------------------------

build_agent() {
    local STAGE="$BUILD/agent"
    local PREFIX="$STAGE/opt/missiondebug"

    for tool in python3 pip3; do
        command -v "$tool" >/dev/null 2>&1 || {
            echo "build-deb.sh agent: missing required tool: $tool" >&2
            exit 1
        }
    done
    local PY
    PY="$(command -v python3.10 || command -v python3)"
    echo "[agent] python: $PY ($($PY --version)) arch=$ARCH_NATIVE version=$VERSION"

    rm -rf "$STAGE"
    mkdir -p "$PREFIX/bin" "$STAGE/etc/missiondebug" \
             "$STAGE/lib/systemd/system" "$STAGE/DEBIAN"

    "$PY" -m venv --system-site-packages "$PREFIX/venv"
    "$PREFIX/venv/bin/pip" install --quiet --upgrade pip
    "$PREFIX/venv/bin/pip" install --quiet "$ROOT/agent"

    find "$PREFIX/venv/lib" -name "__pycache__" -type d -prune \
        -exec rm -rf {} + 2>/dev/null || true

    relocate_venv_shebangs "$PREFIX/venv" "/opt/missiondebug/venv"

    install -m 0755 "$PKG/missiondebug-agent" "$PREFIX/bin/missiondebug-agent"
    install -m 0644 "$PKG/default-config.yaml" "$STAGE/etc/missiondebug/config.yaml.default"
    install -m 0644 "$PKG/missiondebug-agent.service" \
        "$STAGE/lib/systemd/system/missiondebug-agent.service"

    write_control "$PKG/debian/control.template" "$STAGE/DEBIAN/control"
    install -m 0755 "$PKG/debian/postinst" "$STAGE/DEBIAN/postinst"
    install -m 0755 "$PKG/debian/prerm"    "$STAGE/DEBIAN/prerm"
    install -m 0755 "$PKG/debian/postrm"   "$STAGE/DEBIAN/postrm"

    cat > "$STAGE/DEBIAN/conffiles" <<EOF
/etc/missiondebug/config.yaml.default
EOF

    local OUT="$DIST/missiondebug-agent_${VERSION}_${ARCH_NATIVE}.deb"
    fakeroot dpkg-deb --build --root-owner-group "$STAGE" "$OUT"
    echo "[agent] built $OUT"
}

# ---- backend --------------------------------------------------------------

build_backend() {
    local STAGE="$BUILD/backend"
    local PREFIX="$STAGE/opt/missiondebug"

    for tool in python3 pip3; do
        command -v "$tool" >/dev/null 2>&1 || {
            echo "build-deb.sh backend: missing required tool: $tool" >&2
            exit 1
        }
    done
    local PY
    PY="$(command -v python3.10 || command -v python3)"
    echo "[backend] python: $PY ($($PY --version)) arch=$ARCH_NATIVE version=$VERSION"

    rm -rf "$STAGE"
    mkdir -p "$PREFIX" "$STAGE/etc/missiondebug" \
             "$STAGE/lib/systemd/system" "$STAGE/DEBIAN"

    # Separate venv from the agent's so they upgrade independently.
    "$PY" -m venv "$PREFIX/backend-venv"
    "$PREFIX/backend-venv/bin/pip" install --quiet --upgrade pip
    "$PREFIX/backend-venv/bin/pip" install --quiet "$ROOT/backend"

    find "$PREFIX/backend-venv/lib" -name "__pycache__" -type d -prune \
        -exec rm -rf {} + 2>/dev/null || true

    relocate_venv_shebangs "$PREFIX/backend-venv" "/opt/missiondebug/backend-venv"

    install -m 0644 "$PKG/default-backend.env" \
        "$STAGE/etc/missiondebug/backend.env.default"
    install -m 0644 "$PKG/missiondebug-backend.service" \
        "$STAGE/lib/systemd/system/missiondebug-backend.service"

    write_control "$PKG/debian/backend/control.template" "$STAGE/DEBIAN/control"
    install -m 0755 "$PKG/debian/backend/postinst" "$STAGE/DEBIAN/postinst"
    install -m 0755 "$PKG/debian/backend/prerm"    "$STAGE/DEBIAN/prerm"
    install -m 0755 "$PKG/debian/backend/postrm"   "$STAGE/DEBIAN/postrm"

    cat > "$STAGE/DEBIAN/conffiles" <<EOF
/etc/missiondebug/backend.env.default
EOF

    local OUT="$DIST/missiondebug-backend_${VERSION}_${ARCH_NATIVE}.deb"
    fakeroot dpkg-deb --build --root-owner-group "$STAGE" "$OUT"
    echo "[backend] built $OUT"
}

# ---- web ------------------------------------------------------------------

build_web() {
    local STAGE="$BUILD/web"
    local WEB_TARGET="$STAGE/var/lib/missiondebug/web"

    local PNPM
    PNPM="$(command -v pnpm || true)"
    if [ -z "$PNPM" ] && ! command -v npm >/dev/null 2>&1; then
        echo "build-deb.sh web: need pnpm or npm to build the web dist" >&2
        exit 1
    fi
    echo "[web] arch=all version=$VERSION"

    rm -rf "$STAGE"
    mkdir -p "$WEB_TARGET" "$STAGE/DEBIAN"

    pushd "$ROOT/web" >/dev/null
    if [ -n "$PNPM" ]; then
        pnpm install --frozen-lockfile
        pnpm build
    else
        npm ci
        npm run build
    fi
    popd >/dev/null

    cp -r "$ROOT/web/dist/." "$WEB_TARGET/"

    write_control "$PKG/debian/web/control.template" "$STAGE/DEBIAN/control"
    # Web is arch-independent.
    sed -i 's/^Architecture:.*/Architecture: all/' "$STAGE/DEBIAN/control"

    install -m 0755 "$PKG/debian/web/postinst" "$STAGE/DEBIAN/postinst"
    install -m 0755 "$PKG/debian/web/prerm"    "$STAGE/DEBIAN/prerm"
    install -m 0755 "$PKG/debian/web/postrm"   "$STAGE/DEBIAN/postrm"

    local OUT="$DIST/missiondebug-web_${VERSION}_all.deb"
    fakeroot dpkg-deb --build --root-owner-group "$STAGE" "$OUT"
    echo "[web] built $OUT"
}

# ---- dispatch -------------------------------------------------------------

case "$TARGET" in
    agent)   build_agent ;;
    backend) build_backend ;;
    web)     build_web ;;
    all)     build_agent; build_backend; build_web ;;
    *)
        echo "Unknown target: $TARGET (expected: agent | backend | web | all)" >&2
        exit 2
        ;;
esac

echo
echo "Done. Output in $DIST/:"
ls -lh "$DIST"
