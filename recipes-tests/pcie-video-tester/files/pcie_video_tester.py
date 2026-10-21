#!/usr/bin/env python3
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# Contributors: BW, KC
# Maintainer: BW
# SPDX-License-Identifier: MIT
"""
PCIe Video Tester — visual streaming sanity check for the MDB5 video bridge.

Runs directly on the x86 root complex host.  Loads the mdb5_v4l2 driver,
discovers video devices, and launches GStreamer pipelines to display live
streams in tiled Wayland windows.

By default, assumes ep_proxy is already running on the endpoint.  Pass
--endpoint_host to have this script SSH into the endpoint, create vivid
capture sources, and start ep_proxy automatically.

Usage:
    pcie_video_tester.py [--endpoint_host root@IP] [--channels N]
"""

import argparse
import os
import re
import signal
import shlex
import subprocess
import sys
import time

SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=accept-new"]

DRIVER_DEPS = ["videodev", "videobuf2-common", "videobuf2-v4l2",
               "videobuf2-dma-sg", "dw-edma"]

SETUP_SCRIPT = "ep_vivid_setup.sh"

# The endpoint setup script now ships pre-installed on the Versal rootfs (via
# meta-erica-edf, which installs it to ${bindir}) rather than being copied from
# this host. We look for it on PATH and at its known install location, in order;
# override with --endpoint-script.
EP_SCRIPT_CANDIDATES = [
    "/usr/bin/ep_vivid_setup.sh",
]

SUDO = "sudo -n" if os.geteuid() != 0 else ""


def log(msg):
    print(f"[pcie_video_tester] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Remote SSH helper (only used when --endpoint_host is given)
# ---------------------------------------------------------------------------
class Remote:
    def __init__(self, target):
        self.target = target

    def run(self, cmd, timeout=120):
        try:
            return subprocess.run(["ssh", *SSH_OPTS, self.target, cmd],
                                  timeout=timeout, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(
                cmd, 124, stdout="", stderr=f"timeout after {timeout}s")

    def ok(self, cmd, timeout=60):
        return self.run(cmd, timeout=timeout).returncode == 0

    def put(self, local, remote, timeout=60):
        return subprocess.run(["scp", *SSH_OPTS, local,
                               f"{self.target}:{remote}"],
                              timeout=timeout, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True).returncode == 0


# ---------------------------------------------------------------------------
# Local command helpers
# ---------------------------------------------------------------------------
def local_run(cmd, timeout=60):
    return subprocess.run(cmd, shell=True, timeout=timeout,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True)


def local_ok(cmd, timeout=60):
    return local_run(cmd, timeout=timeout).returncode == 0


# ---------------------------------------------------------------------------
# Endpoint setup (optional)
# ---------------------------------------------------------------------------
def setup_endpoint(ep, channels, script=None):
    """Run ep_vivid_setup.sh in place on the endpoint.

    The script ships pre-installed on the Versal rootfs (meta-erica-edf), so we
    resolve and execute it there rather than copying it from this host.

    Returns the systemd unit name on success, or None on failure.
    """
    log(f"checking endpoint {ep.target} reachability")
    if not ep.ok("true", timeout=15):
        log(f"ERROR: endpoint {ep.target} not reachable")
        return None

    remote_script = resolve_endpoint_script(ep, script)
    if not remote_script:
        cands = script or ", ".join(EP_SCRIPT_CANDIDATES)
        log(f"ERROR: {SETUP_SCRIPT} not found on endpoint (looked in: {cands}); "
            "is the Versal rootfs current?")
        return None

    log(f"running {remote_script} on endpoint ({channels} channels)")
    r = ep.run(f"bash {shlex.quote(remote_script)} {channels}", timeout=30)
    if r.returncode != 0:
        log(f"ERROR: endpoint setup failed:\n{r.stderr.strip()}")
        return None

    # The script prints the systemd unit name on its last stdout line.
    lines = r.stdout.strip().splitlines()
    unit = lines[-1].strip() if lines else None
    if not unit:
        log("WARNING: could not parse systemd unit from endpoint setup output")
        return None

    log(f"endpoint ep_proxy running as: {unit}")
    return unit


def resolve_endpoint_script(ep, script=None):
    """Return the path to ep_vivid_setup.sh on the endpoint, or None.

    Honours an explicit --endpoint-script path, then falls back to ``command
    -v`` (PATH) and a list of known install locations.
    """
    candidates = [script] if script else [SETUP_SCRIPT, *EP_SCRIPT_CANDIDATES]
    # `command -v` resolves a bare name on PATH; an absolute path is tested with
    # `test -x`. Emit the first hit on stdout.
    probe = " || ".join(
        f"command -v {shlex.quote(c)} 2>/dev/null" if "/" not in c
        else f"{{ test -x {shlex.quote(c)} && echo {shlex.quote(c)}; }}"
        for c in candidates
    )
    r = ep.run(probe, timeout=15)
    if r.returncode != 0:
        return None
    out = r.stdout.strip().splitlines()
    return out[-1].strip() if out else None


def teardown_endpoint(ep, unit):
    """Stop ep_proxy and unload vivid on the endpoint."""
    log("tearing down endpoint")
    if unit:
        ep.run(f"systemctl stop {shlex.quote(unit)} 2>/dev/null", timeout=15)
        ep.run(f"systemctl reset-failed {shlex.quote(unit)} 2>/dev/null",
               timeout=15)
    ep.run("pkill -x ep_proxy 2>/dev/null", timeout=15)
    time.sleep(0.5)
    for _ in range(5):
        if not ep.ok("lsmod | grep -q '^vivid'", timeout=15):
            break
        ep.run("rmmod vivid 2>/dev/null", timeout=15)
        time.sleep(0.5)
    # The setup script writes this channel map at runtime; the script itself
    # lives on the rootfs now, so only the generated file is cleaned up.
    ep.run("rm -f /tmp/mdb5_vivid_channels.json", timeout=15)
    log("endpoint teardown complete")


# ---------------------------------------------------------------------------
# Host driver loading
# ---------------------------------------------------------------------------
def unload_driver():
    """Unload mdb5_v4l2 so a subsequent load re-probes the endpoint.

    The driver binds its channels at probe time by reading the layout from the
    endpoint proxy, so a module that came up before the proxy was ready stays
    loaded but bound to zero channels; unloading lets it re-probe cleanly."""
    if local_ok("lsmod | grep -q '^mdb5_v4l2'"):
        local_run(f"{SUDO} rmmod mdb5_v4l2")
        time.sleep(1)


def load_driver(host_ko=None, force=False):
    """Load mdb5_v4l2 and its dependencies. Uses insmod with a .ko path when
    host_ko is given, otherwise modprobe. When force is set, an already-loaded
    module is unloaded first so it re-probes the (now ready) endpoint proxy.
    Returns True on success."""
    if local_ok("lsmod | grep -q '^mdb5_v4l2'"):
        if not force:
            log("mdb5_v4l2 already loaded")
            return True
        log("reloading mdb5_v4l2 to re-probe the endpoint")
        unload_driver()

    for dep in DRIVER_DEPS:
        local_run(f"{SUDO} modprobe {shlex.quote(dep)} 2>/dev/null")

    if host_ko:
        r = local_run(f"{SUDO} insmod {shlex.quote(host_ko)}")
        label = host_ko
    else:
        r = local_run(f"{SUDO} modprobe mdb5_v4l2")
        label = "mdb5_v4l2"
    if r.returncode != 0:
        log(f"ERROR: loading {label} failed: {r.stderr.strip()}")
        return False

    log(f"mdb5_v4l2 loaded ({label})")
    time.sleep(2)
    return True


# ---------------------------------------------------------------------------
# Device discovery
# ---------------------------------------------------------------------------
def discover_mdb5_devices():
    """Find /dev/video* nodes backed by the mdb5_v4l2 driver."""
    r = local_run("ls -1 /dev/video* 2>/dev/null")
    if r.returncode != 0 or not r.stdout.strip():
        return []

    mdb5_devs = []
    for dev in sorted(r.stdout.split()):
        d = local_run(f"v4l2-ctl -d {shlex.quote(dev)} -D")
        if d.returncode == 0 and "mdb5" in d.stdout.lower():
            mdb5_devs.append(dev)
    return mdb5_devs


def print_device_info(devs):
    """Print format details for each discovered device."""
    for dev in devs:
        r = local_run(f"v4l2-ctl -d {shlex.quote(dev)} --get-fmt-video")
        fmt_lines = ""
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                s = line.strip()
                if any(k in s for k in ("Width/Height", "Pixel Format",
                                        "Bytes per Line", "Size Image")):
                    fmt_lines += f"  {s}\n"
        log(f"  {dev}:")
        if fmt_lines:
            for fl in fmt_lines.strip().splitlines():
                log(f"    {fl.strip()}")


# ---------------------------------------------------------------------------
# GStreamer display
# ---------------------------------------------------------------------------
def build_composite_pipeline(devs, sink="autovideosink",
                             tile_w=960, tile_h=540):
    """Build a single gst-launch-1.0 pipeline that composites all channels
    into one 2xN tiled window using the compositor element.

    Layout is a 2-column grid:
        sink_0 | sink_1
        sink_2 | sink_3
        ...
    """
    n = len(devs)
    cols = 2 if n > 1 else 1

    # compositor sink pad positions
    pad_props = []
    for i in range(n):
        col = i % cols
        row = i // cols
        pad_props.append(
            f"sink_{i}::xpos={col * tile_w} sink_{i}::ypos={row * tile_h}")

    parts = [f"gst-launch-1.0 compositor name=mix {' '.join(pad_props)} "
             f"! {sink}"]

    for i, dev in enumerate(devs):
        parts.append(
            f"v4l2src device={shlex.quote(dev)} io-mode=mmap ! videoconvert ! videoscale ! "
            f"video/x-raw,width={tile_w},height={tile_h} ! mix.sink_{i}")

    return " ".join(parts)


def launch_gstreamer(devs, sink="autovideosink"):
    """Launch a single composited GStreamer pipeline. Returns list of Popen
    (one element for API compatibility with stop_gstreamer)."""
    pipeline = build_composite_pipeline(devs, sink=sink)
    log(f"  pipeline: {pipeline}")
    # Exec gst-launch directly (no shell wrapper) so terminate()/kill() act on
    # gst-launch itself rather than an intermediate /bin/sh. The pipeline has no
    # shell metacharacters ('!' is a gst-launch argument), so shlex.split is
    # safe. start_new_session puts it in its own process group, letting us tear
    # down the whole tree; otherwise an interrupted run leaks a gst-launch that
    # keeps the V4L2 devices open and makes the next run fail "device busy".
    try:
        p = subprocess.Popen(shlex.split(pipeline),
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.PIPE, text=True,
                             start_new_session=True)
        return [p]
    except OSError as e:
        log(f"  ERROR launching pipeline: {e}")
        return []


def stop_gstreamer(procs):
    """Terminate all GStreamer child processes (and their process groups)."""
    def _signal(p, sig):
        try:
            os.killpg(os.getpgid(p.pid), sig)
        except (ProcessLookupError, PermissionError):
            pass

    for p in procs:
        if p.poll() is None:
            _signal(p, signal.SIGTERM)
    deadline = time.time() + 5
    for p in procs:
        remaining = max(0, deadline - time.time())
        try:
            p.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            _signal(p, signal.SIGKILL)
            p.wait()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="PCIe Video Tester — visual streaming sanity check "
                    "for the MDB5 video bridge",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
EXAMPLES
========
Run with ep_proxy already active on the endpoint:
    python3 pcie_video_tester.py

Kick off vivid + ep_proxy on the endpoint automatically:
    python3 pcie_video_tester.py --endpoint_host root@192.168.1.40

Show only 2 channels:
    python3 pcie_video_tester.py --channels 2
""")
    ap.add_argument("--endpoint_host", default=None, metavar="USER@IP",
                    help="SSH into this endpoint to create vivid sources "
                         "and start ep_proxy (default: assume ep_proxy is "
                         "already running)")
    ap.add_argument("--endpoint-script", default=None, metavar="PATH",
                    help="path to ep_vivid_setup.sh on the endpoint (default: "
                         "resolve on PATH / known install locations). The "
                         "script ships on the Versal rootfs.")
    ap.add_argument("--channels", type=int, default=4,
                    help="number of video channels to display (default: 4)")
    ap.add_argument("--host-ko", default=None, metavar="PATH",
                    help="path to mdb5_v4l2.ko for insmod (default: use "
                         "modprobe). Example: /home/amd/mdb5_v4l2/mdb5_v4l2.ko")
    ap.add_argument("--sink", default="autovideosink",
                    help="GStreamer video sink element (default: autovideosink). "
                         "Try waylandsink, ximagesink, or fakesink")
    args = ap.parse_args()

    ep = None
    ep_unit = None
    gst_procs = []

    def cleanup(signum=None, frame=None):
        if signum:
            log(f"caught signal {signum}, shutting down")
        stop_gstreamer(gst_procs)
        if ep and ep_unit:
            teardown_endpoint(ep, ep_unit)
        log("done")
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    # 1. Endpoint setup (optional)
    if args.endpoint_host:
        ep = Remote(args.endpoint_host)
        ep_unit = setup_endpoint(ep, args.channels, args.endpoint_script)
        if not ep_unit:
            log("endpoint setup failed; exiting")
            sys.exit(1)
        time.sleep(2)

    # 2. Load driver
    if not load_driver(args.host_ko):
        cleanup()
        sys.exit(1)

    # 3. Discover devices
    devs = discover_mdb5_devices()
    if not devs:
        # The module may have probed before the endpoint proxy was up, leaving
        # it loaded but bound to zero channels. Now that the endpoint is
        # running, reload it once so it re-reads the layout, then re-discover.
        log("no mdb5 devices found — reloading driver now that endpoint is up")
        if load_driver(args.host_ko, force=True):
            devs = discover_mdb5_devices()
    if not devs:
        log("ERROR: no mdb5 video devices found")
        cleanup()
        sys.exit(1)

    log(f"discovered {len(devs)} mdb5 device(s):")
    print_device_info(devs)

    if len(devs) < args.channels:
        log(f"WARNING: expected {args.channels} channels but found {len(devs)}")
    devs = devs[:args.channels]

    # 4. Launch GStreamer
    log(f"launching {len(devs)} GStreamer pipeline(s) (sink={args.sink})")
    gst_procs = launch_gstreamer(devs, sink=args.sink)
    if not gst_procs:
        log("ERROR: no GStreamer pipelines started")
        cleanup()
        sys.exit(1)

    log(f"{len(gst_procs)} stream(s) active — press Ctrl+C to stop")

    # 5. Wait for processes or signal
    try:
        while True:
            alive = [p for p in gst_procs if p.poll() is None]
            if not alive:
                log("all GStreamer pipelines exited")
                for i, p in enumerate(gst_procs):
                    err = (p.stderr.read() or "") if p.stderr else ""
                    if p.returncode != 0:
                        detail = err.strip()[-200:] if err else "no stderr"
                        log(f"  ch{i} exited with rc={p.returncode}: "
                            f"{detail}")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    cleanup()


if __name__ == "__main__":
    main()
