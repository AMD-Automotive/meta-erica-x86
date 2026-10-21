# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""Reboot the Versal via the FTDI cable, then rescan PCIe.

Mirrors rave-menu's ``scripts/reconfigure_versal.sh``: drive a POR_B reset over
the FTDI cable (the vendored :mod:`reconfigure_versal`) and then rescan the PCIe
root port so the endpoint re-enumerates. The PCIe check uses this to recover
when the Versal is not on the bus yet.

The reset needs root (it unbinds/rebinds ``ftdi_sio`` and talks to the FTDI via
libusb), so the script is invoked through ``sudo -n``; the app user has NOPASSWD
sudo configured by the device installer.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))

#: The vendored FTDI reset script (run as a subprocess so it gets a clean root
#: context via sudo and its ftdi_sio unbind/rebind doesn't disturb our process).
RECONFIGURE_SCRIPT = os.path.join(_HERE, "reconfigure_versal.py")

#: DMI ``board_name`` -> Versal PCIe root port. The root-port BDF is a fixed
#: property of each motherboard, so this resolves correctly whether or not the
#: Versal endpoint is currently enumerated (an unprogrammed Versal exposes no
#: endpoint on the bus, but the root port it sits behind always exists):
#:   VPR-7P132 (RAVE2-CORE) -> 0000:00:02.3
#:   VPR-7X188 (RAVE2-MAX)  -> 0000:00:03.2
#: Unknown boards fall back to the RAVE2-CORE root port.
_ROOT_PORT_BY_BOARD = {
    "VPR-7P132": "0000:00:02.3",  # RAVE2-CORE
    "VPR-7X188": "0000:00:03.2",  # RAVE2-MAX
}
_FALLBACK_ROOT_PORT = "0000:00:02.3"  # RAVE2-CORE (VPR-7P132)


def resolve_root_port() -> str:
    """Return the Versal PCIe root port BDF for this board (from DMI board_name)."""
    try:
        with open("/sys/class/dmi/id/board_name", "r", errors="ignore") as fh:
            board = fh.read().strip()
    except OSError:
        board = ""
    return _ROOT_PORT_BY_BOARD.get(board, _FALLBACK_ROOT_PORT)


#: Root port the Versal endpoint sits behind (rescanned to re-enumerate).
DEFAULT_ROOT_PORT = resolve_root_port()


class VersalResetError(RuntimeError):
    """Raised when the FTDI reset or the PCIe rescan fails."""


def _run(cmd: list[str], timeout: float) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except FileNotFoundError as exc:
        raise VersalResetError(f"command not found: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise VersalResetError(
            f"timed out after {timeout:.0f}s: {' '.join(cmd)}") from exc
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _maybe_sudo(cmd: list[str], use_sudo: bool) -> list[str]:
    if use_sudo and os.geteuid() != 0:
        return ["sudo", "-n", *cmd]
    return cmd


def reset_versal(*, mode3_high: bool = True, use_sudo: bool = True,
                 timeout: float = 60.0) -> str:
    """Drive a POR_B reset over the FTDI cable. Returns the script transcript."""
    if not os.path.exists(RECONFIGURE_SCRIPT):
        raise VersalResetError(f"reset script missing: {RECONFIGURE_SCRIPT}")
    mode = "--mode3-high" if mode3_high else "--mode3-low"
    cmd = _maybe_sudo([sys.executable, RECONFIGURE_SCRIPT, mode], use_sudo)
    rc, out = _run(cmd, timeout)
    if rc != 0:
        raise VersalResetError(f"reset failed (rc={rc}):\n{out.strip()}")
    return out


def rescan_pci(root_port: str = DEFAULT_ROOT_PORT, *, use_sudo: bool = True,
               timeout: float = 15.0) -> str:
    """Trigger a rescan of the PCIe root port so the endpoint re-enumerates."""
    rescan = f"/sys/bus/pci/devices/{root_port}/rescan"
    cmd = _maybe_sudo(["sh", "-c", f"echo 1 > {rescan}"], use_sudo)
    rc, out = _run(cmd, timeout)
    if rc != 0:
        raise VersalResetError(
            f"rescan of {root_port} failed (rc={rc}):\n{out.strip()}")
    return out


def reset_and_rescan(*, mode3_high: bool = True, root_port: str = DEFAULT_ROOT_PORT,
                     settle_s: float = 1.0, use_sudo: bool = True) -> str:
    """Reset the Versal and rescan PCIe. Returns a combined transcript.

    The caller is responsible for polling for the endpoint afterwards (the Versal
    takes time to boot far enough to re-establish the PCIe link).
    """
    log = reset_versal(mode3_high=mode3_high, use_sudo=use_sudo)
    if settle_s > 0:
        time.sleep(settle_s)
    log += rescan_pci(root_port, use_sudo=use_sudo)
    return log
