#!/usr/bin/env bash
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

# deploy.sh TARGET [DEST] — copy the app to the target over SSH (no rsync).
#
#   scripts/deploy.sh amd@192.168.1.50              # -> /opt/welcome-gui
#   scripts/deploy.sh amd@192.168.1.50 /opt/welcome-gui
#
# rsync is NOT installed on the target, so we stream a tarball over SSH and
# unpack it there. Override the SSH command for password auth, e.g.:
#
#   SSH='sshpass -p amd ssh -o StrictHostKeyChecking=no \
#        -o PreferredAuthentications=password -o PubkeyAuthentication=no' \
#       scripts/deploy.sh amd@192.168.0.146
set -euo pipefail

TARGET="${1:?usage: deploy.sh user@host [dest]}"
DEST="${2:-/opt/welcome-gui}"
SSH="${SSH:-ssh}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$HERE/../../.." && pwd)"
TESTS_SRC="$REPO_ROOT/recipes-tests/pcie-video-tester/files"
STAGE="/tmp/welcome-gui-stage"

echo "Deploying $HERE -> $TARGET:$DEST (via $SSH)"

# Build a local staging tree so the meta-layer visual-demo file (the single
# source of truth, also bundled by pack-welcome-gui.sh) rides along under tests/.
# We keep only tests/README.md in git here; the demo .py is pulled from
# recipes-tests/ at deploy time, exactly like the packer does. The endpoint-side
# scripts ship on the Versal rootfs via meta-erica-edf, not from here.
LOCAL_STAGE="$(mktemp -d)"
trap 'rm -rf "$LOCAL_STAGE"' EXIT
mkdir -p "$LOCAL_STAGE/tests"
cp "$HERE/tests/README.md" "$LOCAL_STAGE/tests/"
if [ -d "$TESTS_SRC" ]; then
    cp "$TESTS_SRC/pcie_video_tester.py" "$LOCAL_STAGE/tests/"
    chmod 0755 "$LOCAL_STAGE/tests/pcie_video_tester.py"
else
    echo "WARNING: $TESTS_SRC not found — deploying tests/README.md only" >&2
fi

# Stream the app (package, config, drivers, launcher, tests) as a tarball;
# unpack into a staging dir, then sudo-move into place so we never need rsync or
# rsync-over-sudo gymnastics.
tar czf - \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.git' \
    -C "$HERE" welcome_gui config drivers scripts/run-welcome-gui.sh \
    -C "$LOCAL_STAGE" tests \
  | $SSH "$TARGET" "
        set -e
        rm -rf '$STAGE' && mkdir -p '$STAGE'
        tar xzf - -C '$STAGE'
        sudo mkdir -p '$DEST'
        sudo cp -r '$STAGE/welcome_gui' '$DEST/'
        sudo cp -r '$STAGE/config' '$DEST/'
        sudo cp -r '$STAGE/drivers' '$DEST/'
        sudo cp -r '$STAGE/tests' '$DEST/'
        sudo install -m 0755 '$STAGE/scripts/run-welcome-gui.sh' '$DEST/run-welcome-gui.sh'
        sudo chown -R root:root '$DEST'
        rm -rf '$STAGE'
        echo \"Installed to $DEST\"
    "

echo "Done. Run it with: scripts/run-on-target.sh $TARGET"
