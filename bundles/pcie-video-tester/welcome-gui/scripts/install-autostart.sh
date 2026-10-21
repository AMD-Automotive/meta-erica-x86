#!/usr/bin/env bash
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

# install-autostart.sh TARGET — enable self-launch on login for the autologin
# user by installing the .desktop file into ~/.config/autostart.
#
#   scripts/install-autostart.sh amd@192.168.1.50
#
# Run AFTER scripts/deploy.sh (the .desktop Exec points at /opt/welcome-gui).
set -euo pipefail

TARGET="${1:?usage: install-autostart.sh user@host}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

scp "$HERE/packaging/welcome-gui.desktop" "$TARGET:/tmp/welcome-gui.desktop"

ssh "$TARGET" 'bash -s' <<'REMOTE'
set -euo pipefail
mkdir -p "$HOME/.config/autostart"
install -m 0644 /tmp/welcome-gui.desktop "$HOME/.config/autostart/welcome-gui.desktop"
rm -f /tmp/welcome-gui.desktop
echo "Autostart enabled for $USER (will launch on next login)."
REMOTE
