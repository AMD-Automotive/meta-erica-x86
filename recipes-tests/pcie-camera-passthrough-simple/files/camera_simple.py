#!/usr/bin/env python3
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# Contributors: BW
# Maintainer: BW
# SPDX-License-Identifier: MIT
"""
pcie-camera-passthrough-simple — camera -> PCIe -> display passthrough demo
for the MDB5 bridge.

Demonstrates video from cameras on a Versal endpoint, streamed over PCIe via the
mdb5_v4l2 host driver, displayed on the x86 root complex host in two modes:

  mmap    - GStreamer compositor tiled into a window (Wayland/X, autovideosink).
  dmabuf  - Zero-copy V4L2 -> amdgpu DRM/KMS; one overlay plane per camera,
            taking over the whole display.

Camera configs:
  0  1 channel   (/dev/video0)
  1  4 channels  (/dev/video0 .. /dev/video3)
  2  5 channels  (/dev/video0 .. /dev/video4)

Cameras 0-3 run 1920x1536 (mmap) / 1792x1536 (dmabuf); camera 4 is 3840x2160.
All channels are NV12. Resolutions are configured on the endpoint by
init_cameras.sh (the driver format is static). The endpoint always brings up
all 5 channels; the mode picks the resolution and the config is passed through
too. The app opens just the channels the config variant needs.

Flow:
  1. Connect to the endpoint serial console and run init_cameras.sh <mode> <config>.
  2. Run install_mdb5_drv.sh locally to build + load mdb5_v4l2.
  3. Discover the expected /dev/videoN nodes.
  4. Launch the mmap or dmabuf display path.

Usage:
  camera_simple.py --mode mmap   --config 1
  camera_simple.py --mode dmabuf --config 2 --serial-port /dev/ttyUSB1
"""

import argparse
import os
import shlex
import signal
import subprocess
import sys
import time

import init


def log(msg):
    print(f"[pcie-camera-passthrough-simple] {msg}", flush=True)


def detect_display(card):
    """Best-effort active display resolution 'WxH' from sysfs, or None."""
    try:
        drm = "/sys/class/drm"
        card_name = os.path.basename(card)  # e.g. card0
        for entry in sorted(os.listdir(drm)):
            if not entry.startswith(card_name + "-"):
                continue
            base = os.path.join(drm, entry)
            try:
                with open(os.path.join(base, "enabled")) as f:
                    if f.read().strip() != "enabled":
                        continue
                with open(os.path.join(base, "modes")) as f:
                    first = f.readline().strip()
                if "x" in first:
                    return first
            except OSError:
                continue
    except OSError:
        pass
    return None


def run_mmap(args, devs):
    disp = args.display or detect_display(args.card) or "1920x1080"
    script = os.path.join(init.here(), "camera_mmap.py")
    cmd = [sys.executable, script, "--display", disp, "--sink", args.sink, *devs]
    log(f"launching mmap display ({disp}): {' '.join(shlex.quote(c) for c in cmd)}")
    return subprocess.Popen(cmd, start_new_session=True)


def run_dmabuf(args, devs):
    binp = init.ensure_dmabuf_built()
    if not binp:
        log("ERROR: could not build camera_dmabuf")
        return None
    cmd = [binp, args.card, str(args.config), *devs]
    log(f"launching dmabuf display: {' '.join(shlex.quote(c) for c in cmd)}")
    return subprocess.Popen(cmd, start_new_session=True)


def main():
    ap = argparse.ArgumentParser(
        description="pcie-camera-passthrough-simple — camera to PCIe video "
                    "passthrough demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
NOTES
=====
dmabuf mode takes over the display via DRM/KMS and must run on a bare
virtual terminal (not inside a Wayland/X session). Switch to a free VT
first, e.g.:
    sudo chvt 3
Then run this script from that TTY or over SSH. Switch back afterwards
with e.g. `sudo chvt 2`.

EXAMPLES
========
4-channel tiled window (mmap):
    camera_simple.py --mode mmap --config 1

5-channel zero-copy takeover on amdgpu (dmabuf):
    sudo chvt 3
    camera_simple.py --mode dmabuf --config 2

Use a different serial port:
    camera_simple.py --mode mmap --config 1 --serial-port /dev/ttyUSB0

Skip endpoint bring-up / driver install (already done):
    camera_simple.py --mode mmap --config 1 --no-endpoint --no-install
""")
    ap.add_argument("--mode", required=True, choices=["mmap", "dmabuf"],
                    help="display path")
    ap.add_argument("--config", type=int, default=0, choices=[0, 1, 2],
                    help="camera config: 0=1ch, 1=4ch, 2=5ch (default: 0)")
    ap.add_argument("--serial-port", default="/dev/ttyUSB1",
                    metavar="DEV",
                    help="serial console device for the endpoint "
                         "(default: /dev/ttyUSB1)")
    ap.add_argument("--serial-baud", type=int, default=115200,
                    metavar="BAUD",
                    help="baud rate (default: 115200)")
    ap.add_argument("--endpoint-script", default=None, metavar="PATH",
                    help="path to init_cameras.sh on the endpoint "
                         "(default: resolve on PATH / /usr/bin)")
    ap.add_argument("--card", default="/dev/dri/card0",
                    help="DRM device for dmabuf mode (default: /dev/dri/card0)")
    ap.add_argument("--display", default=None, metavar="WxH",
                    help="output canvas for mmap mode "
                         "(default: auto-detect, fallback 1920x1080)")
    ap.add_argument("--sink", default="autovideosink",
                    help="GStreamer sink for mmap mode (default: autovideosink)")
    ap.add_argument("--no-endpoint", action="store_true",
                    help="skip running init_cameras.sh on the endpoint")
    ap.add_argument("--no-install", action="store_true",
                    help="skip running install_mdb5_drv.sh locally")
    ap.add_argument("--install-script", default=None, metavar="PATH",
                    help="path to install_mdb5_drv.sh "
                         "(default: alongside this script)")
    args = ap.parse_args()

    child = None

    def cleanup(signum=None, frame=None):
        if signum:
            log(f"caught signal {signum}, shutting down")
        if child and child.poll() is None:
            try:
                os.killpg(os.getpgid(child.pid), signal.SIGTERM)
                child.wait(timeout=5)
            except (ProcessLookupError, PermissionError):
                pass
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(child.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        log("done")
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    # 1. Endpoint camera bring-up
    if not args.no_endpoint:
        ep = init.Remote(args.serial_port, baud=args.serial_baud)
        if not init.setup_endpoint(ep, args.mode, args.config,
                                   args.endpoint_script):
            log("endpoint setup failed; exiting")
            sys.exit(1)

    # 2. Host driver install
    if not args.no_install:
        if not init.install_driver(args.install_script):
            log("driver install failed; exiting")
            sys.exit(1)

    # 3. Device discovery
    devs = init.discover_devices(args.config)
    expected = init.expected_devices(args.config)
    if not devs:
        log("ERROR: no mdb5 video devices found")
        sys.exit(1)
    if len(devs) < len(expected):
        log(f"WARNING: expected {len(expected)} channel(s), found {len(devs)}")
    log(f"displaying {len(devs)} channel(s): {', '.join(devs)}")

    # 4. Launch display path
    if args.mode == "mmap":
        child = run_mmap(args, devs)
    else:
        child = run_dmabuf(args, devs)

    if child is None:
        log("ERROR: failed to launch display")
        sys.exit(1)

    # 5. Zero-copy validation (dmabuf mode only): wait briefly for the child to
    #    open its fds, then confirm it holds dmabuf handles (not mmap handles).
    if args.mode == "dmabuf":
        time.sleep(2)
        if child.poll() is None:
            init.validate_zerocopy(child.pid, len(devs))
        else:
            log("WARNING: dmabuf process exited before zero-copy check")

    log("display running — press Ctrl+C to stop")
    rc = child.wait()
    if rc != 0:
        log(f"display exited with rc={rc}")
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
