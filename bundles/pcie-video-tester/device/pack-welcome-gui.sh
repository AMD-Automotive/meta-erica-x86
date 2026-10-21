#!/bin/sh
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC, TO
# Maintainer: KC

# pack-welcome-gui.sh — packer "script" source for the Welcome GUI app.
#
# Run by make_bundle.py on the HOST while building the .rave2app. It writes the
# bundle member `x86/welcome-gui.tar` (an *uncompressed* tar — the surrounding
# .rave2app is gzipped, so compressing here would gain nothing and would break
# the installer's `tar -xO ... | tar -x` overlay extraction).
#
# Env provided by the packer: RECIPE_DIR, WORK_DIR, OUT_DIR, VERSION.
set -eu

APP_DIR="${RECIPE_DIR}/welcome-gui"
OUT="${WORK_DIR}/x86/welcome-gui.tar"

# The mdb5-v4l2 host driver sources live in the meta-layer recipe and are the
# single source of truth (also consumed by recipes-modules/mdb5-v4l2/*.bb). We
# pull them into the app's drivers/ at pack time instead of keeping a second
# copy in the bundle, so the two never drift. RECIPE_DIR is the bundle dir
# (.../bundles/pcie-video-tester); the repo root is two levels up.
DRIVER_SRC="${RECIPE_DIR}/../../recipes-modules/mdb5-v4l2/files"

# The host-side visual demo (pcie_video_tester.py) is authored in the meta-layer
# test recipe and is the single source of truth (also consumed by
# recipes-tests/pcie-video-tester/*.bb). We pull it into the app's tests/ at
# pack time so the demo lands on the x86 target under /opt/welcome-gui/tests for
# the GUI to drive later. The endpoint-side scripts (ep_vivid_setup.sh + channel
# config) now ship on the Versal rootfs via meta-erica-edf, not from here.
TESTS_SRC="${RECIPE_DIR}/../../recipes-tests/pcie-video-tester/files"
TESTS_SRC2="${RECIPE_DIR}/../../recipes-tests/pcie-camera-passthrough-simple/files"

[ -d "$APP_DIR" ] || { echo "pack-welcome-gui: missing $APP_DIR" >&2; exit 1; }
[ -d "$DRIVER_SRC" ] || {
    echo "pack-welcome-gui: missing driver sources $DRIVER_SRC" >&2; exit 1; }
[ -d "$TESTS_SRC" ] || {
    echo "pack-welcome-gui: missing test sources $TESTS_SRC" >&2; exit 1; }
[ -d "$TESTS_SRC2" ] || {
    echo "pack-welcome-gui: missing test sources $TESTS_SRC2" >&2; exit 1; }

# Stage drivers/ = the app's README + the mdb5-v4l2 sources from the meta-layer.
STAGE="${WORK_DIR}/wg-drivers-stage"
rm -rf "$STAGE"
mkdir -p "$STAGE/drivers"
cp "$APP_DIR/drivers/README.md" "$STAGE/drivers/"
cp "$DRIVER_SRC"/Makefile "$DRIVER_SRC"/*.c "$DRIVER_SRC"/*.h "$STAGE/drivers/"

# Stage tests/ = the app's README + the host-side visual demo from the meta-layer.
mkdir -p "$STAGE/tests"
cp "$APP_DIR/tests/README.md" "$STAGE/tests/"
cp "$TESTS_SRC/pcie_video_tester.py" "$STAGE/tests/"
chmod 0755 "$STAGE/tests/pcie_video_tester.py"

# Add the pcie-camera-passthrough-simple test
cp "$TESTS_SRC2/camera_simple.py" "$STAGE/tests/"
cp "$TESTS_SRC2/camera_mmap.py" "$STAGE/tests/"
cp "$TESTS_SRC2/init.py" "$STAGE/tests/"
cp "$TESTS_SRC2/Makefile" "$STAGE/tests/"
cp "$TESTS_SRC2/camera_dmabuf.c" "$STAGE/tests/"
cp "$TESTS_SRC2/install_mdb5_drv.sh" "$STAGE/tests/"
chmod 0755 "$STAGE/tests/camera_simple.py"
chmod 0755 "$STAGE/tests/camera_mmap.py"
chmod 0755 "$STAGE/tests/init.py"
chmod 0755 "$STAGE/tests/install_mdb5_drv.sh"


# The member's top level becomes the overlay's `target` (/opt/welcome-gui), so
# pack the app contents at the tar root. drivers/ is appended from the stage
# (uncompressed tar supports append) so it carries the meta-layer sources.
tar cf "$OUT" -C "$APP_DIR" \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    welcome_gui \
    config \
    requirements.txt \
    scripts/run-welcome-gui.sh \
    packaging/welcome-gui.desktop
tar rf "$OUT" -C "$STAGE" drivers
tar rf "$OUT" -C "$STAGE" tests

echo "pack-welcome-gui: wrote $OUT ($(wc -c < "$OUT") bytes)"
echo "pack-welcome-gui: bundled drivers: $(ls "$STAGE/drivers" | tr '\n' ' ')"
echo "pack-welcome-gui: bundled tests: $(ls "$STAGE/tests" | tr '\n' ' ')"
