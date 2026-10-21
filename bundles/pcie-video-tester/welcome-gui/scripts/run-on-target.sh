#!/usr/bin/env bash
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

# run-on-target.sh TARGET [args...] — run the deployed app in the target's
# graphical session over SSH (handy while iterating).
#
#   scripts/run-on-target.sh amd@192.168.1.50 --windowed
#
# Requires the app to be deployed (scripts/deploy.sh) first. Targets the active
# Wayland/X session of the logged-in autologin user.
set -euo pipefail

TARGET="${1:?usage: run-on-target.sh user@host [args...]}"
shift || true
DEST="${WELCOME_GUI_DIR:-/opt/welcome-gui}"

# shellcheck disable=SC2029  # we intentionally expand args locally
ssh -t "$TARGET" "
    set -e
    uid=\$(id -u)
    export XDG_RUNTIME_DIR=/run/user/\$uid
    # Pick up the running compositor session.
    export WAYLAND_DISPLAY=\${WAYLAND_DISPLAY:-wayland-0}
    export DISPLAY=\${DISPLAY:-:0}
    cd '$DEST'
    python3 -m welcome_gui --config '$DEST/config/welcome.yaml' $*
"
