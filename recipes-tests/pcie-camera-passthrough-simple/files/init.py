#!/usr/bin/env python3
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# Contributors: BW
# Maintainer: BW
# SPDX-License-Identifier: MIT
"""
pcie-camera-passthrough-simple setup module.

Shared setup used by camera_simple.py before a demo runs:

  1. setup_endpoint()  - Connect to the endpoint serial console and run
                         init_cameras.sh so the Versal side brings up the
                         requested camera channels at the resolution matching
                         the host display mode.
  2. install_driver()  - Run install_mdb5_drv.sh locally to build + load the
                         mdb5_v4l2 host driver.
  3. ensure_dmabuf_built() - Compile the camera_dmabuf C helper on demand if the
                         binary is not already present (host-build path).
  4. discover_devices() - Resolve the camera config (0/1/2) to the expected
                         /dev/videoN nodes and confirm they are backed by
                         mdb5_v4l2.

The mdb5_v4l2 driver format is static (set by the endpoint), so the host never
issues VIDIOC_S_FMT; init_cameras.sh is what selects the per-mode resolution.
"""

import os
import pty
import re
import select
import shlex
import subprocess
import time

# tio serial console connection parameters.
TIO_BAUD = 115200
TIO_FLOW = "none"
# Seconds to let tio open the port and connect before we send the command.
TIO_SETTLE = 0.4

# Sentinel used to extract the exit code of a remote command over serial.
# Chosen to be unlikely to appear in normal command output. Only "$?" expanded
# to digits matches; the echoed input line (serial consoles echo what we type)
# carries the literal "__CAMRC__$?", so it never looks like a result.
_RC_SENTINEL = "__CAMRC__"
_RC_SEARCH = re.compile(re.escape(_RC_SENTINEL) + r"(\d+)")
# tio prints its own status lines prefixed with a timestamp, e.g.
# "[16:50:10.020] Connected"; these are filtered out of captured output.
_TIO_BANNER = re.compile(r"^\[\d{2}:\d{2}:\d{2}")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# init_cameras.sh ships on the endpoint rootfs (like ep_vivid_setup.sh); we
# resolve it on PATH first, then fall back to its known install location.
ENDPOINT_SCRIPT = "init_cameras.sh"
ENDPOINT_SCRIPT_CANDIDATES = ["/usr/bin/init_cameras.sh"]

# init_cameras.sh must return 0 within this many seconds or the run fails.
ENDPOINT_INIT_TIMEOUT = 60

# Endpoint-side log the script's stdout/stderr is redirected into. The redirect
# is essential: it divorces any long-lived child spawned during camera bring-up
# (an ISP/gst helper, or ep_proxy itself) from the shell's stdio, so the
# command returns promptly even though ep_proxy keeps running.
ENDPOINT_INIT_LOG = "/tmp/init_cameras_last.log"

# install_mdb5_drv.sh ships alongside this module in the recipe and runs locally.
DRIVER_INSTALL_SCRIPT = "install_mdb5_drv.sh"

DMABUF_BIN = "camera_dmabuf"
DMABUF_SRC = "camera_dmabuf.c"

# Camera-config -> number of channels. 0=1ch, 1=4ch, 2=5ch.
CONFIG_CHANNELS = {0: 1, 1: 4, 2: 5}


def log(msg):
    print(f"[pcie-camera-passthrough-simple:init] {msg}", flush=True)


def here():
    """Directory this module (and its sibling scripts/binary) lives in."""
    return os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Local command helpers
# ---------------------------------------------------------------------------
def local_run(cmd, timeout=120):
    return subprocess.run(cmd, shell=True, timeout=timeout,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True)


def local_ok(cmd, timeout=120):
    return local_run(cmd, timeout=timeout).returncode == 0


# ---------------------------------------------------------------------------
# Remote serial console helper (via tio)
# ---------------------------------------------------------------------------
class Remote:
    """Drive the endpoint's serial console through tio.

    tio only drives the port interactively when it is attached to a terminal,
    so each run() call gives it a pseudo-terminal (pty) on stdin/stdout/stderr,
    exactly as if a person had run `tio ... /dev/ttyUSB1`. We write the command
    plus an exit-code sentinel to the pty, read the reflected serial output
    until the sentinel appears (or we time out), then terminate tio.

    Protocol
    --------
    We send:
        <cmd> ; echo __CAMRC__$?\n
    and scan the output for "__CAMRC__<digits>", taking the digits as the exit
    code. The serial console echoes what we type, so the echoed command line
    (carrying the literal, unexpanded "__CAMRC__$?") also comes back; it never
    matches the digit sentinel and is filtered out of captured stdout along
    with tio's own banner lines and ANSI escapes.

    Assumes the console is already at an interactive root shell (auto-login);
    it does not handle a login prompt.
    """

    def __init__(self, port, baud=TIO_BAUD, flow=TIO_FLOW):
        self.port   = port
        self.baud   = baud
        self.flow   = flow
        self.target = port  # used in log messages by setup_endpoint

    def run(self, cmd, timeout=120):
        sentinel_cmd = f"{cmd} ; echo {_RC_SENTINEL}$?\n"
        master, slave = pty.openpty()
        try:
            proc = subprocess.Popen(
                ["tio", "-b", str(self.baud), "-f", self.flow, self.port],
                stdin=slave, stdout=slave, stderr=slave, close_fds=True)
        except FileNotFoundError:
            os.close(master)
            os.close(slave)
            return subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="tio not found in PATH")
        os.close(slave)  # tio owns its end now

        buf = b""
        rc = None
        deadline = time.monotonic() + timeout
        try:
            time.sleep(TIO_SETTLE)
            os.write(master, sentinel_cmd.encode())
            while time.monotonic() < deadline:
                r, _, _ = select.select([master], [], [],
                                        max(0.0, deadline - time.monotonic()))
                if not r:
                    break
                try:
                    data = os.read(master, 4096)
                except OSError:
                    break  # tio exited -> pty master EOF (EIO)
                if not data:
                    break
                buf += data
                m = _RC_SEARCH.search(buf.decode(errors="replace"))
                if m:
                    rc = int(m.group(1))
                    break
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            os.close(master)

        text = buf.decode(errors="replace")
        if rc is None:
            return subprocess.CompletedProcess(
                cmd, 124, stdout=self._clean(text),
                stderr=f"no result within {timeout}s (console unresponsive?)")
        # Keep only what preceded the sentinel as command output.
        pre = text[:_RC_SEARCH.search(text).start()]
        return subprocess.CompletedProcess(
            cmd, rc, stdout=self._clean(pre), stderr="")

    @staticmethod
    def _clean(text):
        """Strip ANSI, tio banners, and the echoed command from serial output."""
        out = []
        for line in _ANSI.sub("", text).splitlines():
            s = line.rstrip("\r")
            if not s.strip():
                continue
            if _TIO_BANNER.match(s):
                continue
            if f"{_RC_SENTINEL}$?" in s:  # echoed input (with prompt prefix)
                continue
            out.append(s)
        return "\n".join(out)

    def ok(self, cmd, timeout=60):
        return self.run(cmd, timeout=timeout).returncode == 0


# ---------------------------------------------------------------------------
# 1. Endpoint camera bring-up
# ---------------------------------------------------------------------------
def resolve_endpoint_script(ep, script=None):
    """Return the path to init_cameras.sh on the endpoint, or None."""
    candidates = [script] if script else [ENDPOINT_SCRIPT, *ENDPOINT_SCRIPT_CANDIDATES]
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


def setup_endpoint(ep, mode, config, script=None):
    """Run init_cameras.sh on the endpoint for the given mode/config.

    Invocation contract:  init_cameras.sh <mmap|dmabuf> <0|1|2>
    The endpoint always brings up all 5 channels; the mode selects the
    resolution (1920x1536 for mmap, 1792x1536 for dmabuf on cams 0-3;
    3840x2160 on cam4). The config is also passed through. The host app opens
    just the subset of channels the config variant needs. Returns True on
    success.
    """
    log(f"checking endpoint console ({ep.target})")
    if not ep.ok("true", timeout=15):
        log(f"ERROR: no response from endpoint console ({ep.target})")
        return False

    remote_script = resolve_endpoint_script(ep, script)
    if not remote_script:
        cands = script or ", ".join(ENDPOINT_SCRIPT_CANDIDATES)
        log(f"ERROR: {ENDPOINT_SCRIPT} not found on endpoint (looked in: {cands})")
        return False

    log(f"running {remote_script} {mode} {config} on endpoint "
        f"(log: {ENDPOINT_INIT_LOG})")
    # Redirect the script's stdio so ep_proxy (launched inside the script in a
    # detached tmux session) does not keep the shell's stdio open and delay the
    # sentinel echo that signals command completion.
    r = ep.run(f"bash {shlex.quote(remote_script)} {shlex.quote(mode)} "
               f"{shlex.quote(str(config))} "
               f"> {shlex.quote(ENDPOINT_INIT_LOG)} 2>&1 < /dev/null",
               timeout=ENDPOINT_INIT_TIMEOUT)
    if r.returncode != 0:
        log(f"ERROR: endpoint camera init failed (rc={r.returncode})")
        if r.stderr.strip():
            log(f"  serial: {r.stderr.strip()}")
        # Pull the endpoint-side log back for context (stdout went there).
        lr = ep.run(f"tail -20 {shlex.quote(ENDPOINT_INIT_LOG)}", timeout=15)
        for line in lr.stdout.strip().splitlines():
            log(f"  endpoint: {line}")
        return False

    log("endpoint cameras initialized")
    return True


# ---------------------------------------------------------------------------
# 2. Host driver install
# ---------------------------------------------------------------------------
def install_driver(script=None):
    """Build + load mdb5_v4l2 via install_mdb5_drv.sh (runs locally).

    The driver latches the endpoint's channel count from BRAM at probe time, so
    a module left loaded from a previous config would expose the wrong number of
    /dev/videoN nodes. If mdb5_v4l2 is already loaded, force-remove it first so
    the install script reloads it fresh against the current endpoint config; if
    the removal fails, error out rather than proceed with a stale driver.
    """
    if local_ok("lsmod | grep -q '^mdb5_v4l2'"):
        sudo = "" if os.geteuid() == 0 else "sudo -n"
        log("mdb5_v4l2 already loaded; force-removing so it re-reads the "
            "endpoint channel count")
        local_run(f"{sudo} rmmod -f mdb5_v4l2".strip())
        if local_ok("lsmod | grep -q '^mdb5_v4l2'"):
            log("ERROR: failed to remove existing mdb5_v4l2 (rmmod -f)")
            return False

    path = script or os.path.join(here(), DRIVER_INSTALL_SCRIPT)
    if not os.path.exists(path):
        log(f"ERROR: driver install script not found: {path}")
        return False

    # Run the script as the normal user; it escalates internally via `sudo
    # modprobe`/`sudo rmmod` (the commands granted NOPASSWD). Wrapping the whole
    # script in sudo would need NOPASSWD for `bash`, which is not granted.
    log(f"running {path}")
    r = local_run(f"bash {shlex.quote(path)}", timeout=600)
    if r.stdout.strip():
        for line in r.stdout.strip().splitlines():
            log(f"  {line}")
    if r.returncode != 0:
        log(f"ERROR: driver install failed: {r.stderr.strip()}")
        return False

    if not local_ok("lsmod | grep -q '^mdb5_v4l2'"):
        log("ERROR: mdb5_v4l2 not loaded after install script")
        return False

    log("mdb5_v4l2 loaded")
    time.sleep(2)
    return True


# ---------------------------------------------------------------------------
# 3. Build the dmabuf C helper on demand
# ---------------------------------------------------------------------------
def dmabuf_bin_path():
    return os.path.join(here(), DMABUF_BIN)


def ensure_dmabuf_built():
    """Ensure the camera_dmabuf binary exists; build via make if missing."""
    binp = dmabuf_bin_path()
    src = os.path.join(here(), DMABUF_SRC)
    if os.path.exists(binp) and (not os.path.exists(src) or
                                 os.path.getmtime(binp) >= os.path.getmtime(src)):
        return binp

    log(f"building {DMABUF_BIN}")
    r = local_run(f"make -C {shlex.quote(here())} {DMABUF_BIN}", timeout=120)
    if r.returncode != 0:
        log(f"ERROR: build failed: {r.stderr.strip() or r.stdout.strip()}")
        return None
    log(f"built {binp}")
    return binp


# ---------------------------------------------------------------------------
# 4. Device discovery
# ---------------------------------------------------------------------------
def expected_devices(config):
    """Return the list of /dev/videoN nodes for a camera config."""
    n = CONFIG_CHANNELS.get(config)
    if n is None:
        raise ValueError(f"invalid config {config} (expected 0, 1, or 2)")
    return [f"/dev/video{i}" for i in range(n)]


def is_mdb5(dev):
    r = local_run(f"v4l2-ctl -d {shlex.quote(dev)} -D", timeout=15)
    return r.returncode == 0 and "mdb5" in r.stdout.lower()


def device_geometry(dev):
    """Return (width, height) for a device via v4l2-ctl, or None."""
    r = local_run(f"v4l2-ctl -d {shlex.quote(dev)} --get-fmt-video", timeout=15)
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        s = line.strip()
        if "Width/Height" in s:
            # "Width/Height      : 1920/1536"
            try:
                wh = s.split(":", 1)[1].strip().split("/")
                return int(wh[0]), int(wh[1])
            except (IndexError, ValueError):
                return None
    return None


# ---------------------------------------------------------------------------
# 5. Zero-copy validation (dmabuf mode)
# ---------------------------------------------------------------------------
def validate_zerocopy(pid, n_cameras, num_bufs=4):
    """Confirm camera_dmabuf is using genuine dmabuf zero-copy.

    In the dmabuf path the process holds exactly n_cameras * num_bufs
    /dmabuf: file descriptors — one exported GPU dumb buffer per V4L2 slot per
    camera. A fallback to mmap would show /dev/videoN handles instead.
    Prints a PASS/FAIL line and returns True on pass.
    """
    r = local_run(f"ls -la /proc/{pid}/fd 2>/dev/null", timeout=10)
    if r.returncode != 0 or not r.stdout.strip():
        log("ZEROCOPY CHECK: FAIL — could not read fds "
            f"(process {pid} not running?)")
        return False

    lines = r.stdout.splitlines()
    dmabuf_fds   = [l for l in lines if "/dmabuf:" in l]
    video_mmap   = [l for l in lines if "/dev/video" in l]
    drm_card     = [l for l in lines if "/dev/dri/card" in l]

    expected = n_cameras * num_bufs
    ok = len(dmabuf_fds) == expected and len(drm_card) >= 1

    log("--- zero-copy validation ---")
    log(f"  /dmabuf: fds  : {len(dmabuf_fds):2d}  (expected {expected})")
    log(f"  /dev/dri/card : {len(drm_card):2d}  (DRM overlay open)")
    log(f"  /dev/video mmap: {len(video_mmap):2d}  (expected 0 in dmabuf mode)")
    if ok:
        log("ZEROCOPY CHECK: PASS — PCIe DMA -> GPU dumb buffer -> DRM overlay, "
            "no host copies")
    else:
        if len(dmabuf_fds) != expected:
            log(f"ZEROCOPY CHECK: FAIL — got {len(dmabuf_fds)} dmabuf fds, "
                f"expected {expected} ({n_cameras} camera(s) x {num_bufs} bufs)")
        if not drm_card:
            log("ZEROCOPY CHECK: FAIL — no DRM device open")
    log("----------------------------")
    return ok


def discover_devices(config, require_mdb5=True):
    """Resolve config to device nodes, verifying each exists and is mdb5.

    Returns the list of usable device paths (may be shorter than expected if
    some nodes are missing).
    """
    devs = []
    for dev in expected_devices(config):
        if not os.path.exists(dev):
            log(f"WARNING: {dev} not present")
            continue
        if require_mdb5 and not is_mdb5(dev):
            log(f"WARNING: {dev} is not an mdb5_v4l2 device; skipping")
            continue
        geo = device_geometry(dev)
        geo_s = f"{geo[0]}x{geo[1]}" if geo else "unknown"
        log(f"  {dev}: {geo_s}")
        devs.append(dev)
    return devs
