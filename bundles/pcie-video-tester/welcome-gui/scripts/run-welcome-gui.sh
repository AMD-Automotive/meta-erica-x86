#!/usr/bin/env bash
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

# run-welcome-gui.sh — launcher used by the autostart .desktop entry.
#
# Installed to /opt/welcome-gui/run-welcome-gui.sh. Ensures the app finds its
# bundled config and runs against the user's session.
set -euo pipefail

APP_DIR="${WELCOME_GUI_DIR:-/opt/welcome-gui}"
CONFIG="${WELCOME_GUI_CONFIG:-$APP_DIR/config/welcome.yaml}"

# Mark the Desktop launcher icon trusted so GNOME (ding) lets it launch without
# the "Untrusted application launcher" prompt. This needs the session D-Bus, so
# it can't be done by the offline installer — do it here on (every) login. Cheap
# and idempotent; ignore failures (e.g. no Desktop icon / no gio).
desktop_icon="${HOME:-/home/$(id -un)}/Desktop/welcome-gui.desktop"
if [ -f "$desktop_icon" ] && command -v gio >/dev/null 2>&1; then
    gio set "$desktop_icon" metadata::trusted true 2>/dev/null || true
    chmod 0755 "$desktop_icon" 2>/dev/null || true
fi

cd "$APP_DIR"
exec python3 -m welcome_gui --config "$CONFIG" "$@"
