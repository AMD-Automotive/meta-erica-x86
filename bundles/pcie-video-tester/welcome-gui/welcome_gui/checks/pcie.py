# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""PCIe presence check.

Verifies that a given ``vendor:device`` (default ``10ee:9038`` — the Versal
PCIe endpoint) is enumerated on the bus by scanning sysfs directly. We read
sysfs rather than shelling out to ``lspci`` so the check has no external
dependency and works on a minimal rootfs.

If the endpoint is missing, the check can recover it the same way the rave-menu
app does: drive a POR_B reset over the FTDI cable and rescan the PCIe root port,
then wait for the Versal to re-enumerate (see :mod:`welcome_gui.versal.reset`).
"""

from __future__ import annotations

import glob
import os
import time

from .base import Check, CheckResult
from ..versal.reset import (
    DEFAULT_ROOT_PORT,
    VersalResetError,
    rescan_pci,
    reset_and_rescan,
)

_REMEDIATION = (
    "The Versal PCIe endpoint was not found. The Versal side is most likely not "
    "running the expected image (or the link did not train).\n\n"
    "Reboot the system into Buddy and re-install this application / re-program "
    "the Versal image, then power-cycle and try again."
)


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="ascii") as fh:
            return fh.read().strip().lower()
    except OSError:
        return ""


def find_pci_device(vendor: str, device: str) -> str | None:
    """Return the PCI address (e.g. ``0000:01:00.0``) of the first matching
    device, or ``None`` if not present.

    ``vendor`` / ``device`` are bare 4-hex-digit strings without the ``0x``
    prefix (e.g. ``"10ee"``, ``"9038"``).
    """
    want_v = f"0x{vendor.lower()}"
    want_d = f"0x{device.lower()}"
    for dev_dir in sorted(glob.glob("/sys/bus/pci/devices/*")):
        if _read(os.path.join(dev_dir, "vendor")) == want_v and \
           _read(os.path.join(dev_dir, "device")) == want_d:
            return os.path.basename(dev_dir)
    return None


class PcieCheck(Check):
    key = "pcie"
    title = "PCIe endpoint"
    critical = True

    def run(self) -> CheckResult:
        target = str(self.config.get("vendor_device", "10ee:9038")).lower()
        try:
            vendor, device = target.split(":", 1)
        except ValueError:
            return self.fail(f"Invalid vendor_device {target!r}", remediation=_REMEDIATION)

        addr = find_pci_device(vendor, device)
        if addr:
            return self.ok(f"Found {target} at {addr}")

        # Endpoint missing. Optionally reboot the Versal over the FTDI cable and
        # rescan the bus, then wait for it to re-enumerate.
        if not bool(self.config.get("reboot_on_missing", True)):
            return self.fail(
                f"PCIe device {target} not found",
                detail="Scanned /sys/bus/pci/devices for the Versal endpoint.",
                remediation=self.config.get("remediation", _REMEDIATION),
            )

        return self._recover(vendor, device, target)

    def _recover(self, vendor: str, device: str, target: str) -> CheckResult:
        """Reboot the Versal via FTDI + rescan, then poll for the endpoint."""
        attempts = max(1, int(self.config.get("reboot_attempts", 1)))
        root_port = str(self.config.get("rescan_root_port", DEFAULT_ROOT_PORT))
        mode3_high = str(self.config.get("reset_mode", "mode3-high")) != "mode3-low"
        enumerate_timeout = float(self.config.get("enumerate_timeout_s", 45))
        rescan_interval = float(self.config.get("rescan_interval_s", 3))
        remediation = self.config.get("remediation", _REMEDIATION)

        transcript: list[str] = [
            f"PCIe {target} not present — rebooting the Versal over FTDI and rescanning."
        ]
        for attempt in range(1, attempts + 1):
            transcript.append(f"--- Versal reboot attempt {attempt}/{attempts} ---")
            try:
                transcript.append(
                    reset_and_rescan(mode3_high=mode3_high, root_port=root_port))
            except VersalResetError as exc:
                transcript.append(str(exc))
                return self.fail(
                    "Versal reboot failed",
                    detail="\n".join(transcript),
                    remediation=remediation,
                )

            # The Versal needs time to boot far enough to train the PCIe link;
            # poll, re-issuing a rescan periodically until it appears.
            deadline = time.monotonic() + enumerate_timeout
            next_rescan = time.monotonic() + rescan_interval
            while time.monotonic() < deadline:
                addr = find_pci_device(vendor, device)
                if addr:
                    transcript.append(f"Endpoint enumerated at {addr}.")
                    return self.ok(
                        f"Found {target} at {addr} after Versal reboot",
                        detail="\n".join(transcript),
                    )
                time.sleep(0.5)
                if time.monotonic() >= next_rescan:
                    try:
                        rescan_pci(root_port)
                    except VersalResetError:
                        pass
                    next_rescan = time.monotonic() + rescan_interval

        transcript.append(
            f"Endpoint still absent after {attempts} reboot(s) "
            f"and {enumerate_timeout:.0f}s of rescanning.")
        return self.fail(
            f"PCIe device {target} not found after Versal reboot",
            detail="\n".join(transcript),
            remediation=remediation,
        )
