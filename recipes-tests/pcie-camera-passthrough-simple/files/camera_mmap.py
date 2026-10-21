#!/usr/bin/env python3
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# Contributors: BW
# Maintainer: BW
# SPDX-License-Identifier: MIT
"""
pcie-camera-passthrough-simple mmap display.

Standalone GStreamer path for the `mmap` mode. Takes one or more
mdb5_v4l2 capture devices, reads them with io-mode=mmap, and composites all
channels into a single tiled window via `compositor ! autovideosink`. Works
under both Wayland and X.

Can be run directly for debugging:
    camera_mmap.py --display 1920x1080 /dev/video0 /dev/video1 ...

Normally it is launched by camera_simple.py, which handles endpoint bring-up
and driver install first.
"""

import argparse
import math
import os
import shlex
import signal
import subprocess
import sys
import time


def log(msg):
    print(f"[pcie-camera-passthrough-simple:mmap] {msg}", flush=True)


def grid_dims(n):
    """Uniform tiling: cols = ceil(sqrt(n)), rows = ceil(n/cols).

    Shared with the dmabuf path so both modes tile identically
    (1->1x1, 4->2x2, 5->3x2).
    """
    cols = int(math.ceil(math.sqrt(n)))
    rows = int(math.ceil(n / cols))
    return cols, rows


def build_pipeline(devs, disp_w, disp_h, sink="autovideosink"):
    """Build a single gst-launch-1.0 compositor pipeline tiling all devices."""
    n = len(devs)
    cols, rows = grid_dims(n)
    tile_w = disp_w // cols
    tile_h = disp_h // rows

    pad_props = []
    for i in range(n):
        col = i % cols
        row = i // cols
        pad_props.append(
            f"sink_{i}::xpos={col * tile_w} sink_{i}::ypos={row * tile_h} "
            f"sink_{i}::width={tile_w} sink_{i}::height={tile_h}")

    parts = [f"gst-launch-1.0 -e compositor name=mix {' '.join(pad_props)} "
             f"! video/x-raw,width={disp_w},height={disp_h} ! {sink}"]

    for i, dev in enumerate(devs):
        # NV12 capture -> convert/scale into the tile. videoscale keeps the
        # pipeline robust to per-channel resolution differences (cams 0-3 vs
        # the 4K cam4).
        parts.append(
            f"v4l2src device={shlex.quote(dev)} io-mode=mmap ! "
            f"videoconvert ! videoscale ! "
            f"video/x-raw,width={tile_w},height={tile_h} ! mix.sink_{i}")

    return " ".join(parts)


def main():
    ap = argparse.ArgumentParser(
        description="pcie-camera-passthrough-simple mmap tiled GStreamer display")
    ap.add_argument("devices", nargs="+", help="V4L2 device nodes to display")
    ap.add_argument("--display", default="1920x1080", metavar="WxH",
                    help="output canvas size (default: 1920x1080)")
    ap.add_argument("--sink", default="autovideosink",
                    help="GStreamer video sink (default: autovideosink)")
    args = ap.parse_args()

    try:
        disp_w, disp_h = (int(x) for x in args.display.lower().split("x"))
    except ValueError:
        log(f"ERROR: bad --display '{args.display}' (expected WxH)")
        sys.exit(2)

    pipeline = build_pipeline(args.devices, disp_w, disp_h, sink=args.sink)
    log(f"pipeline: {pipeline}")

    # Exec gst-launch directly in its own session so we can tear down the whole
    # process group on exit; otherwise an interrupted run leaks gst-launch and
    # keeps the V4L2 devices busy for the next run.
    try:
        proc = subprocess.Popen(shlex.split(pipeline), start_new_session=True)
    except OSError as e:
        log(f"ERROR launching pipeline: {e}")
        sys.exit(1)

    def cleanup(signum=None, frame=None):
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            deadline = time.time() + 5
            while proc.poll() is None and time.time() < deadline:
                time.sleep(0.1)
            if proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        sys.exit(0 if (proc.returncode or 0) == 0 else 1)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    log(f"{len(args.devices)} channel(s) streaming — press Ctrl+C to stop")
    proc.wait()
    if proc.returncode != 0:
        log(f"gst-launch exited with rc={proc.returncode}")
    sys.exit(proc.returncode or 0)


if __name__ == "__main__":
    main()
