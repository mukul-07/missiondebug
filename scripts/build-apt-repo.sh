#!/usr/bin/env bash
# Build (or update) the static apt repository tree served by GitHub Pages.
#
# Usage:
#   ./scripts/build-apt-repo.sh <debs-dir> <repo-dir>
#
#   <debs-dir>  directory containing freshly built .debs (per-distro
#               filenames from build-deb.sh: *_ubuntu22.04_*.deb,
#               *_ubuntu24.04_*.deb, *_all.deb)
#   <repo-dir>  output tree. May already contain a previous repo state
#               (checked out from gh-pages) — new debs are ADDED to the
#               pool, then the pool is PRUNED to the newest
#               MD_APT_KEEP_VERSIONS (default 3) versions per package.
#               GitHub Pages refuses sites over ~1GB (v0.8.0's deploy
#               failed at 2.8GB of accumulated pool), and every pruned
#               version remains downloadable from its GitHub Release.
#               Indexes are regenerated over what remains.
#
# Layout produced (apt standard):
#   pool/jammy/*.deb           Ubuntu 22.04 / ROS 2 Humble packages
#   pool/noble/*.deb           Ubuntu 24.04 / ROS 2 Jazzy packages
#   dists/<suite>/main/binary-{amd64,arm64}/Packages{,.gz}
#   dists/<suite>/{Release,Release.gpg,InRelease}
#   missiondebug-archive-key.asc
#
# Signing: expects the repo's GPG private key already imported into the
# default keyring (CI imports it from the APT_GPG_PRIVATE_KEY secret).
# Requires: dpkg-dev (dpkg-scanpackages), apt-utils (apt-ftparchive), gpg.

set -euo pipefail

DEBS="${1:?usage: build-apt-repo.sh <debs-dir> <repo-dir>}"
REPO="${2:?usage: build-apt-repo.sh <debs-dir> <repo-dir>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SUITES="jammy noble"

for tool in dpkg-scanpackages apt-ftparchive gpg; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "build-apt-repo.sh: missing required tool: $tool" >&2
        echo "  sudo apt install dpkg-dev apt-utils gnupg" >&2
        exit 1
    }
done

distro_tag() {
    case "$1" in
        jammy) echo "ubuntu22.04" ;;
        noble) echo "ubuntu24.04" ;;
        *) echo "unknown suite: $1" >&2; exit 2 ;;
    esac
}

# ---- pool: add the new debs ------------------------------------------------

for suite in $SUITES; do
    mkdir -p "$REPO/pool/$suite"
    tag="$(distro_tag "$suite")"
    found=0
    for deb in "$DEBS"/*_"${tag}"_*.deb; do
        [ -e "$deb" ] || continue
        cp -f "$deb" "$REPO/pool/$suite/"
        found=1
    done
    # Architecture-independent packages (web) go in every suite.
    for deb in "$DEBS"/*_all.deb; do
        [ -e "$deb" ] || continue
        cp -f "$deb" "$REPO/pool/$suite/"
        found=1
    done
    [ "$found" = 1 ] || { echo "no debs found for $suite ($tag) in $DEBS" >&2; exit 1; }
done

# ---- prune: keep the newest N versions per package ---------------------------
# GitHub Pages hard-fails deploys once the published site outgrows ~1GB.
# Older versions stay downloadable from their GitHub Releases.

KEEP_VERSIONS="${MD_APT_KEEP_VERSIONS:-3}"

for suite in $SUITES; do
    pkgs="$(find "$REPO/pool/$suite" -name '*.deb' -exec basename {} \; \
        | cut -d_ -f1 | sort -u)"
    for pkg in $pkgs; do
        keep="$(find "$REPO/pool/$suite" -name "${pkg}_*.deb" -exec basename {} \; \
            | cut -d_ -f2 | sort -u -V | tail -n "$KEEP_VERSIONS" | tr '\n' ' ')"
        for f in "$REPO/pool/$suite/${pkg}"_*.deb; do
            [ -e "$f" ] || continue
            v="$(basename "$f" | cut -d_ -f2)"
            case " $keep " in
                *" $v "*) ;;
                *) rm -f "$f"; echo "pruned from pool ($suite): $(basename "$f")" ;;
            esac
        done
    done
done

# ---- indexes + signed Release per suite -------------------------------------

cd "$REPO"

for suite in $SUITES; do
    for arch in amd64 arm64; do
        d="dists/$suite/main/binary-$arch"
        mkdir -p "$d"
        # --arch includes Architecture: all packages alongside $arch.
        dpkg-scanpackages --multiversion --arch "$arch" "pool/$suite" \
            > "$d/Packages"
        gzip -9 -c "$d/Packages" > "$d/Packages.gz"
    done

    apt-ftparchive \
        -o "APT::FTPArchive::Release::Origin=MissionDebug" \
        -o "APT::FTPArchive::Release::Label=MissionDebug" \
        -o "APT::FTPArchive::Release::Suite=$suite" \
        -o "APT::FTPArchive::Release::Codename=$suite" \
        -o "APT::FTPArchive::Release::Components=main" \
        -o "APT::FTPArchive::Release::Architectures=amd64 arm64" \
        release "dists/$suite" > "dists/$suite/Release"

    gpg --batch --yes -abs -o "dists/$suite/Release.gpg" "dists/$suite/Release"
    gpg --batch --yes --clearsign -o "dists/$suite/InRelease" "dists/$suite/Release"
done

# ---- repo root: public key + landing page -----------------------------------

cp -f "$ROOT/packaging/apt/missiondebug-archive-key.asc" missiondebug-archive-key.asc

cat > index.html <<'EOF'
<!doctype html>
<title>MissionDebug apt repository</title>
<style>body{font-family:system-ui;max-width:42rem;margin:3rem auto;padding:0 1rem}pre{background:#f4f4f4;padding:1rem;overflow-x:auto}</style>
<h1>MissionDebug apt repository</h1>
<p>Signed apt repository for <a href="https://github.com/mukul-07/missiondebug">MissionDebug</a>.
Suites: <code>jammy</code> (Ubuntu 22.04 / ROS 2 Humble), <code>noble</code> (Ubuntu 24.04 / ROS 2 Jazzy).</p>
<pre>
sudo install -d /etc/apt/keyrings
curl -fsSL https://mukul-07.github.io/missiondebug/missiondebug-archive-key.asc \
  | sudo gpg --dearmor -o /etc/apt/keyrings/missiondebug.gpg
echo "deb [signed-by=/etc/apt/keyrings/missiondebug.gpg] https://mukul-07.github.io/missiondebug $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/missiondebug.list
sudo apt update
sudo apt install missiondebug-agent missiondebug-backend missiondebug-web
</pre>
EOF

# GitHub Pages must not run the tree through Jekyll (dists/ starts fine,
# but keep it explicit and future-proof).
touch .nojekyll

echo
echo "apt repo tree ready in $REPO:"
find dists -name "Packages" | while read -r p; do
    echo "  $p: $(grep -c '^Package:' "$p") package entries"
done
