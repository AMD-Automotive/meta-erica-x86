#!/usr/bin/env bash
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

# bootstrap-target.sh TARGET — install runtime dependencies on the target once.
#
#   scripts/bootstrap-target.sh amd@192.168.1.50
#
# Installs PySide6 + pyserial + PyYAML + pyte and the kernel build
# prerequisites, and adds the login user to the `dialout` group so the GUI can
# open the Versal serial console unprivileged.
#
# Override the SSH command for password auth, e.g.:
#   SSH='sshpass -p amd ssh -o StrictHostKeyChecking=no \
#        -o PreferredAuthentications=password -o PubkeyAuthentication=no' \
#       scripts/bootstrap-target.sh amd@192.168.0.146
set -euo pipefail

TARGET="${1:?usage: bootstrap-target.sh user@host}"
SSH="${SSH:-ssh}"

# The user whose session runs the GUI (defaults to the SSH login user).
LOGIN_USER="${TARGET##*@}"
[ "$LOGIN_USER" = "$TARGET" ] && LOGIN_USER="amd"

$SSH "$TARGET" "sudo bash -s '$LOGIN_USER'" <<'REMOTE'
set -euo pipefail
LOGIN_USER="$1"
export DEBIAN_FRONTEND=noninteractive
apt-get update
# Prefer distro packages (smaller, integrate with the desktop).
apt-get install -y --no-install-recommends \
    python3 python3-pyside6.qtwidgets python3-pyside6.qtcore python3-pyside6.qtgui \
    python3-serial python3-yaml python3-pyte \
    python3-ftdi \
    qt6-wayland libxcb-cursor0 \
    build-essential bc kmod libelf-dev "linux-headers-$(uname -r)" || true

# Fall back to pip for PySide6 if the distro package is unavailable.
if ! python3 -c 'import PySide6' 2>/dev/null; then
    apt-get install -y --no-install-recommends python3-pip
    pip3 install --break-system-packages PySide6 pyserial PyYAML
fi

# Serial console access: /dev/ttyUSB* are root:dialout 0660, so the GUI user
# must be in `dialout` to open the Versal console without sudo.
if id "$LOGIN_USER" >/dev/null 2>&1; then
    usermod -aG dialout "$LOGIN_USER"
    echo "Added $LOGIN_USER to dialout (re-login required to take effect)."
fi
echo "Bootstrap complete."
REMOTE

