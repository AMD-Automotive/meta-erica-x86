# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""Versal serial-console check.

Talks to the Versal Linux console over the serial port (default ``/dev/ttyUSB1``)
and verifies two things, surfacing each as a labelled field on the welcome
screen:

* **OSPI Configuration** — ``/proc/device-tree/model`` must equal the expected
  board model (``RAVE2 ECP - AMD Versal Gen 2``). This identifies the OSPI
  (boot.bin) image.
* **UFS filesystem** — ``/etc/buildinfo`` ``IMAGE_BASENAME`` must equal the
  expected rootfs image (``erica-appdev-image``). This identifies the UFS
  rootfs.

It also extracts the interesting ``/etc/buildinfo`` fields (distro version,
build datetime, and the branch + short SHA of the configured layers such as
``meta-erica-edf`` and ``meta-erica-h-hardware``) and reports them as info.
"""

from __future__ import annotations

from .base import Check, CheckResult
from .serial_console import SerialConsole, SerialConsoleError

_REMEDIATION = (
    "Could not confirm the expected Versal image over the serial console.\n\n"
    "The Versal side is most likely not running the expected build. Reboot into "
    "Buddy and re-install this application / re-program the Versal image, then "
    "power-cycle and try again."
)

_BOOTING_REMEDIATION = (
    "The Versal serial console was still active and never went idle.\n\n"
    "Give the board more time to finish booting and re-run the checks. If it "
    "never settles it may be stuck in a boot loop or halted at the U-Boot "
    "prompt — power-cycle the board and try again."
)

# Default interesting scalar fields to surface from /etc/buildinfo.
_DEFAULT_FIELDS = {
    "DISTRO_VERSION": "Distro version",
    "DATETIME": "Build datetime",
}


def parse_buildinfo(text: str) -> tuple[dict[str, str], dict[str, str]]:
    """Parse ``/etc/buildinfo`` into (scalars, layers).

    * scalars: top ``KEY = VALUE`` build-configuration entries.
    * layers:  ``layer = branch:sha`` revision entries (value kept verbatim).
    """
    scalars: dict[str, str] = {}
    layers: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or "=" not in line or line.startswith("-"):
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not key:
            continue
        # Layer revisions look like "branch:<40-hex-sha>".
        if ":" in value and len(value.rsplit(":", 1)[-1]) >= 12:
            layers[key] = value
        else:
            scalars[key] = value
    return scalars, layers


def short_sha(branch_sha: str, length: int = 7) -> str:
    """Turn ``main:63bcd18ea9...`` into ``main:63bcd18``."""
    branch, _, sha = branch_sha.partition(":")
    if not sha:
        return branch_sha
    return f"{branch}:{sha[:length]}"


class VersalSerialCheck(Check):
    key = "versal_serial"
    title = "Versal serial console"
    critical = True

    def run(self) -> CheckResult:
        device = str(self.config.get("device", "/dev/ttyUSB1"))
        baud = int(self.config.get("baud", 115200))
        timeout_s = float(self.config.get("timeout_s", 10))
        nudge_interval_s = float(self.config.get("nudge_interval_s", 2.5))
        settle_timeout_s = float(self.config.get("settle_timeout_s", 120))
        boot_command = str(self.config.get("boot_command", "boot"))

        model_path = str(self.config.get("model_path", "/proc/device-tree/model"))
        expect_model = str(self.config.get("expect_model", "RAVE2 ECP - AMD Versal Gen 2"))
        buildinfo_path = str(self.config.get("buildinfo_path", "/etc/buildinfo"))
        expect_image = str(self.config.get("expect_image_basename", "erica-appdev-image"))
        report_layers = list(self.config.get("report_layers",
                                             ["meta-erica-edf", "meta-erica-h-hardware"]))
        remediation = str(self.config.get("remediation", _REMEDIATION))

        try:
            with SerialConsole(device, baud, command_timeout=timeout_s,
                               nudge_interval_s=nudge_interval_s,
                               settle_timeout_s=settle_timeout_s,
                               boot_command=boot_command) as con:
                # The board boots firmware -> U-Boot -> Linux. wait_for_idle
                # actively drives it through U-Boot's autoboot/menu (pressing
                # Enter, or typing `boot` at the U-Boot prompt) and returns the
                # instant a Linux shell prompt is ready, so we neither stall the
                # boot nor wait a fixed settle time.
                if not con.wait_for_idle():
                    return self.fail(
                        "Versal still booting (serial console not idle)",
                        detail=(
                            f"{device} never reached a Linux shell prompt within "
                            f"{settle_timeout_s:.0f}s; the board may be in a boot "
                            f"loop or stopped at the U-Boot prompt."
                        ),
                        remediation=_BOOTING_REMEDIATION,
                    )
                # device-tree model is NUL-padded; strip it.
                model = con.run(f"cat {model_path}").replace("\x00", "").strip()
                buildinfo = con.run(f"cat {buildinfo_path}")
        except SerialConsoleError as exc:
            return self.fail(
                f"Serial console unavailable on {device}",
                detail=str(exc),
                remediation=remediation,
            )

        scalars, layers = parse_buildinfo(buildinfo)
        image_basename = scalars.get("IMAGE_BASENAME", "")

        info: dict[str, str] = {
            "OSPI Configuration": model or "(empty)",
            "UFS filesystem": image_basename or "(unknown)",
        }
        for key, label in _DEFAULT_FIELDS.items():
            if key in scalars:
                info[label] = scalars[key]
        for layer in report_layers:
            if layer in layers:
                info[layer] = short_sha(layers[layer])

        # Evaluate the two pass/fail conditions.
        problems: list[str] = []
        if model != expect_model:
            problems.append(f"OSPI model {model!r} != expected {expect_model!r}")
        if image_basename != expect_image:
            problems.append(
                f"UFS IMAGE_BASENAME {image_basename!r} != expected {expect_image!r}")

        if problems:
            return self.fail(
                "Versal image does not match expected configuration",
                detail="\n".join(problems),
                remediation=remediation,
                info=info,
            )

        return self.ok(f"{model} / {image_basename}", info=info)

